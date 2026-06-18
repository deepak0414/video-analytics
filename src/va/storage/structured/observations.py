"""ObservationStore — the deep-scan cache (`observations` table).

A sweep's per-frame micro-captions are stored under (video_id, prompt_key) so a
repeat question with the same scan target reuses the expensive VLM sweep.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple
from uuid import UUID

from va.storage.structured.schema import connect


class ObservationStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._conn = connect(self.path)

    def close(self) -> None:
        self._conn.close()

    def load(self, video_id: UUID, prompt_key: str) -> List[Tuple[float, str]]:
        rows = self._conn.execute(
            "SELECT timestamp, text FROM observations "
            "WHERE video_id = ? AND prompt_key = ? ORDER BY timestamp",
            (str(video_id), prompt_key),
        ).fetchall()
        return [(r["timestamp"], r["text"]) for r in rows]

    def replace(self, video_id: UUID, prompt_key: str,
                observations: List[Tuple[float, str]]) -> None:
        self._conn.execute(
            "DELETE FROM observations WHERE video_id = ? AND prompt_key = ?",
            (str(video_id), prompt_key),
        )
        self._conn.executemany(
            "INSERT INTO observations (video_id, prompt_key, timestamp, text) "
            "VALUES (?, ?, ?, ?)",
            [(str(video_id), prompt_key, ts, text) for ts, text in observations],
        )
        self._conn.commit()
