"""Sidecar stub STT — deterministic, dependency-free, for the test suite.

Real transcription needs a model + actual audio; our synthetic test clips are
silent. So this backend reads a sidecar transcript file placed next to the
video (`<video>.transcript.json`), letting the full ingest→store→search path be
tested deterministically. If no sidecar exists, it returns no transcript.

Sidecar format: {"lines": [{"start_time":0.0,"end_time":2.0,"text":"...","speaker":"A"}, ...]}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from va.contracts.transcript import TranscriptLine


def sidecar_path(media_path: str) -> Path:
    return Path(media_path).with_suffix(".transcript.json")


class SidecarSTT:
    def transcribe(self, media_path: str) -> List[TranscriptLine]:
        path = sidecar_path(media_path)
        if not path.exists():
            return []
        doc = json.loads(path.read_text())
        return [TranscriptLine(**ln) for ln in doc.get("lines", [])]
