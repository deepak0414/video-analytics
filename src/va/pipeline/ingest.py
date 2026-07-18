"""Ingest pipeline: URL/path -> dedup -> fetch -> sample -> embed -> vector store.

Idempotent: a source already at ingest_status='done' is skipped. Tags every
frame vector with its video_id + timestamp so query results map back to the
source moment.
"""
from __future__ import annotations

import shutil
import traceback
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Iterable, Iterator

from va.contracts.segment import Segment
from va.contracts.video import IngestStatus, Video
from va.pipeline.diarize import assign_speakers
from va.pipeline.text_index import index_text
from va.media.frames import keyframes_for_spans, sample_frames
from va.pipeline.paths import Workspace
from va.runtime.trace import current_run_id, trace, traced_run
from va.registry import (
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
from va.sources.base import resolve_source
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


def _trace_fail(role: str, exc: Exception) -> None:
    """Surface a swallowed best-effort role failure in the trace (a warn, not an
    abort — ingest continues). No-op when tracing is off. Before TR.2 these
    `except` blocks vanished silently; now a degraded ingest is visible."""
    trace(role, "failed", f"{type(exc).__name__}: {exc}", level="warn",
          traceback=traceback.format_exc())


def ingest(uri: str, workdir: str = ".va", fps: float = 1.0) -> IngestResult:
    """Ingest a source. The body runs inside a VA_TRACE-gated trace run (no-op when
    off); each best-effort role logs success or a visible warn on failure, and the
    decode pass emits its event."""
    ws = Workspace(workdir)
    with traced_run("ingest", workdir):
        return _ingest_impl(uri, ws, fps)


def _ingest_impl(uri: str, ws: Workspace, fps: float) -> IngestResult:
    catalog = Catalog(ws.catalog_db)
    try:
        source = resolve_source(uri)
        resolved = source.resolve(uri)  # cheap; gives source_key for dedup
        video, created = catalog.get_or_create(resolved)

        if video.ingest_status is IngestStatus.done:
            trace("ingest", "deduped", f"{resolved.source_key} already done")
            return IngestResult(video=video, deduped=True, frames_indexed=0)

        try:
            catalog.set_status(video.id, IngestStatus.fetching)
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

            # Role 1: scene boundaries -> the segments table (temporal backbone).
            spans = get_scene_detector().detect(fetched.local_path)
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
            try:
                captioner = get_vlm_captioner()
                keyframes = keyframes_for_spans(fetched.local_path, spans, per_segment=1)
                for seg, kf in zip(segments, keyframes):
                    seg_store.set_caption(seg.id, captioner.caption(kf))
                    captioned += 1
                trace("caption", "done", f"{captioned}/{len(segments)} segments captioned")
            except Exception as e:
                captioned = 0
                _trace_fail("caption", e)
            seg_store.close()

            # Role 8: speech-to-text -> transcripts (recommended, best-effort:
            # a transcription failure must not abort the whole ingest).
            # Role 9: speaker diarization labels those lines (best-effort: a
            # diarization failure must not lose the transcript).
            transcript_lines = 0
            n_speakers = 0
            try:
                lines = get_speech_to_text().transcribe(fetched.local_path)
                if lines:
                    try:
                        turns = get_speaker_diarizer().diarize(fetched.local_path)
                        if turns:
                            lines = assign_speakers(lines, turns)
                            n_speakers = len({ln.speaker for ln in lines if ln.speaker})
                    except Exception as e:
                        n_speakers = 0
                        _trace_fail("diarize", e)
                tx_store = TranscriptStore(ws.catalog_db)
                tx_store.replace_transcripts(video.id, lines)
                tx_store.close()
                transcript_lines = len(lines)
                trace("transcript", "done",
                      f"{transcript_lines} lines, {n_speakers} speakers")
            except Exception as e:
                transcript_lines = 0
                _trace_fail("transcript", e)

            # Role 10: on-screen text -> ocr_results (optional, best-effort).
            ocr_lines = 0
            try:
                lines = get_ocr_reader().read(fetched.local_path)
                ocr_store = OcrStore(ws.catalog_db)
                ocr_store.replace_lines(video.id, lines)
                ocr_store.close()
                ocr_lines = len(lines)
                trace("ocr", "done", f"{ocr_lines} lines")
            except Exception as e:
                ocr_lines = 0
                _trace_fail("ocr", e)

            # Role 7: action recognition per Role-1 segment (optional, best-effort).
            n_actions = 0
            try:
                per_span = get_action_recognizer().recognize(
                    fetched.local_path, spans, get_ingest_actions()
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
                _trace_fail("action", exc)

            # Decode the file ONCE at the target fps and fan the single frame
            # stream out to BOTH Role 2 (visual embedding, critical) and Role 5
            # (object detection, best-effort) — previously two separate full decode
            # passes over the identical frames. Streaming per batch keeps memory to
            # one batch, not the whole video.
            embedder = get_visual_embedder()
            # per-video vector shard (layout v2): removal = delete the video dir
            store = NumpyFlatVectorStore(video_dir / "vectors")

            # Role 5 detector is optional; if it won't even load we still embed.
            detector = None
            classes = None
            try:
                detector = get_object_detector()
                classes = get_ingest_classes()
            except Exception as e:
                detector = None
                _trace_fail("detect", e)
            det_ok = detector is not None
            frames_dets: list[tuple[float, list]] = []

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
                        for ts, dets in zip(timestamps, per_image):
                            frames_dets.append((ts, dets))
                    except Exception as e:
                        det_ok = False
                        frames_dets = []
                        _trace_fail("detect", e)
            store.persist()
            trace("ingest", "decode",
                  f"{n} frames @ {fps}fps -> embedding + detection (single pass)",
                  frames=n, fps=fps, detection=det_ok)

            # Role 6: associate detections into persistent tracks, then store both
            # (best-effort). The tracker fills video_id/timestamp/track_id.
            n_detections = 0
            n_tracks = 0
            if det_ok and frames_dets:
                try:
                    result = get_object_tracker().track(video.id, frames_dets)
                    det_store = DetectionStore(ws.catalog_db)
                    det_store.replace_detections(video.id, result.detections)
                    det_store.close()
                    track_store = TrackStore(ws.catalog_db)
                    track_store.replace_tracks(video.id, result.tracks)
                    track_store.close()
                    n_detections = len(result.detections)
                    n_tracks = len(result.tracks)
                    trace("track", "done", f"{n_tracks} tracks, {n_detections} detections")
                except Exception as e:
                    n_detections = 0
                    n_tracks = 0
                    _trace_fail("track", e)

            # Retrieval Layer (SR.2): semantic text index over the caption /
            # transcript / OCR / action text (best-effort — needs those rows
            # already written above, which they are).
            n_text = 0
            try:
                n_text = index_text(video.id, video_dir, ws.catalog_db)
                trace("text_index", "done", f"{n_text} text vectors")
            except Exception as e:
                n_text = 0
                _trace_fail("text_index", e)

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
            raise
    finally:
        catalog.close()
