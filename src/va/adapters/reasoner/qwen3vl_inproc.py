"""Qwen3-VL-30B-A3B (MoE) Role-11 reasoner — ACCEPTED local backend.

Same Role-11 protocol + tolerant JSON parsing + plan()/reason()/_chat() as the
Qwen2.5-VL reasoner (QwenReasoner), but loads the Qwen3-VL **MoE** architecture
(`Qwen3VLMoeForConditionalGeneration`) under a DISTINCT ModelManager key (it is a
separate, larger model — not shared with the Role-4 captioner). Validated at
golden-set parity with the claude-code backend — decision + revisit triggers in
`video-analytics-model-analysis.md` (Role 11 block, Accepted 2026-07-29).
Selected via `reasoner.model: qwen3-vl-30b-a3b` (`run-qwen3vl/config`).
"""
from __future__ import annotations

from typing import Any

from va.adapters.reasoner.qwen_inproc import QwenReasoner
from va.adapters.reasoner.rule_inproc import RuleReasoner
from va.runtime.device import resolve_device
from va.runtime.manager import MANAGER

# Only the validated variant. Adding another (e.g. the -Thinking build) needs a
# `weights:` profile entry pointing at LOCAL files first — without one,
# from_pretrained would start a ~58 GB HF download on this box's ~0.5 MB/s path.
_WEIGHTS = {
    "qwen3-vl-30b-a3b": "Qwen/Qwen3-VL-30B-A3B-Instruct",
}


class Qwen3VLReasoner(QwenReasoner):
    """Inherits plan()/reason()/_chat() from QwenReasoner; overrides only how the
    model is built (MoE arch, own cache key, bf16)."""

    def __init__(self, load: dict[str, Any] | None = None):
        load = load or {}
        self.model_key = load.get("model", "qwen3-vl-30b-a3b")
        self.weights = load.get("weights", _WEIGHTS.get(self.model_key, self.model_key))
        self.device = resolve_device(load.get("device"))
        self.dtype_name = str(load.get("dtype", "bfloat16")).lower()
        # Validate the NAME at construction (no torch needed): a profile typo
        # like `dtype: fp16` must fail here, offline-testably — not minutes
        # into the real 58 GB load on the Spark.
        if self.dtype_name not in ("bfloat16", "float16", "float32"):
            raise ValueError(
                f"qwen3-vl reasoner: unrecognized dtype '{self.dtype_name}' "
                f"(allowed: bfloat16, float16, float32; cpu always uses float32)"
            )
        self.max_new_tokens = int(load.get("max_new_tokens", 512))
        # DISTINCT key — this is NOT the Role-4 captioner's model. dtype is part
        # of the key: honoring the knob means a different dtype is a different
        # cached model, never a silent reuse at the wrong precision.
        bundle = MANAGER.get(
            f"qwen3vl::{self.weights}::{self.device}::{self.dtype_name}",
            self._build,
        )
        self._model = bundle["model"]
        self._processor = bundle["processor"]
        self._fallback = RuleReasoner()

    def _build(self) -> dict:
        import torch
        from transformers import AutoProcessor, Qwen3VLMoeForConditionalGeneration

        # dtype_name was validated at construction; cpu still forces float32,
        # and bfloat16 stays the default (what the golden parity run validated).
        if self.device == "cpu":
            dtype = torch.float32
        else:
            dtype = getattr(torch, self.dtype_name)
        # device_map loads shards straight onto the device (unified memory) —
        # avoids a transient 2x host+device copy of a ~60GB model.
        model = Qwen3VLMoeForConditionalGeneration.from_pretrained(
            self.weights, torch_dtype=dtype, device_map=self.device,
        ).eval()
        processor = AutoProcessor.from_pretrained(self.weights)
        return {"model": model, "processor": processor}
