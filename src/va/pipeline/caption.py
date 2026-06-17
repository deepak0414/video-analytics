"""Caption search — "find the segment about X" over Role 4 segment captions.

A third query modality alongside visual (Role 2) and transcript (Role 8). Captions
capture meaning/context that visual similarity alone can miss.
"""
from __future__ import annotations

from typing import List

from va.pipeline.paths import Workspace
from va.storage.structured.segments import CaptionHit, SegmentStore


def search_captions(text: str, workdir: str = ".va", k: int = 10) -> List[CaptionHit]:
    store = SegmentStore(Workspace(workdir).catalog_db)
    try:
        return store.search_captions(text, k=k)
    finally:
        store.close()
