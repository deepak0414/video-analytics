"""Motion stub recognizer — deterministic, dependency-free, for the test suite.

Real action recognition needs a video model; synthetic test clips have no
semantic actions. What they DO have is motion (write_boxes_video drift), and
motion-vs-static is the one temporal property computable from pixel diffs
alone. So this stub "recognizes" exactly two literal action names when they are
requested: "motion" (mean inter-frame difference above threshold) and
"static scene" (below). Mirrors the color-detector semantics: tests request a
vocabulary the stub can honestly ground. X-CLIP provides real recognition.
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

import numpy as np

from va.contracts.action import ActionEvent
from va.media.frames import frames_at

_SAMPLES_PER_SPAN = 4
_MOTION_THRESHOLD = 0.01  # mean abs diff as fraction of full scale (255)


class MotionRecognizer:
    def recognize(
        self,
        media_path: str,
        spans: Sequence[Tuple[float, float]],
        actions: Sequence[str],
    ) -> List[List[ActionEvent]]:
        wanted = {a.lower() for a in actions}
        out: List[List[ActionEvent]] = []
        for start, end in spans:
            ts = list(np.linspace(start, max(start, end - 0.2), _SAMPLES_PER_SPAN))
            frames = [np.asarray(f.convert("L"), dtype=np.float32)
                      for f in frames_at(media_path, ts)]
            diffs = [float(np.abs(a - b).mean()) / 255.0
                     for a, b in zip(frames, frames[1:])]
            score = max(diffs) if diffs else 0.0

            events: List[ActionEvent] = []
            if score >= _MOTION_THRESHOLD and "motion" in wanted:
                events.append(ActionEvent(
                    action_class="motion",
                    confidence=min(1.0, 0.5 + 10.0 * score),
                    start_time=start, end_time=end,
                ))
            elif score < _MOTION_THRESHOLD and "static scene" in wanted:
                events.append(ActionEvent(
                    action_class="static scene", confidence=0.9,
                    start_time=start, end_time=end,
                ))
            out.append(events)
        return out
