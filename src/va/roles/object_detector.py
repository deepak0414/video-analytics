"""Role 5 — Object Detector (the contract).

Open-vocabulary: callers pass the class names to look for as text; the detector
returns labeled boxes per image. Backends (color stub, YOLO-World,
GroundingDINO later) are interchangeable.
"""
from __future__ import annotations

from typing import List, Protocol, Sequence, runtime_checkable

from PIL import Image

from va.contracts.detection import Detection

# Common objects detected at ingest time (architecture: "always-on for common
# objects, on-demand for specific ones"). Overridable via roles.yaml `classes:`.
DEFAULT_INGEST_CLASSES = [
    "person", "car", "truck", "bicycle", "motorcycle", "bus",
    "dog", "cat", "bird", "horse",
    "chair", "table", "laptop", "phone", "bottle", "cup",
]


@runtime_checkable
class ObjectDetector(Protocol):
    def detect(
        self, images: Sequence[Image.Image], classes: Sequence[str]
    ) -> List[List[Detection]]:
        """Per input image, the detections among `classes` (video_id/timestamp
        left unset — the pipeline fills them)."""
        ...
