"""SegmentStore — read/write the `segments` table (the temporal backbone).

Shares the same SQLite DB as the catalog (the central correlation store). Other
roles later read these segments to attach captions/actions/keyframes.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from uuid import UUID, uuid4

from va.contracts.segment import Segment
from va.storage.structured.schema import apply_schema


@dataclass
class CaptionHit:
    video_id: UUID
    segment_index: int
    start_time: float
    end_time: float
    caption: str
    score: float


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", s.lower()))


class SegmentStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        apply_schema(self._conn)

    def close(self) -> None:
        self._conn.close()

    def replace_segments(self, video_id: UUID, segments: List[Segment]) -> None:
        """Idempotent write: clear this video's segments, then insert. Re-ingesting
        a video never leaves stale or duplicated segments."""
        self._conn.execute("DELETE FROM segments WHERE video_id = ?", (str(video_id),))
        self._conn.executemany(
            "INSERT INTO segments (id, video_id, segment_index, start_time, end_time, "
            "keyframe_paths, caption) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (str(s.id), str(s.video_id), s.segment_index, s.start_time, s.end_time,
                 json.dumps(s.keyframe_paths), s.caption)
                for s in segments
            ],
        )
        self._conn.commit()

    def get_segments(self, video_id: UUID) -> List[Segment]:
        rows = self._conn.execute(
            "SELECT * FROM segments WHERE video_id = ? ORDER BY segment_index",
            (str(video_id),),
        ).fetchall()
        # Defensive: SQLite TEXT PRIMARY KEY permits NULL (the famous quirk) and
        # a NULL id was observed in the wild (crashed deep-scan sampling via
        # UUID(None)). Synthesize an id rather than crash the read path.
        return [
            Segment(
                id=UUID(r["id"]) if r["id"] else uuid4(),
                video_id=UUID(r["video_id"]),
                segment_index=r["segment_index"],
                start_time=r["start_time"],
                end_time=r["end_time"],
                keyframe_paths=json.loads(r["keyframe_paths"]) if r["keyframe_paths"] else [],
                caption=r["caption"],
            )
            for r in rows
        ]

    def count(self, video_id: UUID) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM segments WHERE video_id = ?", (str(video_id),)
        ).fetchone()[0]

    def set_caption(self, segment_id: UUID, caption: str) -> None:
        self._conn.execute(
            "UPDATE segments SET caption = ? WHERE id = ?", (caption, str(segment_id))
        )
        self._conn.commit()

    def search_captions(self, query: str, video_id: Optional[UUID] = None, k: int = 10) -> List[CaptionHit]:
        """Rank captioned segments by query-word overlap (Role 4 search)."""
        q = _tokens(query)
        if not q:
            return []
        sql = "SELECT video_id, segment_index, start_time, end_time, caption FROM segments WHERE caption IS NOT NULL"
        params: list = []
        if video_id is not None:
            sql += " AND video_id = ?"
            params.append(str(video_id))
        hits: List[CaptionHit] = []
        for r in self._conn.execute(sql, params).fetchall():
            overlap = q & _tokens(r["caption"])
            if not overlap:
                continue
            hits.append(CaptionHit(
                video_id=UUID(r["video_id"]), segment_index=r["segment_index"],
                start_time=r["start_time"], end_time=r["end_time"],
                caption=r["caption"], score=len(overlap) / len(q),
            ))
        hits.sort(key=lambda h: (-h.score, h.start_time))
        return hits[:k]
