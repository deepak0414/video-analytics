"""Real SR.6 backend: Qwen2.5-VL verifies a claim about a single frame.

Reuses the SAME Qwen ModelManager bundle as the Role-4 captioner / Role-11
reasoner (keyed by weights+device), so enabling the verifier costs NO extra VRAM.
Select via config: vlm_verifier.model = qwen2.5-vl-7b.

The parse is deliberately CONSERVATIVE (see VlmVerifier contract): the frame is
DROPPED only when Qwen's answer leads with a clear negative; "yes", a hedge, or
anything unparseable KEEPS the frame. So the verifier overturns SigLIP/YOLO only
when the VLM is confidently negative — it cannot silently delete true positives,
which is what protects the currently-passing queries from regressing.
"""
from __future__ import annotations

import re
from typing import Any

from PIL import Image

from va.roles.vlm_verifier import Verdict

# Leading token classifies the answer; anything else is "unsure".
_NEGATIVE = re.compile(r"^\s*(no|nope|not|none|negative|false)\b", re.I)
_POSITIVE = re.compile(r"^\s*(yes|yeah|yep|correct|true|sure)\b", re.I)

_PROMPT = (
    "Look at this image. Does it show: {claim}? "
    "Answer with one word — YES or NO — then at most 5 words of reason."
)


class QwenVerifier:
    enabled = True

    def __init__(self, load: dict[str, Any] | None = None):
        # A short answer is all we need; cap tokens so verification stays cheap.
        from va.adapters.vlm_captioner.qwen_inproc import QwenCaptioner

        self._vlm = QwenCaptioner({**(load or {}), "max_new_tokens": 24})

    def verify(self, image: Image.Image, claim: str) -> Verdict:
        answer = self._vlm.caption([image], prompt=_PROMPT.format(claim=claim)).strip()
        if _NEGATIVE.match(answer):
            label = "no"
        elif _POSITIVE.match(answer):
            label = "yes"
        else:
            label = "unsure"
        return Verdict(label=label, detail=answer)
