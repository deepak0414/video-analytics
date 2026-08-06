"""Camera registry — the `cameras` table (WS-3).

A camera is a fixed stream whose recorded chunks land in `videos` rows via
`videos.camera_id`; the set of one camera's chunks is the unit A-LSSRVF queries
range over. Ingestion (WS-4's stream source) creates/looks up cameras; the query
layer reads them to scope searches and label results.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from va.contracts.video import Camera
from va.storage.structured.schema import connect


class CameraStore:
    def __init__(self, path: str | Path):
        self._conn = connect(Path(path))

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _from_row(r: sqlite3.Row) -> Camera:
        return Camera(
            id=r["id"], name=r["name"], source_ref=r["source_ref"],
            location=r["location"],
            created_at=datetime.fromisoformat(r["created_at"])
            if "T" in r["created_at"]
            # SQLite's datetime('now') default writes "YYYY-MM-DD HH:MM:SS" (UTC)
            else datetime.fromisoformat(r["created_at"].replace(" ", "T")).replace(
                tzinfo=timezone.utc),
        )

    def get_or_create(self, camera: Camera) -> tuple[Camera, bool]:
        """Return (camera, created). The id is the idempotency key — re-registering
        an existing id returns the stored row untouched (rename via `update`).
        Atomic (WS3.a review carry-over): INSERT OR IGNORE + re-SELECT, so two
        parallel per-camera ingests racing on the same new id both succeed —
        SELECT-then-INSERT would crash the loser on the UNIQUE constraint."""
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO cameras (id, name, source_ref, location, "
            "created_at) VALUES (?, ?, ?, ?, ?)",
            (camera.id, camera.name, camera.source_ref, camera.location,
             camera.created_at.isoformat()),
        )
        self._conn.commit()
        created = cur.rowcount == 1
        stored = self.get(camera.id)
        assert stored is not None  # just inserted or already present
        return stored, created

    def get(self, camera_id: str) -> Optional[Camera]:
        r = self._conn.execute(
            "SELECT * FROM cameras WHERE id = ?", (camera_id,)
        ).fetchone()
        return self._from_row(r) if r else None

    def list(self) -> list[Camera]:
        rows = self._conn.execute("SELECT * FROM cameras ORDER BY name").fetchall()
        return [self._from_row(r) for r in rows]
