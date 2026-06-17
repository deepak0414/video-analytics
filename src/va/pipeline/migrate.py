"""One-time migration: workdir layout v1 -> v2 (`va migrate-layout`).

v1: flat cache/ with downloads + keyframes; ONE monolithic vectors.npz/.json.
v2: videos/<key16>-<slug>/ per video holding media + vector shard + keyframes.

Idempotent and conservative: media moves only if it lives under cache/ (user
files outside the workdir are never touched); catalog local_path/source_uri are
updated; the old monolith is renamed *.v1.bak (not deleted) after a successful
split. Safe to run on an already-migrated or empty workdir.
"""
from __future__ import annotations

import shutil
from collections import defaultdict
from pathlib import Path

from va.contracts.video import SourceType
from va.pipeline.paths import Workspace
from va.storage.structured.catalog_sqlite import Catalog
from va.storage.vector.numpy_flat import NumpyFlatVectorStore


def migrate_workdir(workdir: str) -> dict:
    ws = Workspace(workdir)
    stats = {"videos": 0, "media_moved": 0, "vectors_split": 0, "keyframes_moved": 0}
    if not ws.catalog_db.exists():
        return stats

    catalog = Catalog(ws.catalog_db)
    try:
        videos = catalog.list()
        stats["videos"] = len(videos)

        # --- split the monolithic vector index into per-video shards ---------
        legacy_npz = ws.root / "vectors.npz"
        if legacy_npz.exists():
            legacy = NumpyFlatVectorStore(ws.legacy_vectors)
            by_video: dict[str, list[int]] = defaultdict(list)
            for i, payload in enumerate(legacy._payloads):
                by_video[payload.get("video_id", "?")].append(i)
            for video in videos:
                rows = by_video.get(str(video.id))
                if not rows:
                    continue
                video_dir = ws.video_dir(video.source_key, video.title, create=True)
                shard = NumpyFlatVectorStore(video_dir / "vectors")
                if shard.count() == 0:          # idempotency
                    shard.add(
                        legacy._vecs[rows],
                        [legacy._payloads[i] for i in rows],
                    )
                    shard.persist()
                    stats["vectors_split"] += 1
            legacy_npz.rename(legacy_npz.with_suffix(".npz.v1.bak"))
            legacy_json = ws.root / "vectors.json"
            if legacy_json.exists():
                legacy_json.rename(legacy_json.with_suffix(".json.v1.bak"))

        # --- move managed media + retarget catalog paths ---------------------
        for video in videos:
            if not video.local_path:
                continue
            media = Path(video.local_path)
            video_dir = ws.video_dir(video.source_key, video.title, create=True)
            in_cache = media.exists() and ws.cache.resolve() in media.resolve().parents
            if in_cache:
                dest = video_dir / ("media" + media.suffix)
                if not dest.exists():
                    shutil.move(str(media), str(dest))
                new_uri = str(dest) if video.source_type is SourceType.local else None
                catalog.set_paths(video.id, str(dest), source_uri=new_uri)
                stats["media_moved"] += 1

            # --- move this video's keyframes out of the shared cache ---------
            old_kf = ws.cache / "keyframes"
            if old_kf.is_dir():
                kf_dir = video_dir / "keyframes"
                for png in sorted(old_kf.glob(f"{video.id}_*.png")):
                    kf_dir.mkdir(parents=True, exist_ok=True)
                    ts = png.stem.split("_")[-1]
                    target = kf_dir / f"{ts}.png"
                    if not target.exists():
                        shutil.move(str(png), str(target))
                    stats["keyframes_moved"] += 1
        return stats
    finally:
        catalog.close()
