"""Motion-episode scene detection (WS4.b, plan §3.2 row 1) — episodes → segments.

The A-LSSRVF Role-1 backend: instead of looking at pixels, ask a MotionSource
what happened on this camera while the chunk was recording and make each merged
motion episode a segment. Downstream roles (captioner, actions) then work on
"something moved here" windows instead of one static-scene keyframe — the
transient-blindness gap measured in security-footage-spike-findings.md and
pinned by the tests/golden_queries/nvr0801_clip02* xfails.

Time mapping (plan §4 dual time model): MotionEvents are wall-clock (UTC epoch);
segments are video-relative. relative = event_epoch - context.start_epoch,
then clamped to [0, duration].

Degraded modes (deliberate, both warn):
- No context / no start_epoch (an A-EV video under this profile, or chunk
  metadata not yet attached): ONE full-span segment — the role still yields a
  temporal backbone, captions degrade to today's per-chunk behavior.
- MotionSource failure (device/network): same full-span fallback — Role 1 runs
  inside the ingest critical path and a flaky NVR must not abort the ingest.
An epoch-placed chunk with ZERO events is NOT degraded: it returns no segments
(a genuinely quiet chunk has nothing to caption).
"""
from __future__ import annotations

import logging
from typing import List, Optional

from va.media.frames import probe
from va.roles.motion_source import MotionSource, cluster_events
from va.roles.scene_detector import SceneContext, SceneSpan

logger = logging.getLogger(__name__)


class MotionEpisodeSceneDetector:
    def __init__(
        self,
        motion_source: MotionSource,
        pad_s: float = 2.0,
        gap_s: float = 30.0,
        min_span_s: float = 0.5,
    ):
        # pad_s: context margin added around each episode (motion logs clip the
        #   moment of first movement); gap_s: cluster gap forwarded to
        #   cluster_events; min_span_s: drop slivers shorter than this after
        #   clamping (an episode that barely straddles the chunk edge).
        # Structure/budget knobs, overridable via the role spec in config.
        self.motion_source = motion_source
        self.pad_s = float(pad_s)
        self.gap_s = float(gap_s)
        self.min_span_s = float(min_span_s)

    def detect(
        self, video_path: str, context: Optional[SceneContext] = None
    ) -> List[SceneSpan]:
        duration = context.duration_seconds if context else None
        if duration is None:
            duration = probe(video_path).duration_seconds or 0.0
        duration = float(duration)

        if context is None or context.start_epoch is None:
            logger.warning(
                "motion-episodes: no start_epoch for %s — cannot place the chunk "
                "on the wall clock; degrading to one full-span segment",
                video_path,
            )
            return [(0.0, duration)]

        try:
            events = self.motion_source.events(
                context.start_epoch,
                context.start_epoch + duration,
                camera_ref=context.camera_ref,
            )
        except Exception:  # noqa: BLE001 — device I/O; must not abort ingest
            logger.warning(
                "motion-episodes: MotionSource failed for %s — degrading to one "
                "full-span segment",
                video_path,
                exc_info=True,
            )
            return [(0.0, duration)]

        spans: List[SceneSpan] = []
        for ep in cluster_events(events, gap_s=self.gap_s):
            start = max(0.0, ep.start_epoch - context.start_epoch - self.pad_s)
            end = min(duration, ep.end_epoch - context.start_epoch + self.pad_s)
            if end - start >= self.min_span_s:
                spans.append((start, end))

        # Padding (and camera_ref=None pulling several cameras' episodes) can
        # re-introduce overlaps after clustering — merge to keep the Role-1
        # contract's "ordered, non-overlapping".
        spans.sort()
        merged: List[SceneSpan] = []
        for s, e in spans:
            if merged and s <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        return merged
