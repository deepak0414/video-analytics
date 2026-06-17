"""Resolve the device/dtype a model should load on, honoring the profile but
degrading gracefully if the requested accelerator isn't present."""
from __future__ import annotations

from typing import Any


def resolve_device(requested: str | None) -> str:
    """Map a requested device to one that actually exists.

    'cuda' falls back to 'cpu' when torch/CUDA is unavailable, so the same
    config runs on the Spark and on a laptop/CI box.
    """
    req = (requested or "cpu").lower()
    if req == "cpu":
        return "cpu"
    try:
        import torch  # type: ignore

        if req.startswith("cuda") and torch.cuda.is_available():
            return req
    except Exception:
        pass
    return "cpu"


def resolve(load: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized copy of load params with device resolved."""
    out = dict(load)
    out["device"] = resolve_device(load.get("device"))
    return out
