"""Action contract — Role 7 output; rows of the `action_events` table.

One recognized action over a time span (typically one Role-1 segment: actions
are properties of frame *sequences*, so the shot is the natural unit).
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ActionEvent(BaseModel):
    video_id: Optional[UUID] = None      # filled by the pipeline
    segment_id: Optional[UUID] = None    # filled by the pipeline (span -> segment)
    action_class: str
    confidence: float = Field(ge=0.0, le=1.0)
    start_time: float = Field(default=0.0, ge=0)
    end_time: float = Field(default=0.0, ge=0)
