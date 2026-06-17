"""Diarization contract — Role 9 (Speaker Diarizer) output.

A speaker turn is a span of audio attributed to one anonymous speaker label
(SPEAKER_00, SPEAKER_01, …). Turns are joined to Role-8 transcript lines by
temporal overlap to fill the `transcripts.speaker` column — there is no separate
turns table, matching the architecture's data model (Role 9 annotates Role 8).
"""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class SpeakerTurn(BaseModel):
    start_time: float = Field(ge=0)
    end_time: float = Field(ge=0)
    speaker: str

    @model_validator(mode="after")
    def _check_order(self) -> "SpeakerTurn":
        if self.end_time < self.start_time:
            raise ValueError("end_time must be >= start_time")
        return self
