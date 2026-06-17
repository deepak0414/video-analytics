"""IoU-association tracker — the default Role-6 backend.

Greedy IoU matching of same-class detections across consecutive sampled frames
(the core idea of SORT/ByteTrack without the Kalman motion model). Dependency-
free and deterministic, so it doubles as the offline test backend — but it is a
legitimate lightweight tracker for slow-moving/static objects at PoC sampling
rates. ByteTrack (motion-aware) is the heavier alternative behind the same
Protocol.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence
from uuid import UUID, uuid4

from va.contracts.detection import Detection
from va.contracts.track import ObjectTrack, TrackingResult
from va.roles.object_tracker import FrameDetections


def _iou(a: Detection, b: Detection) -> float:
    ax0, ay0, ax1, ay1 = a.bbox_x, a.bbox_y, a.bbox_x + a.bbox_w, a.bbox_y + a.bbox_h
    bx0, by0, bx1, by1 = b.bbox_x, b.bbox_y, b.bbox_x + b.bbox_w, b.bbox_y + b.bbox_h
    ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    union = a.bbox_w * a.bbox_h + b.bbox_w * b.bbox_h - inter
    return inter / union if union > 0 else 0.0


@dataclass
class _Active:
    track_id: UUID
    object_class: str
    last_det: Detection
    last_ts: float
    first_seen: float
    frames: int = 1
    max_conf: float = 0.0
    timestamps: set = field(default_factory=set)


class IouTracker:
    def __init__(self, iou_threshold: float = 0.3, max_gap_seconds: float = 2.5):
        self.iou_threshold = iou_threshold
        self.max_gap = max_gap_seconds

    def track(
        self, video_id: UUID, frames: Sequence[FrameDetections]
    ) -> TrackingResult:
        active: List[_Active] = []
        out_dets: List[Detection] = []

        for ts, dets in sorted(frames, key=lambda f: f[0]):
            # Expire tracks that haven't been seen recently.
            live = [t for t in active if ts - t.last_ts <= self.max_gap]
            claimed: set[int] = set()
            for det in sorted(dets, key=lambda d: -d.confidence):
                best_i, best_iou = -1, self.iou_threshold
                for i, tr in enumerate(live):
                    if i in claimed or tr.object_class != det.object_class:
                        continue
                    iou = _iou(det, tr.last_det)
                    if iou >= best_iou:
                        best_i, best_iou = i, iou
                if best_i >= 0:
                    tr = live[best_i]
                    claimed.add(best_i)
                    tr.last_det, tr.last_ts = det, ts
                    tr.max_conf = max(tr.max_conf, det.confidence)
                    if ts not in tr.timestamps:
                        tr.timestamps.add(ts)
                        tr.frames += 1
                    track_id = tr.track_id
                else:
                    tr = _Active(
                        track_id=uuid4(), object_class=det.object_class,
                        last_det=det, last_ts=ts, first_seen=ts,
                        max_conf=det.confidence, timestamps={ts},
                    )
                    active.append(tr)
                    track_id = tr.track_id
                out_dets.append(det.model_copy(
                    update={"video_id": video_id, "timestamp": ts, "track_id": track_id}
                ))

        tracks = [
            ObjectTrack(
                id=t.track_id, video_id=video_id, object_class=t.object_class,
                track_confidence=t.max_conf, first_seen=t.first_seen,
                last_seen=t.last_ts, frame_count=t.frames,
            )
            for t in active
        ]
        return TrackingResult(tracks=tracks, detections=out_dets)
