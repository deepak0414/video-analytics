"""TrackStore — write/query the `object_tracks` table.

The distinct-count query lives here: COUNT of tracks per class = "how many
distinct X appeared" (each track is one followed object instance).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence
from uuid import UUID

from va.contracts.track import ObjectTrack
from va.storage.structured.schema import connect


@dataclass
class DistinctCount:
    video_id: UUID
    object_class: str
    distinct: int          # number of tracks = distinct object instances
    first_seen: float
    last_seen: float


class TrackStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._conn = connect(self.path)

    def close(self) -> None:
        self._conn.close()

    def replace_tracks(self, video_id: UUID, tracks: List[ObjectTrack]) -> None:
        """Idempotent: clear this video's tracks, then insert."""
        self._conn.execute("DELETE FROM object_tracks WHERE video_id = ?", (str(video_id),))
        self._conn.executemany(
            "INSERT INTO object_tracks (id, video_id, object_class, track_confidence, "
            "first_seen, last_seen, frame_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (str(t.id), str(t.video_id), t.object_class, t.track_confidence,
                 t.first_seen, t.last_seen, t.frame_count)
                for t in tracks
            ],
        )
        self._conn.commit()

    def count(self, video_id: UUID) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM object_tracks WHERE video_id = ?", (str(video_id),)
        ).fetchone()[0]

    def get_tracks(self, video_id: UUID) -> List[ObjectTrack]:
        rows = self._conn.execute(
            "SELECT * FROM object_tracks WHERE video_id = ? ORDER BY first_seen",
            (str(video_id),),
        ).fetchall()
        return [
            ObjectTrack(
                id=UUID(r["id"]), video_id=UUID(r["video_id"]),
                object_class=r["object_class"], track_confidence=r["track_confidence"],
                first_seen=r["first_seen"], last_seen=r["last_seen"],
                frame_count=r["frame_count"],
            )
            for r in rows
        ]

    def distinct_counts(
        self, classes: Sequence[str], video_id: Optional[UUID] = None,
        min_frames: int = 1,
    ) -> List[DistinctCount]:
        """Distinct object instances per video & class ("how many different X").

        min_frames filters single-frame tracks (often detector flicker)."""
        if not classes:
            return []
        marks = ", ".join("?" for _ in classes)
        sql = (
            "SELECT video_id, object_class, COUNT(*) AS n, "
            "MIN(first_seen) AS first_seen, MAX(last_seen) AS last_seen "
            "FROM object_tracks "
            f"WHERE lower(object_class) IN ({marks}) AND frame_count >= ?"
        )
        params: list = [c.lower() for c in classes] + [min_frames]
        if video_id is not None:
            sql += " AND video_id = ?"
            params.append(str(video_id))
        sql += " GROUP BY video_id, object_class ORDER BY n DESC"
        return [
            DistinctCount(
                video_id=UUID(r["video_id"]), object_class=r["object_class"],
                distinct=r["n"], first_seen=r["first_seen"], last_seen=r["last_seen"],
            )
            for r in self._conn.execute(sql, params).fetchall()
        ]
