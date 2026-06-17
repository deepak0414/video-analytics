"""TranscriptStore — write/search the `transcripts` table.

Shares the central correlation DB (keyed by video_id). Search is a simple
word-overlap match for the PoC (the architecture's "full-text index" becomes
Elasticsearch/Typesense later); good enough for "when did they say X".
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from uuid import UUID

from va.contracts.transcript import TranscriptLine
from va.storage.structured.schema import apply_schema


@dataclass
class TranscriptHit:
    video_id: UUID
    start_time: float
    end_time: float
    text: str
    speaker: Optional[str]
    score: float


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", s.lower()))


class TranscriptStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        apply_schema(self._conn)

    def close(self) -> None:
        self._conn.close()

    def replace_transcripts(self, video_id: UUID, lines: List[TranscriptLine]) -> None:
        """Idempotent: clear this video's transcript, then insert."""
        self._conn.execute("DELETE FROM transcripts WHERE video_id = ?", (str(video_id),))
        self._conn.executemany(
            "INSERT INTO transcripts (video_id, start_time, end_time, speaker, text) "
            "VALUES (?, ?, ?, ?, ?)",
            [(str(video_id), ln.start_time, ln.end_time, ln.speaker, ln.text) for ln in lines],
        )
        self._conn.commit()

    def count(self, video_id: UUID) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM transcripts WHERE video_id = ?", (str(video_id),)
        ).fetchone()[0]

    def search(self, query: str, video_id: Optional[UUID] = None, k: int = 10,
               speaker: Optional[str] = None) -> List[TranscriptHit]:
        """Rank lines by fraction of query words present (word overlap).

        `speaker` (Role 9) filters to one speaker label — "what did SPEAKER_01 say".
        """
        q_tokens = _tokens(query)
        if not q_tokens:
            return []
        sql = "SELECT video_id, start_time, end_time, speaker, text FROM transcripts"
        conds: list[str] = []
        params: list = []
        if video_id is not None:
            conds.append("video_id = ?")
            params.append(str(video_id))
        if speaker is not None:
            conds.append("speaker = ?")
            params.append(speaker)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        rows = self._conn.execute(sql, params).fetchall()

        hits: List[TranscriptHit] = []
        for r in rows:
            overlap = q_tokens & _tokens(r["text"])
            if not overlap:
                continue
            hits.append(TranscriptHit(
                video_id=UUID(r["video_id"]), start_time=r["start_time"],
                end_time=r["end_time"], text=r["text"], speaker=r["speaker"],
                score=len(overlap) / len(q_tokens),
            ))
        hits.sort(key=lambda h: (-h.score, h.start_time))
        return hits[:k]
