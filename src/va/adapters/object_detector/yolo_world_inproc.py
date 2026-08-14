"""Real Role-5 backend: YOLO-World (open-vocabulary) via ultralytics.

set_classes() primes the detector with the requested vocabulary; predictions
come back with normalized xyxy boxes. Loaded once per weights+device via the
ModelManager. Requires the `yolo` extra. Select via config:
object_detector.model = yolo-world.
"""
from __future__ import annotations

import logging

from typing import Any, List, Sequence

from PIL import Image

from va.contracts.detection import Detection
from va.runtime.device import resolve_device
from va.runtime.manager import MANAGER


class YoloWorldDetector:
    def __init__(self, load: dict[str, Any] | None = None):
        load = load or {}
        self.weights = load.get("weights", "yolov8s-world.pt")
        self.device = resolve_device(load.get("device"))
        self.conf = float(load.get("conf", 0.25))
        self._key = f"yoloworld::{self.weights}"
        self._model = MANAGER.get(self._key, self._build)

    def _build(self):
        from ultralytics import YOLO  # deferred heavy import

        return YOLO(self.weights)

    def _prime(self, wanted: tuple[str, ...]) -> None:
        """Prime the shared model's vocabulary, tracked ON THE MODEL so it happens once per
        process across the fresh adapter instances get_object_detector() builds per video.

        Re-priming a model that has already run inference can raise a CLIP text-encode CPU/CUDA
        device mismatch (the CPU-tokenized prompt vs the CUDA-resident text encoder). That is
        reachable whenever footage profiles present DIFFERENT vocabularies to one long-lived
        process (the `va serve` durable job queue, or a `watch`/`reprocess` pass over mixed-
        profile videos). Recover by evicting and rebuilding the model — a fresh model primes
        cleanly — so a vocabulary change is survivable rather than a silent Role 5+6 skip. A
        change reloads weights, but vocab changes are rare and correctness outweighs the reload.

        Live-validated 2026-08-13 on the real yolo-world backend: in one process a vocab change
        reliably triggers the mismatch, evict+rebuild recovers with no propagated crash, and the
        rebuilt model detects correctly (a car was detected before and after two rebuilds).
        """
        rebuild = False
        try:
            self._model.set_classes(list(wanted))
        except Exception as e:  # noqa: BLE001 — re-prime device mismatch (or memory pressure)
            # Not silent: a rebuild is a full weights reload (a per-change cost worth seeing), and
            # a non-device-mismatch failure (OOM, corrupt weights) surfaces here identically — log
            # the swallowed cause and the vocab transition before rebuilding so both are visible.
            prev = getattr(self._model, "_va_primed_classes", None)
            # Log str(e), NOT e: a retaining log handler (pytest caplog, a MemoryHandler, a
            # breadcrumb/aggregator) keeps the LogRecord — and passing the exception object stores
            # it in record.args, whose traceback pins the evicted model's `self` frame, defeating the
            # free-before-rebuild below. The message string carries the same information, no traceback.
            logging.getLogger(__name__).warning(
                "YOLO-World re-prime failed (%s: %s); rebuilding model to change vocabulary "
                "%s -> %s", type(e).__name__, str(e), list(prev) if prev else None, list(wanted))
            rebuild = True
        if rebuild:
            # Rebuild OUTSIDE the except block. While `e` is alive its traceback strongly pins the
            # evicted model — the failing set_classes frame's `self` IS that model — so dropping the
            # reference inside the handler frees nothing (both copies stay resident through the
            # rebuild). Once the except block has exited, `e` and its traceback are gone, so
            # unload's gc/empty_cache can actually reclaim the old weights before MANAGER.get loads
            # the replacement — otherwise a failure that was itself memory pressure re-OOMs here.
            self._model = None
            MANAGER.unload(self._key)
            self._model = MANAGER.get(self._key, self._build)
            self._model.set_classes(list(wanted))
        self._model._va_primed_classes = wanted

    def detect(
        self, images: Sequence[Image.Image], classes: Sequence[str]
    ) -> List[List[Detection]]:
        wanted = tuple(c.lower() for c in classes)
        # Prime only when the shared model's vocabulary actually differs (marker lives on the
        # model, so it holds across the per-video adapter instances). Same vocab across a batch
        # (the common A-LSSRVF case) primes once; a vocabulary change re-primes survivably.
        if getattr(self._model, "_va_primed_classes", None) != wanted:
            self._prime(wanted)

        results = self._model.predict(
            [im.convert("RGB") for im in images],
            conf=self.conf, device=self.device, verbose=False,
        )
        out: List[List[Detection]] = []
        for res in results:
            dets: List[Detection] = []
            names = res.names  # idx -> class name for the primed vocabulary
            for box in res.boxes:
                x0, y0, x1, y1 = (float(v) for v in box.xyxyn[0])
                dets.append(Detection(
                    object_class=str(names[int(box.cls[0])]),
                    confidence=float(box.conf[0]),
                    bbox_x=max(0.0, x0), bbox_y=max(0.0, y0),
                    bbox_w=max(0.0, x1 - x0), bbox_h=max(0.0, y1 - y0),
                ))
            out.append(dets)
        return out
