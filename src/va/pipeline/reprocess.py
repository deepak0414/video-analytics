"""Batch reprocess — selection (RPRC-3) + execution (RPRC-1a) (WS-1 §6-b, pillar B).

`plan_reprocess` resolves WHICH (video, role) pairs a reprocess would touch — the stale
set from PROV-4 (`stale_report`), scoped by role/video/all-stale — WITHOUT mutating.
`execute_reprocess` then re-runs each stale role that has an in-place reprocessor and
restamps its provenance. `text_embedder`, `visual_embedder`, and `vlm_captioner` are wired
so far (`reprocessable_roles()`); every other stale role is left for whole-video `va reingest`
(D5 scope cap). The CLI runs the plan on `--dry-run` and executes otherwise. Dependency-aware
(RPRC-2, `_SATISFIES`): when a provider's reprocess also rebuilds a dependent's artifact (re-
captioning rebuilds the text index), the dependent is restamped without a redundant rebuild.
"""
from __future__ import annotations

from typing import Any, Optional

from va.configuration import (
    GATE_DEPENDENTS,
    GATEABLE_ROLES,
    config_for,
    default_footage_profile,
    load_config,
)
from va.contracts.video import IngestStatus
from va.pipeline.manage import lookup_video
from va.pipeline.paths import Workspace
from va.pipeline.stale import stale_report
from va.storage.structured.catalog_sqlite import Catalog


def plan_reprocess(
    workdir: str,
    *,
    role: Optional[str] = None,
    all_stale: bool = False,
    video: Optional[str] = None,
) -> list[dict[str, Any]]:
    """The stale (video, role) work set a reprocess WOULD run, scoped by the flags.
    Read-only: it computes the selection, it never mutates.

    Exactly one video scope is required — `all_stale` (every stale video) XOR `video`
    (one video by UUID / source_key / URL / path) — so a reprocess can never fan out
    across the whole corpus by omission. `role` restricts to a single stamped role
    (validated by `stale_report` against `PROVENANCE_ROLES`).

    Returns `stale_report` rows (video_id, source_uri, title, stale_roles, recorded_fps)
    filtered to the scope; empty when nothing in scope is stale (i.e. already current).
    Raises ValueError on a bad scope, an unknown role, or an unknown `--video` ident.
    """
    if all_stale == bool(video):
        raise ValueError(
            "specify exactly one video scope: all_stale=True (every stale video) "
            "or video=<UUID|source_key|URL|path>")

    report = stale_report(workdir, role=role)  # done-only; per-role staleness; role validated
    if video is None:
        return report

    # Resolve --video the same way `va remove`/`reingest` resolve an ident, then keep only
    # that video's row (empty if it is already current, i.e. not in the stale report).
    cat = Catalog(Workspace(workdir).catalog_db)
    try:
        target = lookup_video(cat, video)
    finally:
        cat.close()
    if target is None:
        raise ValueError(f"no such video: {video!r} (expected a UUID, source_key, URL, or path)")
    if target.ingest_status is not IngestStatus.done:
        # stale_report done-filters, so a non-done target yields an EMPTY plan that reads as
        # "already current" — misleading for a video the user explicitly named. An incomplete
        # ingest needs re-ingest (there are no rows to reprocess), so say so instead of nothing.
        raise ValueError(
            f"video {video!r} ingest is not complete (status={target.ingest_status.value}) — "
            f"it needs `va reingest`, not a role reprocess")
    return [e for e in report if e["video_id"] == str(target.id)]


# --- Execution (RPRC-1) --------------------------------------------------------------
# A per-role reprocessor re-runs ONE role for one already-ingested video IN PLACE and
# returns the row/vector count it produced. Only roles with standalone re-run code are
# wired (D5 scope cap); every other stale role falls back to whole-video `va reingest`.


_VISUAL_BATCH = 32  # frames per embed call (matches ingest's batch)


def _batched(it, n):
    batch = []
    for x in it:
        batch.append(x)
        if len(batch) == n:
            yield batch
            batch = []
    if batch:
        yield batch


def _reprocess_text_embedder(workdir: str, video_id: str) -> int:
    from va.pipeline.text_index import backfill_text_index

    n = backfill_text_index(workdir, video_id)  # rebuilds + re-tags the text_vectors shard
    if n is None:
        # backfill returns None when the ident no longer resolves (video removed between
        # plan and execute, or a stale plan): NOT a 0-row rebuild. Raise so the executor
        # routes it to `failed` and never restamps provenance for a rebuild that didn't run.
        raise ValueError(f"video {video_id} not found — cannot reprocess text_embedder")
    return n


def reindex_visual(video, video_dir, fps: float, catalog_db=None, embedder=None,
                   cfg=None) -> int:
    """(Re)build the visual `vectors` shard: re-sample frames at `fps` and re-embed.

    Builds to a TEMP shard and swaps it in only on full success, so a failed re-embed (e.g.
    a GPU OOM on a model switch) leaves the prior shard — and visual search — intact. Unlike
    ingest's single decode pass, this embeds ONLY (no Role-5 detection), so it is a standalone
    re-embed rather than an extraction of that combined loop. `fps` MUST match the density the
    video was ingested at (the caller preserves the recorded fps), else the re-embedded frame
    set drifts. Returns frames embedded."""
    from pathlib import Path

    from va.media.frames import sample_frames
    from va.registry import embedder_id, get_visual_embedder
    from va.storage.vector.numpy_flat import NumpyFlatVectorStore, swap_shard

    if embedder is None:
        # cfg: the footage-profile-overlaid config the video runs under (WS2.c) —
        # build AND tag from the same config or the TAG-3 guard drops the shard.
        embedder = get_visual_embedder(cfg)
        tag = embedder_id("visual_embedder", cfg)
    else:  # injected embedder must declare its own id, else tag "unknown" (TAG-3 skips it)
        tag = getattr(embedder, "model_id", None) or "unknown"

    vdir = Path(video_dir)
    tmp = vdir / "vectors_rebuild"          # NB: no dot — with_suffix() would rewrite it
    for suf in (".npz", ".json"):           # clear any temp left by a prior crash
        p = tmp.with_suffix(suf)
        if p.exists():
            p.unlink()
    store = NumpyFlatVectorStore(tmp)
    n = 0
    for batch in _batched(sample_frames(video.local_path, fps=fps), _VISUAL_BATCH):
        images = [img for _, img in batch]
        vecs = embedder.embed_image(images)
        store.add(vecs, [{"video_id": str(video.id), "timestamp": ts,
                          "source_uri": video.source_uri} for ts, _ in batch])
        n += len(batch)
    if n == 0:
        # Zero frames from a video that previously had them means the decode broke silently
        # (truncated/corrupt media that opens but iterates nothing). Swapping in an empty shard
        # would vanish the video from visual search while `va stale` reads clean — fail instead,
        # leaving the existing shard so the role stays stale for retry.
        raise ValueError(
            f"video {video.id} re-embed produced 0 frames — media may be corrupt; "
            f"not replacing the existing shard")
    store.set_meta({"embedder": tag})
    store.persist()
    if catalog_db is not None:
        # A concurrent `va remove` during the (long) re-embed deletes the catalog row + dir;
        # persist() just mkdir'd the dir back. Re-check RIGHT BEFORE the swap — swapping now
        # would resurrect the removed video in visual search. If it's gone, drop the temp and
        # fail (role stays stale, no restamp), leaving no `vectors.npz` behind.
        cat = Catalog(catalog_db)
        try:
            still_present = cat.get(video.id) is not None
        finally:
            cat.close()
        if not still_present:
            for suf in (".npz", ".json"):
                p = tmp.with_suffix(suf)
                if p.exists():
                    p.unlink()
            raise ValueError(f"video {video.id} was removed during reprocess — aborting re-embed")
    swap_shard(tmp, vdir / "vectors")  # swap in only now that the rebuild fully succeeded
    return n


def _reprocess_visual_embedder(workdir: str, video_id: str) -> int:
    from pathlib import Path

    from va.pipeline.paths import Workspace
    from va.storage.structured.provenance_store import ProvenanceStore

    ws = Workspace(workdir)
    cat = Catalog(ws.catalog_db)
    try:
        video = lookup_video(cat, video_id)
    finally:
        cat.close()
    if video is None:
        raise ValueError(f"video {video_id} not found — cannot reprocess visual_embedder")
    if not video.local_path or not Path(video.local_path).exists():
        raise ValueError(
            f"video {video_id} media is not available locally ({video.local_path!r}) — "
            f"cannot re-embed; use `va reingest` (re-downloads / re-reads the source)")
    # Visual embedding DEPENDS on the sampling fps (which frames are seen). Re-embed at the
    # SAME density it was ingested at, from the recorded provenance; an unknown fps can't be
    # preserved, so refuse rather than silently change the density (Roles 2/5/6/7 rely on it).
    pv = ProvenanceStore(ws.catalog_db)
    try:
        prev = pv.get(video_id, "visual_embedder")
    finally:
        pv.close()
    fps = prev[0]["fps"] if prev else None
    if fps is None:
        raise ValueError(
            f"video {video_id} recorded visual fps is unknown — density can't be preserved; "
            f"use `va reingest {video_id} --fps <N>`")
    return reindex_visual(video, ws.video_dir(video.source_key, video.title), fps,
                          catalog_db=ws.catalog_db,
                          cfg=config_for(video.profile, video.source_type.value))


def _reprocess_vlm_captioner(workdir: str, video_id: str) -> int:
    from pathlib import Path

    from va.media.frames import keyframes_for_spans
    from va.pipeline.paths import Workspace
    from va.pipeline.text_index import backfill_text_index
    from va.registry import get_vlm_captioner
    from va.storage.structured.observations import ObservationStore
    from va.storage.structured.segments import SegmentStore

    ws = Workspace(workdir)
    cat = Catalog(ws.catalog_db)
    try:
        video = lookup_video(cat, video_id)
    finally:
        cat.close()
    if video is None:
        raise ValueError(f"video {video_id} not found — cannot reprocess vlm_captioner")
    if not video.local_path or not Path(video.local_path).exists():
        raise ValueError(
            f"video {video_id} media is not available locally ({video.local_path!r}) — "
            f"cannot re-caption; use `va reingest`")

    seg_store = SegmentStore(ws.catalog_db)
    try:
        segments = seg_store.get_segments(video.id)
        spans = [(s.start_time, s.end_time) for s in segments]
        keyframes = keyframes_for_spans(video.local_path, spans, per_segment=1)
        # Re-caption under the video's recorded footage profile (WS2.c).
        captioner = get_vlm_captioner(config_for(video.profile, video.source_type.value))
        # Caption ALL segments first, THEN write — so a captioner failure mid-run leaves the
        # existing captions intact (nothing half-written), the same durability as the shard
        # rebuilds. On failure the role stays stale (not restamped) and retries cleanly.
        new_caps = [(s.id, captioner.caption(kf)) for s, kf in zip(segments, keyframes)]
        for seg_id, cap in new_caps:
            seg_store.set_caption(seg_id, cap)
    finally:
        seg_store.close()

    # Captions feed two dependents — propagate to both so the new captions actually surface
    # (skipping this would leave text search + `va ask` on the OLD captions after a re-caption):
    #  - the text index (captions are one of its modalities) -> rebuild it in place
    #  - the deep-scan `observations` cache (its sweep uses the captioner) -> purge it so the
    #    next `va ask` re-sweeps instead of serving old-model micro-captions (D6).
    if backfill_text_index(workdir, video_id) is None:
        # the video was removed mid-run (a concurrent `va remove`): its segments/shard are gone
        # and backfill's lookup returned None. Do NOT restamp a purged video — raise so the role
        # routes to `failed` and stays stale (matches _reprocess_text_embedder's None-check).
        raise ValueError(f"video {video_id} not found — removed during reprocess")
    obs = ObservationStore(ws.catalog_db)
    try:
        obs.purge(video.id)
    finally:
        obs.close()
    return len(new_caps)


_REPROCESSORS = {
    "text_embedder": _reprocess_text_embedder,
    "visual_embedder": _reprocess_visual_embedder,
    "vlm_captioner": _reprocess_vlm_captioner,
}

# RPRC-2 dependency-aware invalidation. `_SATISFIES[provider]` = roles whose derived artifact the
# provider's reprocess ALSO rebuilds, so they need only a restamp — not a redundant rebuild — when
# both are stale in the same batch. One edge is active for in-place reprocess: re-captioning
# (`vlm_captioner`) rebuilds the text index, bringing `text_embedder` current. The rest of the role
# graph (R1->R4/5/6/7, R5->R6, R8->R9) involves roles that aren't in-place reprocessable — they go
# through whole-video `va reingest`, which re-runs the pipeline and satisfies those deps wholesale.
_SATISFIES = {
    "vlm_captioner": frozenset({"text_embedder"}),
}


def _dependency_ordered(roles):
    """Stale roles with dependency PROVIDERS first, so a provider's rebuild can satisfy its
    dependents within the same batch (a light topological order; one active edge today:
    vlm_captioner -> text_embedder)."""
    return sorted(roles, key=lambda r: 0 if r in _SATISFIES else 1)


def reprocessable_roles() -> frozenset:
    """Roles `va reprocess` can currently re-run in place; the rest need `va reingest`."""
    return frozenset(_REPROCESSORS)


def execute_reprocess(workdir: str, plan: list[dict[str, Any]]) -> dict[str, list]:
    """Execute a reprocess PLAN (rows from `plan_reprocess`): for each stale role that has a
    reprocessor, re-run it and THEN restamp its provenance with the current fingerprint.

    Ordering is the safety invariant — rows/shard FIRST, provenance SECOND — so a crash
    between them leaves the role stale (safe to retry), never falsely current. A role with
    no reprocessor is skipped (needs `va reingest`); a reprocessor that raises leaves that
    (video, role) stale and does NOT abort the batch (resumable). The restamp preserves the
    recorded ingest fps (a run arg the role's output may depend on) and carries the new
    fingerprint, so `va stale` goes clean only for what actually re-ran.

    Returns {"reprocessed": [(video_id, role, rows)], "skipped": [(video_id, role, reason)],
             "failed": [(video_id, role, error)]}.
    """
    from va.provenance import role_fingerprint
    from va.runtime.trace import current_run_id, traced_run
    from va.storage.structured.provenance_store import ProvenanceStore

    ws = Workspace(workdir)
    # One config snapshot PER FOOTAGE PROFILE for the whole batch (PROV-3's lesson):
    # fingerprinting each restamp against a fresh load_config() would let a mid-batch
    # config edit stamp a fingerprint that drifted from what the reprocessor ran under —
    # a missed stale. Pinned per profile because each video is rebuilt+restamped under
    # ITS recorded profile (WS2.c), not the base config. Plan rows carry profile/
    # source_type (stale_report); older/hand-built rows fall back to a catalog lookup.
    _cfg_pin: dict[str, Any] = {}

    def _cfg_for_item(item: dict[str, Any]):
        if "source_type" in item:
            prof, stype = item.get("profile"), item["source_type"]
        else:
            cat = Catalog(ws.catalog_db)
            try:
                v = lookup_video(cat, item["video_id"])
            finally:
                cat.close()
            if v is None:
                return None
            prof, stype = v.profile, v.source_type.value
        key = prof or default_footage_profile(stype)
        if key not in _cfg_pin:
            _cfg_pin[key] = config_for(prof, stype)
        return _cfg_pin[key]
    reprocessed: list = []
    skipped: list = []
    failed: list = []
    # traced_run gives the batch a run id, recorded on each restamp so a reprocess can be
    # correlated to its trace like an ingest (a no-op when tracing is off — run_id stays None).
    with traced_run("reprocess", workdir):
        pv = ProvenanceStore(ws.catalog_db)  # one connection for the whole batch; restamps only
        try:
            for item in plan:
                vid = item["video_id"]
                try:
                    cfg = _cfg_for_item(item)
                except Exception as e:  # noqa: BLE001 — e.g. the profile yaml was renamed;
                    # one item's config must not abort the batch (resumable contract)
                    for r in item["stale_roles"]:
                        failed.append((vid, r, f"footage profile unavailable: {e}"))
                    continue
                if cfg is None:
                    for r in item["stale_roles"]:
                        failed.append((vid, r, "video not found — removed before reprocess"))
                    continue
                # Roles the item's profile DISABLES must never be re-run here:
                # rebuilding them regenerates data the profile forbids and restamps
                # a stale signal that immediately re-fires (no convergence). Route
                # them to `skipped` — the printed remedy (`va reingest`) purges
                # their rows under the carried profile, which converges.
                disabled = {x for x in GATEABLE_ROLES
                            if x in cfg.roles and not cfg.role(x).enabled}
                for parent in list(disabled):
                    disabled |= GATE_DEPENDENTS.get(parent, frozenset())
                satisfied: set = set()  # roles a provider already rebuilt for THIS video (RPRC-2)
                for r in _dependency_ordered(item["stale_roles"]):
                    if r in disabled:
                        skipped.append((vid, r,
                                        "profile disables this role — reingest purges its rows"))
                        continue
                    if r in satisfied:
                        # a provider (e.g. re-caption rebuilds the text index) already brought this
                        # role's artifact current with the current model -> restamp only, skip the
                        # redundant rebuild. n=None marks "rebuilt via a dependency".
                        n = None
                    else:
                        fn = _REPROCESSORS.get(r)
                        if fn is None:
                            skipped.append((vid, r, "no in-place reprocess yet"))
                            continue
                        try:
                            n = fn(workdir, vid)
                        except Exception as e:  # noqa: BLE001 — one role's failure must not abort the batch
                            failed.append((vid, r, str(e)))
                            continue  # NO restamp -> stays stale -> retried next run
                        satisfied |= _SATISFIES.get(r, frozenset())  # this run rebuilt dependents too
                    # Restamp AFTER the rows are written (atomic at the provenance level).
                    ident = role_fingerprint(r, cfg)
                    prev = pv.get(vid, r)
                    fps = prev[0]["fps"] if prev else None  # preserve the recorded ingest fps
                    pv.record(vid, r, ident["model"], ident["fingerprint"],
                              fps=fps, run_id=current_run_id(), row_count=n)
                    reprocessed.append((vid, r, n))
        finally:
            pv.close()
    return {"reprocessed": reprocessed, "skipped": skipped, "failed": failed}
