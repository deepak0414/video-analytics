"""Video catalog — the `videos` table (SQLite).

The `videos` table is the registry that answers "have we already ingested this?"
via the UNIQUE `source_key`. It lives in the same DB as every other role's table
(see schema.py) — that shared DB is the central correlation store, all keyed by
videos.id.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID

from va.contracts.video import IngestStatus, ResolvedVideo, SourceType, Video
from va.storage.structured.schema import connect

_COLS = [
    "id", "source_type", "source_uri", "source_key", "local_path", "title",
    "duration_seconds", "fps", "resolution", "has_audio", "ingest_status",
    "ingest_error", "created_at", "fetched_at", "processed_at",
    "last_ingest_run_id",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Catalog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._conn = connect(self.path)

    def close(self) -> None:
        self._conn.close()

    # --- row <-> model -----------------------------------------------------
    @staticmethod
    def _to_row(v: Video) -> dict:
        return {
            "id": str(v.id),
            "source_type": v.source_type.value,
            "source_uri": v.source_uri,
            "source_key": v.source_key,
            "local_path": v.local_path,
            "title": v.title,
            "duration_seconds": v.duration_seconds,
            "fps": v.fps,
            "resolution": v.resolution,
            "has_audio": None if v.has_audio is None else int(v.has_audio),
            "ingest_status": v.ingest_status.value,
            "ingest_error": v.ingest_error,
            "created_at": v.created_at.isoformat(),
            "fetched_at": v.fetched_at.isoformat() if v.fetched_at else None,
            "processed_at": v.processed_at.isoformat() if v.processed_at else None,
            "last_ingest_run_id": v.last_ingest_run_id,
        }

    @staticmethod
    def _from_row(r: sqlite3.Row) -> Video:
        return Video(
            id=UUID(r["id"]),
            source_type=SourceType(r["source_type"]),
            source_uri=r["source_uri"],
            source_key=r["source_key"],
            local_path=r["local_path"],
            title=r["title"],
            duration_seconds=r["duration_seconds"],
            fps=r["fps"],
            resolution=r["resolution"],
            has_audio=None if r["has_audio"] is None else bool(r["has_audio"]),
            ingest_status=IngestStatus(r["ingest_status"]),
            ingest_error=r["ingest_error"],
            created_at=datetime.fromisoformat(r["created_at"]),
            fetched_at=datetime.fromisoformat(r["fetched_at"]) if r["fetched_at"] else None,
            processed_at=datetime.fromisoformat(r["processed_at"]) if r["processed_at"] else None,
            last_ingest_run_id=r["last_ingest_run_id"],
        )

    # --- ops ---------------------------------------------------------------
    def get_by_source_key(self, source_key: str) -> Optional[Video]:
        r = self._conn.execute(
            "SELECT * FROM videos WHERE source_key = ?", (source_key,)
        ).fetchone()
        return self._from_row(r) if r else None

    def get(self, video_id: UUID) -> Optional[Video]:
        r = self._conn.execute(
            "SELECT * FROM videos WHERE id = ?", (str(video_id),)
        ).fetchone()
        return self._from_row(r) if r else None

    def get_many(self, video_ids: list[UUID]) -> dict[str, Video]:
        """Batch-fetch videos by id in ONE query -> {id_str: Video}. Lets the read
        path resolve many hits' source video without a SELECT per hit."""
        ids = [str(v) for v in video_ids]
        if not ids:
            return {}
        marks = ", ".join("?" for _ in ids)
        rows = self._conn.execute(
            f"SELECT * FROM videos WHERE id IN ({marks})", ids
        ).fetchall()
        return {r["id"]: self._from_row(r) for r in rows}

    def list(self, limit: Optional[int] = None) -> list[Video]:
        """All videos, newest first. (Consumed by the web layer's GET /api/videos.)"""
        sql = "SELECT * FROM videos ORDER BY created_at DESC"
        params: list = []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [self._from_row(r) for r in self._conn.execute(sql, params).fetchall()]

    def upsert(self, video: Video) -> Video:
        """Insert, or return the existing row if source_key is already present.

        This is the idempotency point: a duplicate source_key never creates a
        second row — the caller gets back the original (with its id/status).
        """
        existing = self.get_by_source_key(video.source_key)
        if existing is not None:
            return existing
        row = self._to_row(video)
        placeholders = ", ".join("?" for _ in _COLS)
        self._conn.execute(
            f"INSERT INTO videos ({', '.join(_COLS)}) VALUES ({placeholders})",
            [row[c] for c in _COLS],
        )
        self._conn.commit()
        return video

    def get_or_create(self, resolved: ResolvedVideo) -> tuple[Video, bool]:
        """Return (video, created). created=False means already in catalog."""
        existing = self.get_by_source_key(resolved.source_key)
        if existing is not None:
            return existing, False
        return self.upsert(Video.from_resolved(resolved)), True

    def set_status(
        self,
        video_id: UUID,
        status: IngestStatus,
        *,
        error: Optional[str] = None,
        local_path: Optional[str] = None,
        mark_fetched: bool = False,
        mark_processed: bool = False,
        ingest_run_id: Optional[str] = None,
    ) -> None:
        sets = ["ingest_status = ?"]
        vals: list = [status.value]
        if error is not None:
            sets.append("ingest_error = ?"); vals.append(error)
        if local_path is not None:
            sets.append("local_path = ?"); vals.append(local_path)
        if mark_fetched:
            sets.append("fetched_at = ?"); vals.append(_now())
        if mark_processed:
            sets.append("processed_at = ?"); vals.append(_now())
        if ingest_run_id is not None:
            sets.append("last_ingest_run_id = ?"); vals.append(ingest_run_id)
        vals.append(str(video_id))
        self._conn.execute(f"UPDATE videos SET {', '.join(sets)} WHERE id = ?", vals)
        self._conn.commit()

    def set_paths(self, video_id: UUID, local_path: str,
                  source_uri: Optional[str] = None) -> None:
        """Update where the media lives (layout moves); optionally also the
        source_uri — for LOCAL sources whose canonical input WAS the old path."""
        if source_uri is not None:
            self._conn.execute(
                "UPDATE videos SET local_path = ?, source_uri = ? WHERE id = ?",
                (local_path, source_uri, str(video_id)),
            )
        else:
            self._conn.execute(
                "UPDATE videos SET local_path = ? WHERE id = ?",
                (local_path, str(video_id)),
            )
        self._conn.commit()

    def delete(self, video_id: UUID) -> bool:
        cur = self._conn.execute("DELETE FROM videos WHERE id = ?", (str(video_id),))
        self._conn.commit()
        return cur.rowcount > 0

    def update_metadata(self, video_id: UUID, resolved: ResolvedVideo) -> None:
        m = resolved.metadata
        self._conn.execute(
            "UPDATE videos SET local_path=?, title=?, duration_seconds=?, fps=?, "
            "resolution=?, has_audio=? WHERE id=?",
            [
                resolved.local_path, m.title, m.duration_seconds, m.fps,
                m.resolution, None if m.has_audio is None else int(m.has_audio),
                str(video_id),
            ],
        )
        self._conn.commit()
