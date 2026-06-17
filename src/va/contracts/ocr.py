"""OCR contract — Role 10 output; rows of the `ocr_results` table.

One line of on-screen text seen at one sampled instant. bbox is normalized to
[0,1] like Detection (top-left + size as fractions of frame width/height); it is
optional because some backends (and the sidecar stub) may not localize.
`confidence` is used for filtering before storage and is not persisted (the
ocr_results table has no confidence column by design — see architecture doc).
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class OcrLine(BaseModel):
    video_id: Optional[UUID] = None      # filled by the pipeline
    timestamp: float = Field(default=0.0, ge=0)
    text: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    bbox_x: Optional[float] = None
    bbox_y: Optional[float] = None
    bbox_w: Optional[float] = None
    bbox_h: Optional[float] = None
