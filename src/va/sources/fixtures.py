"""Pull pinned test fixtures (YouTube clips) declared in tests/fixtures/sources.yaml.

Each entry pins a video by id; downloads go through the same YoutubeSource the
pipeline uses. Network-dependent — not run in unit tests.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from va.sources.youtube import YoutubeSource


def _sources_file() -> Path:
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "sources.yaml"


def pull_fixtures(workdir: str | Path = ".va") -> list[str]:
    doc = yaml.safe_load(_sources_file().read_text()) or {}
    cache = Path(workdir) / "cache"
    src = YoutubeSource()
    pulled: list[str] = []
    for entry in doc.get("fixtures", []):
        url = f"https://www.youtube.com/watch?v={entry['video_id']}"
        resolved = src.fetch(src.resolve(url), cache)
        print(f"pulled {entry.get('name', entry['video_id'])} -> {resolved.local_path}")
        pulled.append(resolved.local_path or "")
    return pulled
