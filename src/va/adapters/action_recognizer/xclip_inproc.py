"""Real Role-7 backend: X-CLIP (video-text contrastive) via transformers.

Zero-shot and open-vocabulary, like YOLO-World for Role 5: the requested action
phrases are encoded by the text tower and scored against an 8-frame clip
embedding per span — no fixed Kinetics label set, so the ingest vocabulary
lives in config. (plan.md S5.6 named InternVideo2 — that's a custom OpenGVLab
stack, not transformers-native; after the paddlepaddle-on-aarch64 segfault
lesson we prefer runtimes already proven on this box. VideoMAE was the listed
alt but is closed-vocab.) Loaded once via the ModelManager. Requires the
`action` extra. Select via config: action_recognizer.model = xclip.

Scores are a softmax over the REQUESTED phrases — relative, not absolute: the
model always picks the least-bad label. Two guards turn that into a usable
signal: a confidence floor (min_confidence), and an abstention foil (NO_ACTION)
always added to the candidate set so the softmax can park probability on "none
of these" instead of forcing a wrong label onto unlisted footage. When the foil
wins, no event is emitted.
"""
from __future__ import annotations

from typing import Any, List, Sequence, Tuple

import numpy as np

from va.contracts.action import ActionEvent
from va.media.frames import frames_at
from va.roles.action_recognizer import NO_ACTION
from va.runtime.device import resolve_device
from va.runtime.manager import MANAGER

_NUM_FRAMES = 8  # what microsoft/xclip-base-patch32 was trained with


class XClipRecognizer:
    def __init__(self, load: dict[str, Any] | None = None):
        load = load or {}
        self.weights = load.get("weights", "microsoft/xclip-base-patch32")
        self.device = resolve_device(load.get("device"))
        self.min_confidence = float(load.get("min_confidence", 0.4))
        self._model, self._processor = MANAGER.get(
            f"xclip::{self.weights}", self._build
        )

    def _build(self):
        from transformers import XCLIPModel, XCLIPProcessor  # deferred heavy import

        model = XCLIPModel.from_pretrained(self.weights).to(self.device).eval()
        processor = XCLIPProcessor.from_pretrained(self.weights)
        return model, processor

    def _score_span(self, media_path: str, start: float, end: float,
                    actions: Sequence[str]) -> List[float]:
        import torch

        ts = list(np.linspace(start, max(start, end - 0.2), _NUM_FRAMES))
        frames = [np.asarray(f.convert("RGB")) for f in frames_at(media_path, ts)]
        inputs = self._processor(
            text=list(actions), videos=[frames], return_tensors="pt", padding=True
        ).to(self.device)
        with torch.no_grad():
            out = self._model(**inputs)
        logits = out.logits_per_video  # (1, n_actions)
        return logits.softmax(dim=-1)[0].tolist()

    def recognize(
        self,
        media_path: str,
        spans: Sequence[Tuple[float, float]],
        actions: Sequence[str],
    ) -> List[List[ActionEvent]]:
        if not actions:
            return [[] for _ in spans]
        # Always score the abstention foil so the softmax has somewhere to put
        # probability when no listed action fits — even if a custom vocab omits it.
        candidates = list(actions)
        if NO_ACTION not in candidates:
            candidates.append(NO_ACTION)
        out: List[List[ActionEvent]] = []
        for start, end in spans:
            probs = self._score_span(media_path, start, end, candidates)
            best = int(np.argmax(probs))
            events: List[ActionEvent] = []
            # Abstain when the foil wins: no listed action is happening here.
            if candidates[best] != NO_ACTION and probs[best] >= self.min_confidence:
                events.append(ActionEvent(
                    action_class=str(candidates[best]),
                    confidence=min(1.0, float(probs[best])),
                    start_time=start, end_time=end,
                ))
            out.append(events)
        return out
