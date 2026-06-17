"""Detection contract — Role 5 output; rows of the `object_detections` table.

bbox is normalized to [0,1]: (bbox_x, bbox_y) = top-left corner, (bbox_w,
bbox_h) = size, all as fractions of frame width/height — resolution-independent.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class Detection(BaseModel):
    video_id: Optional[UUID] = None      # filled by the pipeline
    timestamp: float = 0.0               # filled by the pipeline
    object_class: str
    confidence: float = Field(ge=0.0, le=1.0)
    bbox_x: float = Field(ge=0.0, le=1.0)
    bbox_y: float = Field(ge=0.0, le=1.0)
    bbox_w: float = Field(ge=0.0, le=1.0)
    bbox_h: float = Field(ge=0.0, le=1.0)
    track_id: Optional[UUID] = None      # filled by Role 6 later
