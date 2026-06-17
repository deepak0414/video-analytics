"""Role (Retrieval Layer SR.6) — VLM Verifier (the contract).

Query-time visual grounding: given a frame and a natural-language claim, decide
whether the frame actually supports the claim. This is the *visual* analogue of
the SR.3 cross-encoder reranker — it catches what a bi-encoder (SigLIP) and a
fixed-vocabulary detector (YOLO-World) structurally cannot: attribute binding
("blue" vs "red"), composition/action ("feeding a mouse to the snake"), and
novel/camouflaged objects ("snake"). A VLM (Qwen2.5-VL) reasons over the pixels
and answers, so it overturns a SigLIP false-positive or confirms a YOLO miss.

Used to RE-RANK/FILTER a cheap retriever's top-k candidates (never to scan a
whole video — that is the deep-scan's job). Backends are interchangeable: a no-op
`passthrough` stub (offline/CI; `enabled=False` so callers skip all frame work)
and the real Qwen backend. `enabled` lets a caller cheaply detect the no-op and
avoid loading frames at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from PIL import Image


@dataclass
class Verdict:
    """A 3-way judgement so each call site picks its own SAFE default:

    - reranking keeps a hit unless the VLM is a confident NO  (`accept`) — so a
      hedge never drops a true positive;
    - presence counts a frame only on a confident YES        (`present`) — so a
      hedge never fabricates a detection.
    """

    label: str = "unsure"   # "yes" | "no" | "unsure"
    detail: str = ""        # the VLM's short rationale (for evidence/debugging)

    @property
    def accept(self) -> bool:    # keep unless confidently negative
        return self.label != "no"

    @property
    def present(self) -> bool:   # count only when confidently positive
        return self.label == "yes"


@runtime_checkable
class VlmVerifier(Protocol):
    enabled: bool         # False for the no-op stub -> callers skip frame I/O

    def verify(self, image: Image.Image, claim: str) -> Verdict:
        """Does `image` support `claim`? CONSERVATIVE by contract: a real backend
        returns accept=False ONLY when it is confidently negative; anything
        ambiguous accepts (keeps), so verification can only overturn a retriever
        when the VLM is sure — it must not silently drop true positives."""
        ...
