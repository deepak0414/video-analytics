"""Object queries over Roles 5+6 output.

- query_objects: "does X appear / in how many frames" (Role 5 detections;
  frame appearances).
- count_objects: "how many DISTINCT X" (Role 6 tracks; one track = one followed
  object instance).
"""
from __future__ import annotations

import re
from typing import List

from va.pipeline.paths import Workspace
from va.storage.structured.detections import DetectionStore, ObjectSummary
from va.storage.structured.tracks import DistinctCount, TrackStore


def _classes(text: str) -> List[str]:
    """Candidate class names from query words — include singular forms so
    'birds' matches the detector class 'bird' (observed: plural query words
    silently produced ZERO object evidence)."""
    words = re.findall(r"[a-z0-9']+", text.lower())
    out: List[str] = []
    for w in words:
        for candidate in (w, w.rstrip("s")):
            if candidate and candidate not in out:
                out.append(candidate)
    return out


def query_objects(text: str, workdir: str = ".va") -> List[ObjectSummary]:
    """Treat each word of `text` as a candidate class name and summarize."""
    store = DetectionStore(Workspace(workdir).catalog_db)
    try:
        return store.summarize(_classes(text))
    finally:
        store.close()


def count_objects(
    text: str, workdir: str = ".va", min_frames: int = 2
) -> List[DistinctCount]:
    """Distinct-instance counts per class (Role 6 tracks). min_frames=2 drops
    single-frame tracks, which are usually detector flicker."""
    store = TrackStore(Workspace(workdir).catalog_db)
    try:
        return store.distinct_counts(_classes(text), min_frames=min_frames)
    finally:
        store.close()
