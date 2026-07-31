"""Batch reprocess — selection (RPRC-3) + execution (RPRC-1a) (WS-1 §6-b, pillar B).

`plan_reprocess` resolves WHICH (video, role) pairs a reprocess would touch — the stale
set from PROV-4 (`stale_report`), scoped by role/video/all-stale — WITHOUT mutating.
`execute_reprocess` then re-runs each stale role that has an in-place reprocessor and
restamps its provenance. Only `text_embedder` is wired so far (`reprocessable_roles()`);
every other stale role is left for whole-video `va reingest` (D5 scope cap). The CLI runs
the plan on `--dry-run` and executes otherwise.
"""
from __future__ import annotations

from typing import Any, Optional

from va.configuration import load_config
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


def _reprocess_text_embedder(workdir: str, video_id: str) -> int:
    from va.pipeline.text_index import backfill_text_index

    n = backfill_text_index(workdir, video_id)  # rebuilds + re-tags the text_vectors shard
    if n is None:
        # backfill returns None when the ident no longer resolves (video removed between
        # plan and execute, or a stale plan): NOT a 0-row rebuild. Raise so the executor
        # routes it to `failed` and never restamps provenance for a rebuild that didn't run.
        raise ValueError(f"video {video_id} not found — cannot reprocess text_embedder")
    return n


_REPROCESSORS = {
    "text_embedder": _reprocess_text_embedder,
}


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

    Returns {"reprocessed": [(video_id, role, rows)], "skipped": [(video_id, role)],
             "failed": [(video_id, role, error)]}.
    """
    from va.provenance import role_fingerprint
    from va.runtime.trace import current_run_id
    from va.storage.structured.provenance_store import ProvenanceStore

    ws = Workspace(workdir)
    # One config snapshot for the whole batch (PROV-3's lesson): fingerprinting each restamp
    # against a fresh load_config() would let a mid-batch config edit stamp a fingerprint that
    # drifted from what the reprocessor ran under — a missed stale. Pinning here keeps that a
    # safe false-stale at worst.
    cfg = load_config()
    reprocessed: list = []
    skipped: list = []
    failed: list = []
    pv = ProvenanceStore(ws.catalog_db)  # one connection for the whole batch; restamps only
    try:
        for item in plan:
            vid = item["video_id"]
            for r in item["stale_roles"]:
                fn = _REPROCESSORS.get(r)
                if fn is None:
                    skipped.append((vid, r))  # no in-place reprocess -> `va reingest`
                    continue
                try:
                    n = fn(workdir, vid)
                except Exception as e:  # noqa: BLE001 — one role's failure must not abort the batch
                    failed.append((vid, r, str(e)))
                    continue  # NO restamp -> stays stale -> retried next run
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
