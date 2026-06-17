"""Real Role-5 backend: YOLO-World (open-vocabulary) via ultralytics.

set_classes() primes the detector with the requested vocabulary; predictions
come back with normalized xyxy boxes. Loaded once per weights+device via the
ModelManager. Requires the `yolo` extra. Select via config:
object_detector.model = yolo-world.
"""
from __future__ import annotations

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
        self._model = MANAGER.get(f"yoloworld::{self.weights}", self._build)
        self._classes: tuple[str, ...] = ()

    def _build(self):
        from ultralytics import YOLO  # deferred heavy import

        return YOLO(self.weights)

    def detect(
        self, images: Sequence[Image.Image], classes: Sequence[str]
    ) -> List[List[Detection]]:
        wanted = tuple(c.lower() for c in classes)
        if wanted != self._classes:
            self._model.set_classes(list(wanted))
            self._classes = wanted

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
