"""Speaker assignment — join Role-9 turns to Role-8 transcript lines.

Each transcript line takes the speaker of the turn it most overlaps in time (a
temporal join — the project's universal correlation mechanism, on `video_id` +
time). Lines with no overlapping turn keep their existing speaker (usually None).
"""
from __future__ import annotations

from typing import List, Sequence

from va.contracts.diarization import SpeakerTurn
from va.contracts.transcript import TranscriptLine


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def assign_speakers(
    lines: Sequence[TranscriptLine], turns: Sequence[SpeakerTurn]
) -> List[TranscriptLine]:
    """Return copies of `lines` with `speaker` set to the max-overlap turn's."""
    if not turns:
        return [ln.model_copy() for ln in lines]
    out: List[TranscriptLine] = []
    for ln in lines:
        best_speaker, best_overlap = None, 0.0
        for t in turns:
            ov = _overlap(ln.start_time, ln.end_time, t.start_time, t.end_time)
            if ov > best_overlap:
                best_overlap, best_speaker = ov, t.speaker
        out.append(ln.model_copy(update={"speaker": best_speaker})
                   if best_speaker is not None else ln.model_copy())
    return out
