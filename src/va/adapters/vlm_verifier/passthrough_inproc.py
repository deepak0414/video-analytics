"""Stub VLM verifier — accepts everything (no-op). Default for offline/CI.

`enabled=False` signals callers (pipeline/verify.py) to skip frame extraction and
the verifier loop entirely, so the stub pipeline behaves exactly as it did before
SR.6: SigLIP/YOLO results pass through unchanged.
"""
from __future__ import annotations

from PIL import Image

from va.roles.vlm_verifier import Verdict


class PassthroughVerifier:
    enabled = False

    def verify(self, image: Image.Image, claim: str) -> Verdict:
        # never actually reached (callers short-circuit on enabled=False); accepts
        # by contract.
        return Verdict(label="yes", detail="passthrough (no VLM verifier configured)")
