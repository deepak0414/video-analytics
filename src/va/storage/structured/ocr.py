"""OcrStore — write/search the `ocr_results` table.

Shares the central correlation DB (keyed by video_id). Search is the same
word-overlap match as transcripts; rows whose text is identical (one appearance
per sighting) are grouped into a single hit spanning first..last sighting, so
"Coors Light billboard" returns one hit per distinct on-screen string, not one
per sampled frame.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from uuid import UUID

from va.contracts.ocr import OcrLine
from va.storage.structured.schema import apply_schema


@dataclass
class OcrHit:
    video_id: UUID
    time_start: float   # first sighting of this text
    time_end: float     # last sighting
    text: str
    sightings: int      # distinct appearances of this exact text
    score: float


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", s.lower()))


def _collapsed(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


class OcrStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        apply_schema(self._conn)

    def close(self) -> None:
        self._conn.close()

    def replace_lines(self, video_id: UUID, lines: List[OcrLine]) -> None:
        """Idempotent: clear this video's OCR rows, then insert."""
        self._conn.execute("DELETE FROM ocr_results WHERE video_id = ?", (str(video_id),))
        self._conn.executemany(
            "INSERT INTO ocr_results (video_id, timestamp, text, bbox_x, bbox_y, bbox_w, bbox_h) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(str(video_id), ln.timestamp, ln.text,
              ln.bbox_x, ln.bbox_y, ln.bbox_w, ln.bbox_h) for ln in lines],
        )
        self._conn.commit()

    def count(self, video_id: UUID) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM ocr_results WHERE video_id = ?", (str(video_id),)
        ).fetchone()[0]

    def search(self, query: str, video_id: Optional[UUID] = None, k: int = 10) -> List[OcrHit]:
        """Rank on-screen strings by fraction of query words present.

        Also matches space-insensitively: OCR routinely merges words it read
        correctly ("Coors Light" billboard -> "COOrSLIGHT", measured on the
        Ferrari clip), so a query whose collapsed form appears inside the
        collapsed text counts as a full match.
        """
        q_tokens = _tokens(query)
        if not q_tokens:
            return []
        q_collapsed = _collapsed(query)
        sql = ("SELECT video_id, text, COUNT(*) AS sightings, "
               "MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts "
               "FROM ocr_results")
        params: list = []
        if video_id is not None:
            sql += " WHERE video_id = ?"
            params.append(str(video_id))
        sql += " GROUP BY video_id, text"
        rows = self._conn.execute(sql, params).fetchall()

        hits: List[OcrHit] = []
        for r in rows:
            overlap = q_tokens & _tokens(r["text"])
            score = len(overlap) / len(q_tokens)
            # length guard: a 1-3 char collapsed query inside any longer string
            # would be noise, not a phrase match
            if len(q_collapsed) >= 4 and q_collapsed in _collapsed(r["text"]):
                score = 1.0
            if score == 0:
                continue
            hits.append(OcrHit(
                video_id=UUID(r["video_id"]),
                time_start=r["first_ts"], time_end=r["last_ts"],
                text=r["text"], sightings=r["sightings"],
                score=score,
            ))
        hits.sort(key=lambda h: (-h.score, h.time_start))
        return hits[:k]
