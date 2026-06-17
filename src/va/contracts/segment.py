"""Segment contract — Role 1's output and the temporal backbone other roles
attach metadata to (captions, actions, keyframes)."""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


class Segment(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    video_id: UUID
    segment_index: int
    start_time: float = Field(ge=0)
    end_time: float = Field(ge=0)
    keyframe_paths: List[str] = Field(default_factory=list)  # filled by keyframe selection
    caption: Optional[str] = None                            # filled by Role 4

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    @model_validator(mode="after")
    def _check_order(self) -> "Segment":
        if self.end_time < self.start_time:
            raise ValueError("end_time must be >= start_time")
        return self
