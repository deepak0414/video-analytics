"""Role 4 — VLM Captioner (the contract).

Given 1-N keyframe images from a video segment, produce a text description of the
segment. Runs at ingest time, per segment (depends on Role 1 segments). The same
model type can later serve Role 11 (reasoning) at query time. Backends (color
stub, Qwen2.5-VL, cloud) are interchangeable.
"""
from __future__ import annotations

from typing import Optional, Protocol, Sequence, runtime_checkable

from PIL import Image

DEFAULT_PROMPT = "Describe what is happening in this video segment in one sentence."


@runtime_checkable
class VLMCaptioner(Protocol):
    def caption(self, images: Sequence[Image.Image], prompt: Optional[str] = None) -> str:
        """Return a one-line description of the segment from its keyframe(s)."""
        ...
