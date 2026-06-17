"""Standard locations inside a workdir (default `.va`) — layout v2.

    <workdir>/
    ├── catalog.db                      # ONE shared relational DB (all videos, all roles)
    ├── cache/                          # transient: in-flight downloads, ffmpeg shim
    └── videos/<key16>-<slug>/          # per-video artifacts
        ├── media.<ext>                 # managed media (downloads; user files stay put)
        ├── vectors.npz / vectors.json  # this video's embedding shard
        └── keyframes/                  # ask()/deep-scan keyframes

Naming: identity is the first 16 chars of `source_key` (content sha256 for local
files, the 11-char id for YouTube — stable across re-downloads); the slug is a
cosmetic, human-readable suffix from the title. Lookup globs on the key prefix
only, so a later title change can't orphan a directory.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


class Workspace:
    def __init__(self, workdir: str | Path = ".va"):
        self.root = Path(workdir)

    @property
    def catalog_db(self) -> Path:
        return self.root / "catalog.db"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def videos_root(self) -> Path:
        return self.root / "videos"

    @property
    def legacy_vectors(self) -> Path:
        """Pre-v2 monolithic vector index path (migration source)."""
        return self.root / "vectors"

    # --- per-video directories ----------------------------------------------
    @staticmethod
    def _key16(source_key: str) -> str:
        key = source_key.split(":", 1)[-1]          # "sha256:<hex>" -> "<hex>"
        key = re.sub(r"[^A-Za-z0-9_-]", "", key)
        return key[:16] or "unknown"

    @staticmethod
    def _slug(title: Optional[str]) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
        return slug[:32].rstrip("-") or "video"

    def video_dir(
        self, source_key: str, title: Optional[str] = None, create: bool = False
    ) -> Path:
        key = self._key16(source_key)
        if self.videos_root.is_dir():
            existing = sorted(self.videos_root.glob(f"{key}-*"))
            if existing:
                return existing[0]
        path = self.videos_root / f"{key}-{self._slug(title)}"
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path
