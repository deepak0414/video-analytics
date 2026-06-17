"""Role 10 — OCR (the contract).

Reads text that appears ON SCREEN (signs, billboards, title cards, burned-in
captions) — not what is said (that's Role 8). Backends (sidecar stub, PaddleOCR,
cloud) are interchangeable. Each backend owns its own frame handling (sampling
rate, dedup of text that persists across frames), mirroring SpeechToText owning
its audio handling.
"""
from __future__ import annotations

from typing import List, Protocol, runtime_checkable

from va.contracts.ocr import OcrLine


@runtime_checkable
class OcrReader(Protocol):
    def read(self, media_path: str) -> List[OcrLine]:
        """Return timestamped on-screen text lines (empty if none)."""
        ...
