"""Transcript contract — Role 8 (Speech-to-Text) output; rows of the
`transcripts` table, optionally speaker-labeled by Role 9."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, model_validator


class TranscriptLine(BaseModel):
    start_time: float = Field(ge=0)
    end_time: float = Field(ge=0)
    text: str
    speaker: Optional[str] = None  # filled by Role 9 (diarizer)

    @model_validator(mode="after")
    def _check_order(self) -> "TranscriptLine":
        if self.end_time < self.start_time:
            raise ValueError("end_time must be >= start_time")
        return self
