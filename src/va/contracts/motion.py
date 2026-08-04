"""Motion events — what a MotionSource yields (WS-4, plan §3.1).

A motion event is a wall-clock window on one camera during which the source
(vendor event log, ONVIF, own detection) believes something moved. It is the
gating primitive: ingest pulls and processes ONLY these windows. Evolution-
tolerant by the runtime-contract rules: defaults everywhere, extra="allow",
source-specific payload in `attributes`.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MotionEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    # Source-native camera reference (e.g. the NVR's DISPLAY channel number as a
    # string). Mapping to our `cameras.id` happens at ingest wiring (WS4.c) —
    # the source reports what the device said, nothing more.
    camera_ref: str = ""
    start_epoch: float = 0.0   # UTC epoch seconds
    end_epoch: float = 0.0     # UTC epoch seconds; == start_epoch when unknown
    kind: str = "motion"       # event type as reported (normalized lowercase)
    attributes: dict[str, Any] = Field(default_factory=dict)
