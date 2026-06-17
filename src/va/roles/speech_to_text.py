"""Role 8 — Speech-to-Text (the contract).

Transcribes a media file's audio into timestamped lines. Backends (sidecar stub,
Whisper, cloud) are interchangeable. Each backend owns its own audio handling
(extracting the audio track from the video as needed).
"""
from __future__ import annotations

from typing import List, Protocol, runtime_checkable

from va.contracts.transcript import TranscriptLine


@runtime_checkable
class SpeechToText(Protocol):
    def transcribe(self, media_path: str) -> List[TranscriptLine]:
        """Return timestamped transcript lines (empty if no speech/audio)."""
        ...
