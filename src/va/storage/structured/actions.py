"""ActionStore — write/search the `action_events` table.

Shares the central correlation DB (keyed by video_id, one row per recognized
action per segment). Search is word-overlap on the action label, same family
as transcripts/OCR: "eating" finds 'eating' events; ranking by overlap then
confidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from uuid import UUID

from va.contracts.action import ActionEvent
from va.storage.structured.schema import connect


@dataclass
class ActionHit:
    video_id: UUID
    action_class: str
    confidence: float
    start_time: float
    end_time: float
    score: float


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", s.lower()))


class ActionStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._conn = connect(self.path)

    def close(self) -> None:
        self._conn.close()

    def replace_events(self, video_id: UUID, events: List[ActionEvent]) -> None:
        """Idempotent: clear this video's action events, then insert."""
        self._conn.execute("DELETE FROM action_events WHERE video_id = ?", (str(video_id),))
        self._conn.executemany(
            "INSERT INTO action_events (video_id, segment_id, action_class, confidence, "
            "start_time, end_time) VALUES (?, ?, ?, ?, ?, ?)",
            [(str(video_id), str(e.segment_id) if e.segment_id else None,
              e.action_class, e.confidence, e.start_time, e.end_time) for e in events],
        )
        self._conn.commit()

    def count(self, video_id: UUID) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM action_events WHERE video_id = ?", (str(video_id),)
        ).fetchone()[0]

    def search(self, query: str, video_id: Optional[UUID] = None, k: int = 10) -> List[ActionHit]:
        """Rank events by fraction of query words present in the action label."""
        q_tokens = _tokens(query)
        if not q_tokens:
            return []
        sql = ("SELECT video_id, action_class, confidence, start_time, end_time "
               "FROM action_events")
        params: list = []
        if video_id is not None:
            sql += " WHERE video_id = ?"
            params.append(str(video_id))
        rows = self._conn.execute(sql, params).fetchall()

        hits: List[ActionHit] = []
        for r in rows:
            overlap = q_tokens & _tokens(r["action_class"])
            if not overlap:
                continue
            hits.append(ActionHit(
                video_id=UUID(r["video_id"]), action_class=r["action_class"],
                confidence=r["confidence"] or 0.0,
                start_time=r["start_time"] or 0.0, end_time=r["end_time"] or 0.0,
                score=len(overlap) / len(q_tokens),
            ))
        hits.sort(key=lambda h: (-h.score, -h.confidence, h.start_time))
        return hits[:k]
