"""Ingest pipeline: URL/path -> dedup -> fetch -> sample -> embed -> vector store.

Idempotent: a source already at ingest_status='done' is skipped. Tags every
frame vector with its video_id + timestamp so query results map back to the
source moment.
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
import traceback
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Iterable, Iterator

from va.contracts.segment import Segment
from va.contracts.video import IngestStatus, Video
from va.pipeline.diarize import assign_speakers
from va.configuration import default_footage_profile, load_config
from va.pipeline.text_index import index_text
from va.media.frames import keyframes_for_spans, sample_frames
from va.pipeline.paths import Workspace
from va.runtime.trace import current_run_id, trace, traced_run
from va.registry import (
    embedder_id,
    get_action_recognizer,
    get_ingest_actions,
    get_ingest_classes,
    get_object_detector,
    get_object_tracker,
    get_ocr_reader,
    get_scene_detector,
    get_speaker_diarizer,
    get_speech_to_text,
    get_visual_embedder,
    get_vlm_captioner,
)
from va.provenance import PROVENANCE_ROLES
from va.roles.scene_detector import SceneContext
from va.sources.base import resolve_source
from va.storage.structured.cameras import CameraStore
from va.storage.structured.catalog_sqlite import Catalog
from va.storage.structured.actions import ActionStore
from va.storage.structured.detections import DetectionStore
from va.storage.structured.ocr import OcrStore
from va.storage.structured.segments import SegmentStore
from va.storage.structured.tracks import TrackStore
from va.storage.structured.transcripts import TranscriptStore
from va.storage.vector.numpy_flat import NumpyFlatVectorStore

_BATCH = 32


@dataclass
class IngestResult:
    video: Video
    deduped: bool
    frames_indexed: int
    segments: int = 0
    captioned_segments: int = 0
    transcript_lines: int = 0
    speakers: int = 0
    detections: int = 0
    tracks: int = 0
    ocr_lines: int = 0
    action_events: int = 0
    text_vectors: int = 0


def _batched(it: Iterable, n: int) -> Iterator[list]:
    it = iter(it)
    while batch := list(islice(it, n)):
        yield batch


def _crop_key(ts: float, det) -> tuple:
    """Identity of a detection's geometry at a moment. Valid because BOTH
    tracker adapters return the ORIGINAL detections via model_copy — the iou
    tracker naturally, bytetrack via a supervision `data` index round-trip —
    so geometry is bit-identical by construction (invariant pinned by
    tests/test_tracker_passthrough.py; a tracker that reconstructs boxes
    breaks this join and silently drops appearance refs)."""
    return (round(ts, 3), round(det.bbox_x, 5), round(det.bbox_y, 5),
            round(det.bbox_w, 5), round(det.bbox_h, 5))


def _capture_appearance(tracks, detections, det_crops, embedder, cfg, video_dir):
    """WS4.d: one appearance embedding per track (its highest-confidence
    detection's crop, embedded with the Role-2 embedder) into the per-video
    `appearance` vector store — a SEPARATE file from the frame store, so scene
    search is untouched. Returns the tracks with `appearance_ref` set where a
    crop was available. Role-12 (cross-camera ReID) will replace the embedder
    with a purpose-trained one; this slice locks the schema + plumbing.
    """
    from va.registry import embedder_id

    if not tracks:
        return tracks
    by_track: dict = {}
    for d in detections:
        if d.track_id is None:
            continue
        best = by_track.get(d.track_id)
        if best is None or d.confidence > best.confidence:
            by_track[d.track_id] = d
    from PIL import Image

    todo, ref_by_track = [], {}
    for t in tracks:
        d = by_track.get(t.id)
        path = det_crops.get(_crop_key(d.timestamp, d)) if d is not None else None
        if path is None:
            continue
        ref = f"track:{t.id}"
        todo.append((path, {
            "appearance_ref": ref, "video_id": str(t.video_id),
            "track_id": str(t.id), "timestamp": d.timestamp,
            "object_class": t.object_class,
        }))
        ref_by_track[t.id] = ref
    if not todo:
        return tracks
    store = NumpyFlatVectorStore(video_dir / "appearance")
    # Batched like the frame path: an iou over-count on a long video can mean
    # hundreds of tracks, and a single forward pass over all crops would spike
    # VRAM on the already-OOM-prone box (§8.1). Crops are opened per batch.
    for chunk in _batched(todo, _BATCH):
        images = [Image.open(p).convert("RGB") for p, _ in chunk]
        store.add(embedder.embed_image(images), [pl for _, pl in chunk])
    store.set_meta({"embedder": embedder_id("visual_embedder", cfg),
                    "space": "appearance-crop"})
    store.persist()
    trace("appearance", "done", f"{len(todo)}/{len(tracks)} tracks embedded")
    return [
        t.model_copy(update={"appearance_ref": ref_by_track[t.id]})
        if t.id in ref_by_track else t
        for t in tracks
    ]


def _stage_models(cfg) -> None:
    """Staged model execution (WS4.e, the §8.1 fix): on a constrained profile
    (`residency: unload-after-use` in the hardware profile) drop the finished
    role group's models before the next group loads. Holding every real model
    resident at once silently starves late roles — measured on the Spark:
    YOLO produced detections on 1/22 clips in a single-process batch ingest,
    with no error anywhere. `residency: keep` (the default) is a no-op, so the
    resident-friendly path is byte-identical. Group boundaries are placed so
    models that must coexist (SigLIP + YOLO share the decode pass; the
    appearance step reuses SigLIP) sit inside one group."""
    if (cfg.profile.get("residency") or "keep") == "unload-after-use":
        from va.runtime.manager import MANAGER

        n = len(MANAGER.loaded())
        MANAGER.clear()
        if n:
            trace("runtime", "staged", f"unloaded {n} model(s) at group boundary")


def _trace_fail(role: str, exc: Exception) -> None:
    """Surface a swallowed best-effort role failure in the trace (a warn, not an
    abort — ingest continues). No-op when tracing is off. Before TR.2 these
    `except` blocks vanished silently; now a degraded ingest is visible."""
    trace(role, "failed", f"{type(exc).__name__}: {exc}", level="warn",
          traceback=traceback.format_exc())


def ingest(
    uri: str, workdir: str = ".va", fps: float = 1.0, profile: str | None = None
) -> IngestResult:
    """Ingest a source. The body runs inside a VA_TRACE-gated trace run (no-op when
    off); each best-effort role logs success or a visible warn on failure, and the
    decode pass emits its event. `profile` selects the footage profile this video
    is ingested under (WS-2); default is derived from the source type."""
    ws = Workspace(workdir)
    with traced_run("ingest", workdir):
        return _ingest_impl(uri, ws, fps, profile)


def _record_provenance(ws: Workspace, video_id, fps: float, run_id, counts: dict,
                       roles, cfg) -> None:
    """Best-effort §6-b provenance (PROV-3): for each role that SUCCEEDED (`roles`),
    stamp the model/config fingerprint (`role_fingerprint`) + fps + how many rows it
    produced. Roles whose best-effort step FAILED are deliberately omitted — an absent
    row reads as stale to `va stale` (PROV-4), so a transient failure gets reprocessed
    rather than masked as current provenance (the design's 'false stale OK, missed stale
    forbidden' rule). `cfg` is the config pinned at role-launch time (the caller's), so
    the stamp can't drift from what actually ran during a mid-ingest edit. Keyed by
    (video, role) so a reingest overwrites; never aborts the ingest — a failure here is
    traced and swallowed, like the roles themselves."""
    from va.provenance import role_fingerprint
    from va.storage.structured.provenance_store import ProvenanceStore

    try:
        store = ProvenanceStore(ws.catalog_db)
        try:
            for role in roles:
                ident = role_fingerprint(role, cfg)
                store.record(video_id, role, ident["model"], ident["fingerprint"],
                             fps=fps, run_id=run_id, row_count=counts.get(role))
        finally:
            store.close()
    except Exception as e:  # noqa: BLE001 — provenance is best-effort, never fatal
        _trace_fail("provenance", e)


def _ingest_impl(
    uri: str, ws: Workspace, fps: float, profile: str | None = None
) -> IngestResult:
    catalog = Catalog(ws.catalog_db)
    try:
        source = resolve_source(uri)
        resolved = source.resolve(uri)  # cheap; gives source_key for dedup
        video, created = catalog.get_or_create(resolved)

        # An EXPLICIT profile name is user input: validate it BEFORE any work —
        # including the dedup return — so a typo fails loudly (load_config raises,
        # naming the missing file) instead of being swallowed by
        # "[already-ingested]".
        if profile is not None:
            load_config(footage_profile=profile)
        elif video.profile:
            # A retry of a not-yet-done row carries the recorded profile forward
            # (mirroring reingest_video) — otherwise retrying a failed
            # `--profile security` ingest silently reverts to the source default.
            # NOT validated up here: a broken carried profile must not break the
            # idempotent dedup no-op on a done row (the probe below still catches
            # it before roles run on a real retry).
            profile = video.profile

        if video.ingest_status is IngestStatus.done:
            trace("ingest", "deduped", f"{resolved.source_key} already done")
            return IngestResult(video=video, deduped=True, frames_indexed=0)

        # Resolve the profile to RECORD: it must match what the roles actually run
        # under — roles self-load config, which applies roles.yaml
        # `active_footage_profile` — so the probe's resolution (explicit arg >
        # active_footage_profile > generic) is what we record, with the
        # source-derived default only as the final fallback.
        probe = load_config(footage_profile=profile)
        footage_profile = probe.footage_profile
        if profile is None and footage_profile == "generic":
            footage_profile = default_footage_profile(resolved.source_type.value)

        try:
            catalog.set_status(video.id, IngestStatus.fetching)
            catalog.set_profile(video.id, footage_profile)

            # Chunk placement (WS4.c): a source that knows the camera + wall
            # clock declares it on ResolvedVideo; attach it to the row BEFORE
            # any fetch/role work so (a) Role 1's motion-episodes backend sees
            # start_epoch mid-ingest and (b) a hard kill leaves a pending row
            # that already carries its placement — a plain retry completes it.
            if resolved.camera is not None:
                cam_store = CameraStore(ws.catalog_db)
                try:
                    cam_store.get_or_create(resolved.camera)
                finally:
                    cam_store.close()
                catalog.set_camera(video.id, resolved.camera.id)
            if resolved.start_epoch is not None:
                catalog.set_start_epoch(video.id, resolved.start_epoch)
            fetched = source.fetch(resolved, ws.cache)
            catalog.update_metadata(video.id, fetched)

            # Layout v2: managed media (anything we downloaded into cache/) moves
            # into the per-video directory; user-owned files stay where they are.
            video_dir = ws.video_dir(
                video.source_key, fetched.metadata.title or video.title, create=True
            )
            local_path = fetched.local_path
            lp = Path(local_path).resolve()
            if ws.cache.resolve() in lp.parents:
                dest = video_dir / ("media" + lp.suffix)
                if not dest.exists():
                    shutil.move(str(lp), str(dest))
                new_path = str(dest)
                # local-source videos' canonical input WAS that cache path
                new_uri = new_path if video.source_type.value == "local" else None
                catalog.set_paths(video.id, new_path, source_uri=new_uri)
                local_path = new_path
            fetched = fetched.model_copy(update={"local_path": local_path})
            trace("source", "fetched", fetched.metadata.title or resolved.source_key,
                  resolution=fetched.metadata.resolution,
                  duration_s=fetched.metadata.duration_seconds)

            catalog.set_status(
                video.id, IngestStatus.processing,
                local_path=local_path, mark_fetched=True,
            )

            # Roles whose best-effort step FAILED — omitted from the provenance stamp
            # below, so `va stale` reads them as stale and they get reprocessed rather
            # than masked as current (a transient failure must never read as up-to-date).
            failed: set[str] = set()

            # Pin the config ONCE here, at role-launch time, so the provenance stamp
            # reflects what the roles are about to run under — NOT a fresh load_config()
            # after ingest, which a mid-ingest roles.yaml edit would make stamp old-model
            # rows with the new fingerprint (a MISSED stale, the one thing §6-b forbids).
            # Pinning at the start instead degrades that race to a false stale (safe).
            # WS2.c: the pin carries the footage-profile overlay and is passed to every
            # role getter below, so the roles run under — and the stamp records — the
            # profile this ingest was invoked with.
            cfg = load_config(footage_profile=footage_profile)

            # Roles the footage profile disables for this input domain (e.g. the
            # security profile skips speech roles — no mic). Not stamped in
            # provenance: `va stale` reads them as stale, which is the safe
            # direction (false stale OK, missed stale forbidden).
            skipped: set[str] = set()

            def _purge(role: str, store_cls, method: str) -> None:
                # Purge a skipped role's prior-attempt rows, best-effort: the purge
                # is bookkeeping for a role that isn't even running — it must never
                # abort the ingest (e.g. a transient catalog.db lock).
                try:
                    s = store_cls(ws.catalog_db)
                    try:
                        getattr(s, method)(video.id, [])
                    finally:
                        s.close()
                except Exception as e:  # noqa: BLE001
                    _trace_fail(f"{role}-purge", e)

            def _enabled(role: str) -> bool:
                # A role absent from roles.yaml is ENABLED: every registry getter
                # tolerates that shape via its stub fallback, and the gate must not
                # be stricter than the getters it guards. For PRESENT roles, go
                # through cfg.role() so the flag is interpreted with the same
                # pydantic coercion stale/reprocess use — raw truthiness would read
                # a string 'false' as enabled while staleness reads it disabled,
                # a non-convergent stale loop.
                if role not in cfg.roles or cfg.role(role).enabled:
                    return True
                skipped.add(role)
                trace(role, "skipped", f"disabled by footage profile '{footage_profile}'")
                return False

            # Role 1: scene boundaries -> the segments table (temporal backbone).
            # Context (WS4.b): the chunk's wall-clock placement + camera ref, for
            # placement-aware backends (motion-episodes). Re-read the row — the
            # A-MCLSSRVF pull path attaches camera metadata to a pending row
            # before processing, and `video` predates fetch/metadata updates.
            ctx_row = catalog.get(video.id) or video
            camera_ref = None
            if ctx_row.camera_id:
                cam_store = CameraStore(ws.catalog_db)
                try:
                    cam = cam_store.get(ctx_row.camera_id)
                finally:
                    cam_store.close()
                camera_ref = cam.source_ref if cam else None
                if cam is None:
                    # FK is declared but unenforced (WS4.c gap): a dangling
                    # camera_id would otherwise SILENTLY widen the MotionSource
                    # query to all cameras (camera_ref=None) — warn like every
                    # other degraded mode in this path.
                    logging.getLogger(__name__).warning(
                        "video %s references missing camera %r — motion query "
                        "will not be camera-filtered",
                        video.id, ctx_row.camera_id,
                    )
            scene_ctx = SceneContext(
                start_epoch=ctx_row.start_epoch,
                camera_ref=camera_ref,
                duration_seconds=fetched.metadata.duration_seconds,
            )
            spans = get_scene_detector(cfg).detect(fetched.local_path, scene_ctx)
            segments = [
                Segment(video_id=video.id, segment_index=i, start_time=s, end_time=e)
                for i, (s, e) in enumerate(spans)
            ]
            seg_store = SegmentStore(ws.catalog_db)
            seg_store.replace_segments(video.id, segments)
            trace("scene", "segments", f"{len(segments)} segments")

            # Role 4: caption each segment from a keyframe (best-effort; the VLM
            # is heavy, and a failure must not abort the whole ingest).
            captioned = 0
            if _enabled("vlm_captioner"):
                try:
                    captioner = get_vlm_captioner(cfg)
                    keyframes = keyframes_for_spans(fetched.local_path, spans, per_segment=1)
                    for seg, kf in zip(segments, keyframes):
                        seg_store.set_caption(seg.id, captioner.caption(kf))
                        captioned += 1
                    trace("caption", "done", f"{captioned}/{len(segments)} segments captioned")
                except Exception as e:
                    captioned = 0
                    failed.add("vlm_captioner")
                    _trace_fail("caption", e)
                finally:
                    # The locals pin the weights: MANAGER.clear() alone would
                    # only drop the cache entry while `captioner` keeps the
                    # ~GBs alive for the rest of the ingest (review round 1).
                    captioner = None
                    keyframes = None
                    del captioner, keyframes
            seg_store.close()
            _stage_models(cfg)   # captioner group done

            # Role 8: speech-to-text -> transcripts (recommended, best-effort:
            # a transcription failure must not abort the whole ingest).
            # Role 9: speaker diarization labels those lines (best-effort: a
            # diarization failure must not lose the transcript).
            transcript_lines = 0
            n_speakers = 0
            if _enabled("speech_to_text"):
                # Evaluate the diarizer gate BEFORE knowing whether there are lines:
                # short-circuiting on `lines` first would leave a disabled diarizer
                # neither skipped nor failed on a silent video — and therefore
                # provenance-stamped, violating "skipped roles are never stamped".
                diarizer_on = _enabled("speaker_diarizer")
                try:
                    lines = get_speech_to_text(cfg).transcribe(fetched.local_path)
                    if lines and diarizer_on:
                        try:
                            turns = get_speaker_diarizer(cfg).diarize(fetched.local_path)
                            if turns:
                                lines = assign_speakers(lines, turns)
                                n_speakers = len({ln.speaker for ln in lines if ln.speaker})
                        except Exception as e:
                            n_speakers = 0
                            failed.add("speaker_diarizer")
                            _trace_fail("diarize", e)
                    tx_store = TranscriptStore(ws.catalog_db)
                    tx_store.replace_transcripts(video.id, lines)
                    tx_store.close()
                    transcript_lines = len(lines)
                    trace("transcript", "done",
                          f"{transcript_lines} lines, {n_speakers} speakers")
                except Exception as e:
                    transcript_lines = 0
                    failed.update({"speech_to_text", "speaker_diarizer"})  # diarize couldn't run
                    _trace_fail("transcript", e)
            else:
                # No transcript lines will exist, so the diarizer can't run either.
                skipped.add("speaker_diarizer")
                # Purge rows a prior (failed/other-profile) attempt may have written:
                # a skipped role must leave NO live rows, or the profile's promise
                # (e.g. "0 transcript rows under security") is broken by a retry.
                _purge("speech_to_text", TranscriptStore, "replace_transcripts")

            _stage_models(cfg)   # speech group done
            # Role 10: on-screen text -> ocr_results (optional, best-effort).
            ocr_lines = 0
            if not _enabled("ocr"):
                _purge("ocr", OcrStore, "replace_lines")  # prior-attempt rows (see STT)
            else:
                try:
                    lines = get_ocr_reader(cfg).read(fetched.local_path)
                    ocr_store = OcrStore(ws.catalog_db)
                    ocr_store.replace_lines(video.id, lines)
                    ocr_store.close()
                    ocr_lines = len(lines)
                    trace("ocr", "done", f"{ocr_lines} lines")
                except Exception as e:
                    ocr_lines = 0
                    failed.add("ocr")
                    _trace_fail("ocr", e)

            _stage_models(cfg)   # ocr group done
            # Role 7: action recognition per Role-1 segment (optional, best-effort).
            n_actions = 0
            if not _enabled("action_recognizer"):
                _purge("action_recognizer", ActionStore, "replace_events")
            else:
                try:
                    per_span = get_action_recognizer(cfg).recognize(
                        fetched.local_path, spans, get_ingest_actions(cfg)
                    )
                    events = []
                    for seg, seg_events in zip(segments, per_span):
                        for e in seg_events:
                            events.append(e.model_copy(update={
                                "video_id": video.id, "segment_id": seg.id,
                            }))
                    act_store = ActionStore(ws.catalog_db)
                    act_store.replace_events(video.id, events)
                    act_store.close()
                    n_actions = len(events)
                    trace("action", "done", f"{n_actions} events")
                except Exception as exc:
                    n_actions = 0
                    failed.add("action_recognizer")
                    _trace_fail("action", exc)

            _stage_models(cfg)   # action group done; SigLIP+YOLO group next
            # Decode the file ONCE at the target fps and fan the single frame
            # stream out to BOTH Role 2 (visual embedding, critical) and Role 5
            # (object detection, best-effort) — previously two separate full decode
            # passes over the identical frames. Streaming per batch keeps memory to
            # one batch, not the whole video.
            #
            # Resume safety (WS6.a review): a prior attempt may have PERSISTED a
            # partial shard (killed between store.persist() and `done`). The
            # vector stores load-and-APPEND, so re-running on the leftovers
            # would duplicate every frame/appearance vector. This row is not
            # `done` (the dedup return above filters those), so any existing
            # shard content is by definition a partial prior attempt — start
            # clean. Role tables don't need this: they use replace_* semantics.
            for stale in ("vectors", "appearance"):
                (video_dir / f"{stale}.npz").unlink(missing_ok=True)
                (video_dir / f"{stale}.json").unlink(missing_ok=True)
            # The deleted appearance shard may still be referenced by
            # prior-attempt track rows — and if the tracker fails on THIS run
            # (best-effort: old rows survive), those refs would dangle into a
            # nonexistent store, breaking the WS4.d invariant (round-7
            # review). Null them; a successful tracker run rewrites them.
            try:
                conn = sqlite3.connect(ws.catalog_db)
                conn.execute(
                    "UPDATE object_tracks SET appearance_ref = NULL "
                    "WHERE video_id = ?", (str(video.id),))
                conn.commit()
                conn.close()
            except Exception as e:  # noqa: BLE001 — bookkeeping, never fatal
                _trace_fail("appearance-ref-reset", e)
            embedder = get_visual_embedder(cfg)
            # per-video vector shard (layout v2): removal = delete the video dir
            store = NumpyFlatVectorStore(video_dir / "vectors")

            # Role 5 detector is optional; if it won't even load we still embed.
            detector = None
            classes = None
            if _enabled("object_detector"):
                try:
                    detector = get_object_detector(cfg)
                    classes = get_ingest_classes(cfg)
                except Exception as e:
                    detector = None
                    failed.update({"object_detector", "object_tracker"})
                    _trace_fail("detect", e)
            else:
                # No detections means the tracker has nothing to associate.
                skipped.add("object_tracker")
                _purge("object_detector", DetectionStore, "replace_detections")
                _purge("object_tracker", TrackStore, "replace_tracks")
            det_ok = detector is not None
            frames_dets: list[tuple[float, list]] = []
            # WS4.d: detection crops harvested DURING the single decode pass
            # (frames aren't retained, so cropping later would mean a second
            # decode). Keyed by (timestamp, bbox) — both trackers return the
            # ORIGINAL detections (model_copy; bytetrack routes them through
            # supervision's `data` index), so geometry is identical by
            # construction and the key survives into Role 6's output. Crops
            # SPILL TO DISK (transient cache dir, downscaled JPEGs): an
            # hour-long many-object video would otherwise hold GBs of PIL
            # images in RAM next to the resident models (§8.1).
            det_crops: dict[tuple, Path] = {}
            crops_dir = ws.cache / f"crops-{video.id}"

            n = 0
            for batch in _batched(sample_frames(fetched.local_path, fps=fps), _BATCH):
                timestamps = [t for t, _ in batch]
                images = [img for _, img in batch]
                # Role 2: visual embedding (critical — a failure aborts the ingest)
                vecs = embedder.embed_image(images)
                payloads = [
                    {"video_id": str(video.id), "timestamp": ts,
                     "source_uri": fetched.source_uri}
                    for ts in timestamps
                ]
                store.add(vecs, payloads)
                n += len(batch)
                # Role 5: object detection (best-effort — guarded so it can never
                # break the critical embedding above)
                if det_ok:
                    try:
                        per_image = detector.detect(images, classes)
                        for (ts, img), dets in zip(batch, per_image):
                            frames_dets.append((ts, dets))
                            for d in dets:
                                w, h = img.size
                                box = (int(d.bbox_x * w), int(d.bbox_y * h),
                                       int((d.bbox_x + d.bbox_w) * w),
                                       int((d.bbox_y + d.bbox_h) * h))
                                if box[2] > box[0] and box[3] > box[1]:
                                    crop = img.crop(box)
                                    crop.thumbnail((512, 512))  # embedders resize anyway
                                    crops_dir.mkdir(parents=True, exist_ok=True)
                                    p = crops_dir / f"c{len(det_crops)}.jpg"
                                    crop.convert("RGB").save(p, "JPEG", quality=90)
                                    det_crops[_crop_key(ts, d)] = p
                    except Exception as e:
                        det_ok = False
                        frames_dets = []
                        det_crops = {}
                        failed.update({"object_detector", "object_tracker"})
                        _trace_fail("detect", e)
            store.set_meta({"embedder": embedder_id("visual_embedder", cfg)})
            store.persist()
            trace("ingest", "decode",
                  f"{n} frames @ {fps}fps -> embedding + detection (single pass)",
                  frames=n, fps=fps, detection=det_ok)

            # Role 6: associate detections into persistent tracks, then store both
            # (best-effort). The tracker fills video_id/timestamp/track_id.
            n_detections = 0
            n_tracks = 0
            # Evaluate the tracker gate UNCONDITIONALLY (like diarizer_on): with
            # zero decoded frames the branches below never run, and a disabled
            # tracker that never registered as skipped would be provenance-stamped
            # — permanently stale under the stamped-and-disabled rule.
            tracker_on = _enabled("object_tracker")
            if not tracker_on:
                # Hoisted out of the det_ok branch: the no-live-rows invariant for
                # a disabled role must hold even when the detector failed to load,
                # died mid-decode, or decoded zero frames.
                _purge("object_tracker", TrackStore, "replace_tracks")
            if det_ok and frames_dets and not tracker_on:
                # Tracker disabled by the profile: keep the detector's output
                # (untracked, track_id NULL) and purge any prior-attempt tracks.
                try:
                    flat = [
                        d.model_copy(update={"video_id": video.id, "timestamp": ts})
                        for ts, dets in frames_dets for d in dets
                    ]
                    det_store = DetectionStore(ws.catalog_db)
                    det_store.replace_detections(video.id, flat)
                    det_store.close()
                    n_detections = len(flat)
                    trace("track", "skipped-stored-untracked", f"{n_detections} detections")
                except Exception as e:  # noqa: BLE001 — best-effort, like the enabled path
                    n_detections = 0
                    failed.add("object_detector")
                    _trace_fail("detect", e)
            elif det_ok and frames_dets:
                try:
                    result = get_object_tracker(cfg).track(video.id, frames_dets)
                    det_store = DetectionStore(ws.catalog_db)
                    det_store.replace_detections(video.id, result.detections)
                    det_store.close()
                    # WS4.d (Role-12 schema insurance): embed each track's
                    # best-detection crop into a SECOND per-video vector store
                    # and point the track row at it. Best-effort — appearance
                    # capture failing must cost the refs, never the tracks.
                    tracks = result.tracks
                    try:
                        tracks = _capture_appearance(
                            tracks, result.detections, det_crops,
                            embedder, cfg, video_dir,
                        )
                    except Exception as e:  # noqa: BLE001
                        _trace_fail("appearance", e)
                    track_store = TrackStore(ws.catalog_db)
                    track_store.replace_tracks(video.id, tracks)
                    track_store.close()
                    n_detections = len(result.detections)
                    n_tracks = len(result.tracks)
                    trace("track", "done", f"{n_tracks} tracks, {n_detections} detections")
                except Exception as e:
                    n_detections = 0
                    n_tracks = 0
                    failed.update({"object_detector", "object_tracker"})
                    _trace_fail("track", e)
            # The spilled crops are consumed (or moot) once Role 6 is done.
            shutil.rmtree(crops_dir, ignore_errors=True)
            # Same local-reference rule as the captioner: release the adapters
            # or the boundary below frees nothing.
            embedder = None
            detector = None
            del embedder, detector
            _stage_models(cfg)   # embed/detect/track/appearance group done

            # Retrieval Layer (SR.2): semantic text index over the caption /
            # transcript / OCR / action text (best-effort — needs those rows
            # already written above, which they are).
            n_text = 0
            try:
                n_text = index_text(video.id, video_dir, ws.catalog_db, cfg=cfg)
                trace("text_index", "done", f"{n_text} text vectors")
            except Exception as e:
                n_text = 0
                failed.add("text_embedder")
                _trace_fail("text_index", e)
            _stage_models(cfg)   # text-index group done (last model group)

            # §6-b (PROV-3): record which model/config produced each role's rows so a
            # later `va stale` can find videos on an outdated model after an upgrade.
            _record_provenance(
                ws, video.id, fps, current_run_id(),
                counts={
                    "scene_detector": len(segments),
                    "visual_embedder": n,
                    "vlm_captioner": captioned,
                    "object_detector": n_detections,
                    "object_tracker": n_tracks,
                    "action_recognizer": n_actions,
                    "speech_to_text": transcript_lines,
                    "speaker_diarizer": n_speakers,
                    "ocr": ocr_lines,
                    "text_embedder": n_text,
                },
                roles=[r for r in PROVENANCE_ROLES if r not in failed and r not in skipped],
                cfg=cfg,
            )
            # Stamp the video with this ingest's trace run_id (None when tracing is
            # off) so a later query/ask trace can point back at the ingest that
            # produced its data — and any degradations that ingest recorded.
            catalog.set_status(video.id, IngestStatus.done, mark_processed=True,
                               ingest_run_id=current_run_id())
            trace("ingest", "done",
                  f"{n} frames, {len(segments)} segments, {captioned} caps, "
                  f"{transcript_lines} tx, {n_detections} det, {n_tracks} tracks, "
                  f"{ocr_lines} ocr, {n_actions} actions, {n_text} text-vecs")
            return IngestResult(
                video=catalog.get(video.id), deduped=False,
                frames_indexed=n, segments=len(segments),
                captioned_segments=captioned, transcript_lines=transcript_lines,
                speakers=n_speakers,
                detections=n_detections, tracks=n_tracks, ocr_lines=ocr_lines,
                action_events=n_actions, text_vectors=n_text,
            )
        except Exception as e:  # noqa: BLE001 - record failure, then re-raise
            catalog.set_status(video.id, IngestStatus.failed, error=str(e))
            # A failed ingest must not leave every model resident into the next
            # clip of a single-process batch (review round 1). Best-effort: the
            # original exception is what matters.
            try:
                _stage_models(cfg)
            except Exception:  # noqa: BLE001
                pass
            raise
    finally:
        catalog.close()
