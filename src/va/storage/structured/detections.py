"""DetectionStore — write/query the `object_detections` table.

Shares the central correlation DB (keyed by video_id + timestamp). Until Role 6
adds tracks, counts are per-frame appearances, not distinct objects — summaries
say so explicitly.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence
from uuid import UUID

from va.contracts.detection import Detection
from va.storage.structured.schema import apply_schema


@dataclass
class ObjectSummary:
    video_id: UUID
    object_class: str
    frames: int            # frames the class appears in (NOT distinct objects)
    first_seen: float
    last_seen: float
    max_confidence: float


@dataclass
class CoOccurrence:
    """A time window where ALL requested classes appear simultaneously."""

    video_id: UUID
    classes: tuple
    time_start: float
    time_end: float
    frames: int


class DetectionStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        apply_schema(self._conn)

    def close(self) -> None:
        self._conn.close()

    def replace_detections(self, video_id: UUID, detections: List[Detection]) -> None:
        """Idempotent: clear this video's detections, then insert."""
        self._conn.execute(
            "DELETE FROM object_detections WHERE video_id = ?", (str(video_id),)
        )
        self._conn.executemany(
            "INSERT INTO object_detections (video_id, timestamp, track_id, object_class, "
            "bbox_x, bbox_y, bbox_w, bbox_h, confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (str(video_id), d.timestamp, str(d.track_id) if d.track_id else None,
                 d.object_class, d.bbox_x, d.bbox_y, d.bbox_w, d.bbox_h, d.confidence)
                for d in detections
            ],
        )
        self._conn.commit()

    def count(self, video_id: UUID) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM object_detections WHERE video_id = ?", (str(video_id),)
        ).fetchone()[0]

    def existing_classes(self) -> list[str]:
        return [r[0] for r in self._conn.execute(
            "SELECT DISTINCT lower(object_class) FROM object_detections"
        ).fetchall()]

    def co_occurrence(
        self, classes: Sequence[str], video_id: Optional[UUID] = None,
        max_gap_seconds: float = 2.0,
    ) -> List[CoOccurrence]:
        """Time windows where ALL `classes` are detected at the same timestamp —
        the temporal join behind queries like "person AT the car". Consecutive
        qualifying timestamps (gap <= max_gap_seconds) merge into one window."""
        wanted = sorted({c.lower() for c in classes})
        if len(wanted) < 2:
            return []
        marks = ", ".join("?" for _ in wanted)
        sql = (
            "SELECT video_id, timestamp FROM object_detections "
            f"WHERE lower(object_class) IN ({marks}) "
        )
        params: list = list(wanted)
        if video_id is not None:
            sql += "AND video_id = ? "
            params.append(str(video_id))
        sql += "GROUP BY video_id, timestamp HAVING COUNT(DISTINCT lower(object_class)) = ?"
        params.append(len(wanted))

        by_video: dict[str, list[float]] = {}
        for r in self._conn.execute(sql, params).fetchall():
            by_video.setdefault(r["video_id"], []).append(r["timestamp"])

        out: List[CoOccurrence] = []
        for vid, stamps in by_video.items():
            stamps.sort()
            start, prev, n = stamps[0], stamps[0], 1
            for ts in stamps[1:]:
                if ts - prev <= max_gap_seconds:
                    prev, n = ts, n + 1
                else:
                    out.append(CoOccurrence(UUID(vid), tuple(wanted), start, prev, n))
                    start, prev, n = ts, ts, 1
            out.append(CoOccurrence(UUID(vid), tuple(wanted), start, prev, n))
        out.sort(key=lambda c: -c.frames)
        return out

    def summarize(
        self, classes: Sequence[str], video_id: Optional[UUID] = None
    ) -> List[ObjectSummary]:
        """Per video & class: frame-appearance count + first/last timestamps."""
        if not classes:
            return []
        marks = ", ".join("?" for _ in classes)
        sql = (
            "SELECT video_id, object_class, COUNT(DISTINCT timestamp) AS frames, "
            "MIN(timestamp) AS first_seen, MAX(timestamp) AS last_seen, "
            "MAX(confidence) AS max_conf FROM object_detections "
            f"WHERE lower(object_class) IN ({marks})"
        )
        params: list = [c.lower() for c in classes]
        if video_id is not None:
            sql += " AND video_id = ?"
            params.append(str(video_id))
        sql += " GROUP BY video_id, object_class ORDER BY frames DESC"
        return [
            ObjectSummary(
                video_id=UUID(r["video_id"]), object_class=r["object_class"],
                frames=r["frames"], first_seen=r["first_seen"],
                last_seen=r["last_seen"], max_confidence=r["max_conf"],
            )
            for r in self._conn.execute(sql, params).fetchall()
        ]
