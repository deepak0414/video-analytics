"""Sidecar stub diarizer — deterministic, dependency-free, for the test suite.

Real diarization needs a model + actual multi-speaker audio; our synthetic test
clips are silent. So this backend reads a sidecar file next to the media
(`<video>.diarization.json`), letting the full diarize → assign → store path be
tested offline. No sidecar → no turns (lines keep speaker=None).

Format: {"turns": [{"start_time":0.0,"end_time":5.0,"speaker":"SPEAKER_00"}, ...]}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from va.contracts.diarization import SpeakerTurn


def sidecar_path(media_path: str) -> Path:
    return Path(media_path).with_suffix(".diarization.json")


class SidecarDiarizer:
    def diarize(self, media_path: str, *, num_speakers=None,
                min_speakers=None, max_speakers=None) -> List[SpeakerTurn]:
        # count hints don't apply to a fixed sidecar file — ignored by design.
        path = sidecar_path(media_path)
        if not path.exists():
            return []
        doc = json.loads(path.read_text())
        return [SpeakerTurn(**t) for t in doc.get("turns", [])]
