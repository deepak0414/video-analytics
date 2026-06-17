"""On-screen text search — "when does that sign/title appear" over ocr_results.

A fourth query modality alongside visual (Role 2), captions (Role 4), and
transcripts (Role 8). Reads what is ON screen, not what is said.
"""
from __future__ import annotations

from typing import List

from va.pipeline.paths import Workspace
from va.storage.structured.ocr import OcrHit, OcrStore


def search_ocr(text: str, workdir: str = ".va", k: int = 10) -> List[OcrHit]:
    store = OcrStore(Workspace(workdir).catalog_db)
    try:
        return store.search(text, k=k)
    finally:
        store.close()
