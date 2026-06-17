"""Embedding + search-result contracts (Role 2 boundary)."""
from __future__ import annotations

from uuid import UUID

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator


class FrameEmbedding(BaseModel):
    """One sampled frame's embedding, tagged so a hit maps back to a moment."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    video_id: UUID
    timestamp: float = Field(ge=0)  # seconds into the video
    vector: np.ndarray

    @field_validator("vector")
    @classmethod
    def _check_vector(cls, v: np.ndarray) -> np.ndarray:
        if v.ndim != 1:
            raise ValueError("embedding vector must be 1-D")
        return v.astype(np.float32, copy=False)


class SearchHit(BaseModel):
    """A query result: a moment in a video, with the source for display."""

    video_id: UUID
    source_uri: str
    timestamp: float
    score: float
