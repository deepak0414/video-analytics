"""Sidecar stub MotionSource — deterministic, dependency-free, for tests/CI.

Reads motion windows from a JSON file (`events_file` in the role spec):

    {"events": [{"camera_ref": "2", "start_epoch": 100.0, "end_epoch": 130.0}]}

so the whole motion-gated flow is testable offline with assertable windows —
the same sidecar pattern every other stub role uses. A CONFIGURED-but-missing
file yields no events (a camera with a quiet day), never an error; an
UNCONFIGURED sidecar (no `events_file` in the spec) warns — since WS4.b its
silent [] would turn every epoch-placed chunk into zero segments, making a
config gap indistinguishable from a genuinely quiet chunk.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from va.contracts.motion import MotionEvent

logger = logging.getLogger(__name__)


class SidecarMotionSource:
    def __init__(self, events_file: str | Path | None = None):
        self.events_file = Path(events_file) if events_file else None

    def events(
        self,
        start_epoch: float,
        end_epoch: float,
        camera_ref: Optional[str] = None,
    ) -> List[MotionEvent]:
        if self.events_file is None:
            logger.warning(
                "sidecar MotionSource queried with no events_file configured — "
                "returning no events; real footage needs motion_source: "
                "lnr-eventlog (or an events_file for tests)"
            )
            return []
        if not self.events_file.exists():
            return []
        doc = json.loads(self.events_file.read_text())
        out = []
        for raw in doc.get("events", []):
            e = MotionEvent(**raw)
            if e.end_epoch < start_epoch or e.start_epoch > end_epoch:
                continue
            if camera_ref is not None and e.camera_ref != camera_ref:
                continue
            out.append(e)
        out.sort(key=lambda e: e.start_epoch)
        return out
