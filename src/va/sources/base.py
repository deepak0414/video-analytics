"""Video source interface + URI dispatcher.

Two phases, matching the ingest pipeline:
  resolve(uri)        -> ResolvedVideo with a stable source_key (for dedup),
                         WITHOUT downloading. Cheap.
  fetch(resolved, dir)-> ResolvedVideo with local_path set + metadata probed.

A `VideoSource` (youtube, local, …) implements both. `resolve_source(uri)`
picks the right backend so callers stay source-agnostic.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from va.contracts.video import ResolvedVideo


@runtime_checkable
class VideoSource(Protocol):
    def resolve(self, uri: str) -> ResolvedVideo: ...
    def fetch(self, resolved: ResolvedVideo, cache_dir: str | Path) -> ResolvedVideo: ...


def resolve_source(uri: str) -> VideoSource:
    """Choose a source backend from the URI."""
    from .local import LocalSource
    from .youtube import YoutubeSource, is_youtube_url

    if is_youtube_url(uri):
        return YoutubeSource()
    if Path(uri).exists():
        return LocalSource()
    raise ValueError(f"unrecognized video source: {uri!r} (not a local file or YouTube URL)")
