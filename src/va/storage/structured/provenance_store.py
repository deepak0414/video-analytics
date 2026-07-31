"""Per-(video, role) provenance store (WS-1 §6-b, PROV-2).

Records which model/config fingerprint (`va.provenance.role_fingerprint`) produced each
role's rows for a video, plus run args not in `(role, cfg)` like the ingest `fps`. Written
at ingest (PROV-3), read by `va stale` (PROV-4) and the selective reprocess (B). Keyed by
`(video_id, role)`, so re-processing a role overwrites its prior provenance.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from va.storage.structured.schema import connect

_COLS = "role, model, fingerprint, fps, run_id, row_count, produced_at"


class ProvenanceStore:
    def __init__(self, path: str | Path):
        self._conn = connect(path)

    def record(
        self,
        video_id: UUID | str,
        role: str,
        model: str,
        fingerprint: str,
        *,
        fps: Optional[float] = None,
        run_id: Optional[str] = None,
        row_count: Optional[int] = None,
    ) -> None:
        """Upsert the provenance for one (video, role) — the latest processing wins."""
        self._conn.execute(
            "INSERT OR REPLACE INTO role_provenance "
            "(video_id, role, model, fingerprint, fps, run_id, row_count, produced_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (str(video_id), role, model, fingerprint, fps, run_id, row_count),
        )
        self._conn.commit()

    def get(self, video_id: UUID | str, role: Optional[str] = None) -> list[dict[str, Any]]:
        """Provenance rows for a video — all roles (ordered), or one if `role` is given."""
        if role is None:
            cur = self._conn.execute(
                f"SELECT {_COLS} FROM role_provenance WHERE video_id=? ORDER BY role",
                (str(video_id),),
            )
        else:
            cur = self._conn.execute(
                f"SELECT {_COLS} FROM role_provenance WHERE video_id=? AND role=?",
                (str(video_id), role),
            )
        return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()
