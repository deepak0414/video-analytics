"""Role 11 — Reasoning LLM (the contract).

Two call sites in the query pipeline:
  plan(query)                       -> QueryPlan   (which tiers to run)
  reason(query, evidence, keyframes) -> Answer     (grounded, cited answer)

The reasoner never watches video: it gets the Evidence bundle (text) plus a few
keyframe images from candidate moments, and must cite (video_id, timestamp) for
every claim so answers can hyperlink back into the video. Backends (rule stub,
Qwen2.5-VL, Claude via CLI/API) are interchangeable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Sequence, runtime_checkable
from uuid import UUID

from PIL import Image

from va.contracts.evidence import Evidence
from va.contracts.query_plan import Answer, QueryPlan


@dataclass
class Keyframe:
    """A frame handed to the reasoner, tagged so claims can cite the moment."""

    video_id: UUID
    timestamp: float
    image: Image.Image
    path: Optional[str] = None  # on-disk copy (for CLI backends that Read files)


@runtime_checkable
class Reasoner(Protocol):
    def plan(self, query: str) -> QueryPlan:
        """Classify the query into tier flags + search terms."""
        ...

    def reason(
        self, query: str, evidence: Evidence, keyframes: Sequence[Keyframe] = ()
    ) -> Answer:
        """Answer from evidence + keyframes; cite (video_id, timestamp) per claim."""
        ...
