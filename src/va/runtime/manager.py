"""ModelManager — singleton cache + lifecycle for loaded models.

In-process adapters never load weights directly; they call
`MANAGER.get(key, build)`. The manager guarantees a model is built once and
reused (so SigLIP isn't loaded twice), and can `unload()` to free memory.

For the DGX Spark profile (`residency: keep`) models stay resident. A 24GB
profile would call `unload()` between roles; that policy lives in the caller,
the manager just provides the mechanism.
"""
from __future__ import annotations

import threading
from typing import Any, Callable


class ModelManager:
    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()

    def get(self, key: str, build: Callable[[], Any]) -> Any:
        """Return the cached model for `key`, building it once on first use."""
        # Double-checked locking so concurrent first-callers build only once.
        if key in self._cache:
            return self._cache[key]
        with self._lock:
            if key not in self._cache:
                self._cache[key] = build()
            return self._cache[key]

    def unload(self, key: str) -> bool:
        """Drop a model from the cache. Returns True if something was removed."""
        with self._lock:
            existed = self._cache.pop(key, None) is not None
        if existed:
            self._free_accelerator_memory()
        return existed

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
        self._free_accelerator_memory()

    def loaded(self) -> list[str]:
        return list(self._cache)

    @staticmethod
    def _free_accelerator_memory() -> None:
        import gc

        gc.collect()
        try:
            import torch  # type: ignore

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


# Process-wide singleton. (Per-GPU sharding is a future concern — see plan §8.)
MANAGER = ModelManager()
