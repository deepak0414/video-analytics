"""Qwen2.5-VL Role-11 backend — the local (self-hosted) reasoner.

Reuses the SAME loaded model as the Role-4 captioner (identical ModelManager
key), so enabling reasoning costs no extra GPU memory — exactly the
"models spanning multiple roles" play from the model-analysis doc.

Falls back to the rule stub when the LLM emits unparseable JSON, so the ask
pipeline always returns something structured.
"""
from __future__ import annotations

from typing import Any, List, Sequence
from uuid import UUID

from PIL import Image
from pydantic import ValidationError

from va.adapters.reasoner.prompts import (
    PLANNER_PROMPT,
    REASONER_PROMPT,
    coerce_timestamp,
    parse_json_block,
    render_evidence,
    render_keyframe_note,
)
from va.adapters.reasoner.rule_inproc import RuleReasoner
from va.contracts.evidence import Evidence
from va.contracts.query_plan import Answer, QueryPlan
from va.roles.reasoner import Keyframe
from va.runtime.device import resolve_device
from va.runtime.manager import MANAGER

_WEIGHTS = {
    "qwen2.5-vl-7b": "Qwen/Qwen2.5-VL-7B-Instruct",
    "qwen2.5-vl-3b": "Qwen/Qwen2.5-VL-3B-Instruct",
}


class QwenReasoner:
    def __init__(self, load: dict[str, Any] | None = None):
        load = load or {}
        self.model_key = load.get("model", "qwen2.5-vl-7b")
        self.weights = load.get("weights", _WEIGHTS.get(self.model_key, self.model_key))
        self.device = resolve_device(load.get("device"))
        self.max_new_tokens = int(load.get("max_new_tokens", 512))
        # Same cache key as the Role-4 captioner -> shared model instance.
        bundle = MANAGER.get(f"qwenvl::{self.weights}::{self.device}", self._build)
        self._model = bundle["model"]
        self._processor = bundle["processor"]
        self._fallback = RuleReasoner()

    def _build(self) -> dict:
        import torch
        from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

        dtype = torch.float16 if self.device != "cpu" else torch.float32
        model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            self.weights, torch_dtype=dtype
        ).to(self.device).eval()
        processor = AutoProcessor.from_pretrained(self.weights)
        return {"model": model, "processor": processor}

    def _chat(self, prompt: str, images: Sequence[Image.Image] = ()) -> str:
        import torch

        content: List[dict] = [{"type": "image", "image": im} for im in images]
        content.append({"type": "text", "text": prompt})
        messages = [{"role": "user", "content": content}]
        text = self._processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        kwargs = {"text": [text], "return_tensors": "pt"}
        if images:
            kwargs["images"] = [im.convert("RGB") for im in images]
        inputs = self._processor(**kwargs).to(self.device)
        with torch.no_grad():
            # deterministic generation: answer variance should come from evidence,
            # not sampling (see plan.md S8.4 / stability measurement)
            generated = self._model.generate(
                **inputs, max_new_tokens=self.max_new_tokens, do_sample=False
            )
        trimmed = [out[len(inp):] for inp, out in zip(inputs.input_ids, generated)]
        return self._processor.batch_decode(
            trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=True
        )[0].strip()

    # --- Role 11 interface ---------------------------------------------------
    def plan(self, query: str) -> QueryPlan:
        raw = self._chat(PLANNER_PROMPT.format(query=query))
        doc = parse_json_block(raw)
        if doc is None:
            return self._fallback.plan(query)  # unparseable -> heuristics
        doc.setdefault("query", query)
        try:
            plan = QueryPlan.model_validate(doc)
        except ValidationError as e:
            # Wrong-typed but well-formed JSON (Qwen emits e.g.
            # params: "person's dress" where a dict is required): salvage the
            # plan by dropping the offending fields rather than crashing the ask.
            for err in e.errors():
                if err["loc"]:
                    doc.pop(err["loc"][0], None)
            try:
                plan = QueryPlan.model_validate(doc)
            except ValidationError:
                return self._fallback.plan(query)
        plan.query = query
        return plan

    def reason(
        self, query: str, evidence: Evidence, keyframes: Sequence[Keyframe] = ()
    ) -> Answer:
        prompt = REASONER_PROMPT.format(
            query=query,
            evidence=render_evidence(evidence),
            keyframe_note=render_keyframe_note(keyframes),
        )
        raw = self._chat(prompt, images=[kf.image for kf in keyframes])
        doc = parse_json_block(raw)
        if doc is None:
            ans = self._fallback.reason(query, evidence, keyframes)
            ans.attributes["backend"] = "qwen(fallback-rule)"
            ans.attributes["raw"] = raw[:500]
            return ans

        items = [i for i in doc.get("items", []) if isinstance(i, dict)]
        citations: list[tuple[UUID, float]] = []
        for i in items:
            ts = coerce_timestamp(i.get("timestamp"))
            if ts is not None:
                i["timestamp"] = ts            # normalize in place for rendering
            try:
                if ts is not None:
                    citations.append((UUID(str(i["video_id"])), ts))
            except (KeyError, ValueError, TypeError):
                continue
        return Answer(
            text=str(doc.get("summary", "")),
            citations=citations,
            attributes={"items": items, "backend": f"qwen:{self.model_key}"},
        )
