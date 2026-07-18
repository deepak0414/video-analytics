"""Ingest<->query trace linking.

A query/ask trace names the ingest run(s) that produced the data it touched, so
you can jump from a surprising answer straight to the ingest(s) behind it — and
any degradations those ingests recorded (the swallowed best-effort failures now
surfaced during ingest tracing). The pointer is the ingest `run_id` stamped on
each video's catalog row when it was processed.

No-op — and no DB hit — when tracing is inactive, so callers can invoke it
unconditionally on the read path.
"""
from __future__ import annotations

from typing import Iterable
from uuid import UUID

from va.runtime.trace import current_tracer, trace


def trace_ingest_links(workdir: str, video_ids: Iterable) -> None:
    """Emit one `link/ingest_runs` event mapping each touched video (16-char
    source_key prefix — matches the per-video dir naming) to its last ingest
    `run_id`, or None when that video was ingested with tracing off."""
    if current_tracer() is None:      # tracing inactive -> skip the whole thing
        return
    ids = []
    for v in video_ids:
        if v is None:
            continue
        ids.append(v if isinstance(v, UUID) else UUID(str(v)))
    if not ids:
        return

    # Imported lazily: this is a read-path leaf and the storage import would
    # otherwise pull the DB layer into every trace-aware module.
    from va.pipeline.paths import Workspace
    from va.storage.structured.catalog_sqlite import Catalog

    cat = Catalog(Workspace(workdir).catalog_db)
    try:
        videos = cat.get_many(ids)
    finally:
        cat.close()
    if not videos:
        return
    # Key by the same 16-char key the per-video dir uses (`Workspace._key16`), so
    # the label in the trace matches `videos/<key16>-<slug>/` on disk.
    links = {Workspace._key16(v.source_key or str(v.id)): v.last_ingest_run_id
             for v in videos.values()}
    trace("link", "ingest_runs", f"{len(links)} video(s)", **links)
