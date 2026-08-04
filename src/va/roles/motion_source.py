"""Role: MotionSource (WS-4, plan §3.1) — where motion windows come from.

The product must consume motion from many mechanisms (vendor event logs, ONVIF
events, push streams, own detection on live video). Callers depend only on this
Protocol; adapters in `va.adapters.motion_source` implement it per mechanism —
mirroring the role-adapter pattern of every other role. The vendor-events family
is the default philosophy (cheap, no video decode, retrospective); own-detection
is the universal fallback (see the plan's §3.1 comparison).
"""
from __future__ import annotations

from typing import List, Optional, Protocol

from va.contracts.motion import MotionEvent


class MotionSource(Protocol):
    def events(
        self,
        start_epoch: float,
        end_epoch: float,
        camera_ref: Optional[str] = None,
    ) -> List[MotionEvent]:
        """Motion windows overlapping [start_epoch, end_epoch] (UTC epoch
        seconds), oldest first. `camera_ref` filters to one source-native
        camera; None = all cameras."""
        ...


def cluster_events(
    events: List[MotionEvent], gap_s: float = 30.0
) -> List[MotionEvent]:
    """Merge same-camera events whose gaps are <= gap_s into episodes.

    Vendor logs are chatty (the LNR608 emits many entries per minute overnight —
    nvr-access-notes.md §5b), and pulling one clip per raw entry would fragment
    the footage into confetti. Clustering is a pure function here — every
    adapter's output can be clustered the same way. Events merge only within a
    camera; the merged event keeps the first event's kind/attributes and spans
    min(start)..max(end). Input order does not matter; output is oldest-first
    per camera, cameras interleaved by time.
    """
    by_cam: dict[str, list[MotionEvent]] = {}
    for e in events:
        by_cam.setdefault(e.camera_ref, []).append(e)
    merged: list[MotionEvent] = []
    for cam_events in by_cam.values():
        cam_events.sort(key=lambda e: e.start_epoch)
        cur = cam_events[0]
        for e in cam_events[1:]:
            if e.start_epoch - cur.end_epoch <= gap_s:
                if e.end_epoch > cur.end_epoch:
                    cur = cur.model_copy(update={"end_epoch": e.end_epoch})
            else:
                merged.append(cur)
                cur = e
        merged.append(cur)
    merged.sort(key=lambda e: (e.start_epoch, e.camera_ref))
    return merged
