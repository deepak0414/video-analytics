"""Video lifecycle — `va remove` / `va reingest` (layout v2).

remove: delete one video everywhere — its rows in every role table, its catalog
row, and its artifact directory (media/vectors/keyframes). With per-video vector
shards this is exact: no monolithic-index surgery.

reingest: remove + ingest again from the canonical source — the model-upgrade
path that previously forced a fresh workdir. Managed media of LOCAL sources is
preserved through the cycle (moved aside, re-ingested from there); YouTube
sources re-download.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Optional
from uuid import UUID

from va.contracts.video import SourceType, Video
from va.pipeline.paths import Workspace
from va.storage.structured.catalog_sqlite import Catalog

# every per-role table keyed by video_id (schema.py)
_ROLE_TABLES = [
    "segments", "transcripts", "object_detections", "object_tracks",
    "action_events", "ocr_results", "observations",
]


def lookup_video(catalog: Catalog, ident: str) -> Optional[Video]:
    """Find a video by UUID, source_key, or source URI (URL/path)."""
    try:
        v = catalog.get(UUID(ident))
        if v is not None:
            return v
    except ValueError:
        pass
    v = catalog.get_by_source_key(ident)
    if v is not None:
        return v
    try:  # URL or path -> source_key (offline for youtube ids; hashes local files)
        from va.sources.base import resolve_source

        resolved = resolve_source(ident).resolve(ident)
        return catalog.get_by_source_key(resolved.source_key)
    except Exception:
        return None


def remove_video(workdir: str, ident: str, keep_media: bool = False) -> Optional[Video]:
    """Delete a video's data everywhere. Returns the removed Video, or None.

    keep_media: move managed media out to cache/ instead of deleting it
    (used by reingest so local sources survive the cycle)."""
    ws = Workspace(workdir)
    catalog = Catalog(ws.catalog_db)
    try:
        video = lookup_video(catalog, ident)
        if video is None:
            return None

        video_dir = ws.video_dir(video.source_key, video.title)
        kept_media: Optional[Path] = None
        if keep_media and video.local_path:
            media = Path(video.local_path)
            if media.exists() and video_dir in media.parents:
                ws.cache.mkdir(parents=True, exist_ok=True)
                kept_media = ws.cache / f"reingest-{media.name}"
                shutil.move(str(media), str(kept_media))

        conn = sqlite3.connect(ws.catalog_db)
        try:
            for table in _ROLE_TABLES:
                conn.execute(f"DELETE FROM {table} WHERE video_id = ?", (str(video.id),))
            conn.commit()
        finally:
            conn.close()
        catalog.delete(video.id)

        if video_dir.exists():
            shutil.rmtree(video_dir)
        if kept_media is not None:
            video = video.model_copy(update={"local_path": str(kept_media)})
        return video
    finally:
        catalog.close()


def reingest_video(workdir: str, ident: str, fps: float = 1.0):
    """remove + ingest from the canonical source. Returns IngestResult or None."""
    from va.pipeline.ingest import ingest

    video = remove_video(workdir, ident, keep_media=True)
    if video is None:
        return None
    if video.source_type is SourceType.local:
        # canonical input is a file; the managed copy (if any) was preserved
        src = video.local_path or video.source_uri
    else:
        src = video.source_uri          # e.g. the YouTube URL: re-download
    return ingest(src, workdir=workdir, fps=fps)
