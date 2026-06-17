"""PySceneDetect Role-1 backend — sharper content-aware detection.

Behind the same SceneDetector Protocol as the histogram backend. Requires the
`scenedetect` extra (pulls in OpenCV); import is deferred so the package runs
without it. Select via config: scene_detector.model = pyscenedetect.
"""
from __future__ import annotations

from typing import List

from va.media.frames import probe
from va.roles.scene_detector import SceneSpan


class PySceneDetectDetector:
    def __init__(self, threshold: float = 27.0):
        self.threshold = threshold

    def detect(self, video_path: str) -> List[SceneSpan]:
        from scenedetect import ContentDetector, detect  # deferred heavy import

        scene_list = detect(video_path, ContentDetector(threshold=self.threshold))
        if not scene_list:
            # Whole video is a single scene.
            duration = probe(video_path).duration_seconds or 0.0
            return [(0.0, float(duration))]
        return [(start.get_seconds(), end.get_seconds()) for start, end in scene_list]
