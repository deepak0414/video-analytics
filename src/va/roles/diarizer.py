"""Role 9 — Speaker Diarizer (the contract).

Labels WHO is speaking when: partitions the audio into speaker turns
(SPEAKER_00, SPEAKER_01, …). It does not know names — just consistent anonymous
labels. The pipeline joins these turns to Role-8 transcript lines (temporal
overlap) to fill `transcripts.speaker`. Backends (sidecar stub, pyannote.audio)
are interchangeable; each owns its own audio handling, like SpeechToText.
"""
from __future__ import annotations

from typing import List, Optional, Protocol, runtime_checkable

from va.contracts.diarization import SpeakerTurn


@runtime_checkable
class SpeakerDiarizer(Protocol):
    def diarize(
        self,
        media_path: str,
        *,
        num_speakers: Optional[int] = None,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
    ) -> List[SpeakerTurn]:
        """Return speaker turns (empty if no speech/audio).

        Optional speaker-count hints: clustering diarizers (pyannote) under-cluster
        on hard audio (brief/overlapping/similar voices — measured: 4 found vs >4
        true on an SNL sketch). Pass `num_speakers` (exact) or `min/max_speakers`
        (bounds) when the count is known to recover them. Backends without count
        control (the stub) ignore the hints."""
        ...
