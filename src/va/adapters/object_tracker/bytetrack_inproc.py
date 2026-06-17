"""ByteTrack Role-6 backend via the `supervision` library (Kalman motion model;
better association for moving objects than plain IoU). Requires the `track`
extra. Select via config: object_tracker.model = bytetrack.

supervision works in pixel space; normalized boxes are scaled by a nominal
frame size (association is scale-invariant).
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence
from uuid import UUID, uuid4

from va.contracts.detection import Detection
from va.contracts.track import ObjectTrack, TrackingResult
from va.roles.object_tracker import FrameDetections

_NOMINAL = 1000.0  # virtual frame size for normalized->pixel conversion


class ByteTrackTracker:
    def __init__(self, load: dict[str, Any] | None = None):
        load = load or {}
        self.frame_rate = float(load.get("frame_rate", 1.0))  # sampling fps

    def track(
        self, video_id: UUID, frames: Sequence[FrameDetections]
    ) -> TrackingResult:
        import numpy as np
        import supervision as sv

        tracker = sv.ByteTrack(frame_rate=max(1, int(self.frame_rate)))
        classes: List[str] = []
        class_idx: Dict[str, int] = {}
        ext_ids: Dict[int, UUID] = {}          # supervision tracker_id -> our UUID
        agg: Dict[UUID, dict] = {}
        out_dets: List[Detection] = []

        for ts, dets in sorted(frames, key=lambda f: f[0]):
            if not dets:
                continue
            for d in dets:
                if d.object_class not in class_idx:
                    class_idx[d.object_class] = len(classes)
                    classes.append(d.object_class)
            xyxy = np.array([
                [d.bbox_x * _NOMINAL, d.bbox_y * _NOMINAL,
                 (d.bbox_x + d.bbox_w) * _NOMINAL, (d.bbox_y + d.bbox_h) * _NOMINAL]
                for d in dets
            ], dtype=np.float32)
            sv_dets = sv.Detections(
                xyxy=xyxy,
                confidence=np.array([d.confidence for d in dets], dtype=np.float32),
                class_id=np.array([class_idx[d.object_class] for d in dets]),
            )
            tracked = tracker.update_with_detections(sv_dets)
            for i in range(len(tracked)):
                tid = int(tracked.tracker_id[i])
                uid = ext_ids.setdefault(tid, uuid4())
                cls = classes[int(tracked.class_id[i])]
                conf = float(tracked.confidence[i])
                x0, y0, x1, y1 = (float(v) / _NOMINAL for v in tracked.xyxy[i])
                out_dets.append(Detection(
                    video_id=video_id, timestamp=ts, object_class=cls,
                    confidence=min(1.0, max(0.0, conf)), track_id=uid,
                    bbox_x=min(max(x0, 0.0), 1.0), bbox_y=min(max(y0, 0.0), 1.0),
                    bbox_w=min(max(x1 - x0, 0.0), 1.0), bbox_h=min(max(y1 - y0, 0.0), 1.0),
                ))
                a = agg.setdefault(uid, {
                    "cls": cls, "first": ts, "last": ts, "frames": 0, "conf": 0.0,
                    "seen": set(),
                })
                a["last"] = ts
                a["conf"] = max(a["conf"], conf)
                if ts not in a["seen"]:
                    a["seen"].add(ts)
                    a["frames"] += 1

        tracks = [
            ObjectTrack(
                id=uid, video_id=video_id, object_class=a["cls"],
                track_confidence=min(1.0, a["conf"]), first_seen=a["first"],
                last_seen=a["last"], frame_count=a["frames"],
            )
            for uid, a in agg.items()
        ]
        return TrackingResult(tracks=tracks, detections=out_dets)
