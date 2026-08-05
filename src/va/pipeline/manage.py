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

# every per-role table keyed by video_id (schema.py) — `va remove` purges each so a
# video is deleted EVERYWHERE (incl. §6-b provenance; else `va stale`/reprocess ghost it)
_ROLE_TABLES = [
    "segments", "transcripts", "object_detections", "object_tracks",
    "action_events", "ocr_results", "observations", "role_provenance",
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


def reingest_video(workdir: str, ident: str, fps: float = 1.0, profile: str | None = None):
    """remove + ingest from the canonical source. Returns IngestResult or None.
    The recorded footage profile carries forward unless `profile` overrides it
    (a pre-profile row's None falls through to the source-derived default)."""
    from va.configuration import load_config
    from va.pipeline.ingest import ingest

    ws = Workspace(workdir)
    catalog = Catalog(ws.catalog_db)
    try:
        existing = lookup_video(catalog, ident)
    finally:
        catalog.close()
    if existing is None:
        return None
    # Validate the target profile BEFORE the destructive removal: a typo'd
    # --profile (or a recorded profile whose yaml was renamed since ingest) must
    # fail here with the video's data intact, not after remove_video ran.
    # Unconditional: a None target resolves exactly as ingest's own probe will
    # (roles.yaml active_footage_profile > generic), so a broken active profile
    # also fails pre-removal rather than post.
    target_profile = profile or existing.profile
    load_config(footage_profile=target_profile)

    video = remove_video(workdir, ident, keep_media=True)
    if video is None:
        return None
    if video.source_type is SourceType.local:
        # canonical input is a file; the managed copy (if any) was preserved
        src = video.local_path or video.source_uri
    else:
        src = video.source_uri          # e.g. the YouTube URL: re-download
    def _preattach_chunk_metadata() -> None:
        # Carry the chunk metadata (camera link + wall-clock base) across the
        # remove+ingest cycle, like the profile: reingesting a chunk must not
        # sever it from its camera's collection or drop it from wall-clock
        # queries. Attached BEFORE ingest runs — WS4.b's motion-episodes Role-1
        # backend consumes start_epoch DURING ingest, so a post-hoc reattach
        # would silently degrade an epoch-placed chunk to one full-span segment
        # and stamp it provenance-current (unreachable by `va stale`). The
        # pre-created row also covers the failure path: ingest's get_or_create
        # finds it, and a later plain-`va ingest` retry completes it as-is with
        # the metadata already on it.
        if existing.camera_id is None and existing.start_epoch is None:
            return
        from va.sources.base import resolve_source

        resolved = resolve_source(src).resolve(src)  # cheap; no fetch
        catalog = Catalog(ws.catalog_db)
        try:
            row, _ = catalog.get_or_create(resolved)
            if existing.camera_id is not None:
                catalog.set_camera(row.id, existing.camera_id)
            if existing.start_epoch is not None:
                catalog.set_start_epoch(row.id, existing.start_epoch)
        finally:
            catalog.close()

    _preattach_chunk_metadata()
    result = ingest(src, workdir=workdir, fps=fps, profile=target_profile)
    if result is not None:
        result.video = result.video.model_copy(update={
            "camera_id": existing.camera_id or result.video.camera_id,
            "start_epoch": existing.start_epoch
            if existing.start_epoch is not None else result.video.start_epoch,
        })
    return result
