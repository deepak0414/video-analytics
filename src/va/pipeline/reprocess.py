"""Batch-reprocess selection (WS-1 §6-b, pillar B / RPRC-3 — dry-run front-end).

Resolve WHICH (video, role) pairs a reprocess WOULD touch — the stale set from
PROV-4 (`stale_report`), scoped by role/video/all-stale — WITHOUT mutating anything.
The executor (RPRC-1, not built yet) will consume `plan_reprocess`; today only the
plan exists, so `va reprocess` can currently do nothing but show it.
"""
from __future__ import annotations

from typing import Any, Optional

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
