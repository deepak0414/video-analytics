"""Durable job records (WS6.a) — the `jobs` table.

The web layer's queues persist every job here so a server restart can resume
queued/running INGEST jobs (ingest() is the idempotency point: a job that
crashed mid-run re-runs against the same catalog row and either completes it
or dedups on `done`). Ask jobs are persisted for history/polling but are
FAILED on restart rather than resumed — silently re-running a stale question
against the LLM would burn minutes of GPU time nobody asked for.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from va.storage.structured.schema import connect


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._conn = connect(self.path)

    def close(self) -> None:
        self._conn.close()

    def record(self, job_id: str, kind: str, payload: dict[str, Any]) -> None:
        """Insert a new queued job (idempotent on id)."""
        self._conn.execute(
            "INSERT OR IGNORE INTO jobs (id, kind, state, payload, created_at, "
            "updated_at) VALUES (?, ?, 'queued', ?, ?, ?)",
            (job_id, kind, json.dumps(payload), _now(), _now()),
        )
        self._conn.commit()

    def update(
        self,
        job_id: str,
        state: str,
        video_id: Optional[str] = None,
        error: Optional[str] = None,
        result: Optional[dict[str, Any]] = None,
    ) -> None:
        self._conn.execute(
            "UPDATE jobs SET state = ?, video_id = COALESCE(?, video_id), "
            "error = ?, result = ?, updated_at = ? WHERE id = ?",
            (state, video_id, error,
             json.dumps(result) if result is not None else None,
             _now(), job_id),
        )
        self._conn.commit()

    def get(self, job_id: str) -> Optional[dict[str, Any]]:
        r = self._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return self._row(r) if r else None

    def pending(self, kind: str) -> list[dict[str, Any]]:
        """Jobs a restarted worker must pick up: queued or running (a `running`
        row is a crash artifact — the worker died mid-job), oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE kind = ? AND state IN ('queued', 'running') "
            "ORDER BY created_at",
            (kind,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def bump_attempts(self, job_id: str) -> int:
        """Increment and return the job's resume-attempt count — the poison-job
        guard: a job that kills the process leaves `running` behind, and
        without a cap every restart would resume and re-crash it forever."""
        self._conn.execute(
            "UPDATE jobs SET attempts = attempts + 1, updated_at = ? WHERE id = ?",
            (_now(), job_id),
        )
        self._conn.commit()
        (n,) = self._conn.execute(
            "SELECT attempts FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return int(n)

    def requeue_if_running(self, job_id: str) -> None:
        """Graceful-stop requeue, guarded: only a still-`running` row reverts
        to queued — an unguarded write could land AFTER the worker's terminal
        write (join expired milliseconds early) and erase a done result
        (round-7 review)."""
        self._conn.execute(
            "UPDATE jobs SET state = 'queued', updated_at = ? "
            "WHERE id = ? AND state = 'running'",
            (_now(), job_id),
        )
        self._conn.commit()

    def fail_pending(self, kind: str, error: str) -> int:
        """Mark all queued/running jobs of `kind` failed (the ask restart
        policy). Returns how many were failed."""
        cur = self._conn.execute(
            "UPDATE jobs SET state = 'failed', error = ?, updated_at = ? "
            "WHERE kind = ? AND state IN ('queued', 'running')",
            (error, _now(), kind),
        )
        self._conn.commit()
        return cur.rowcount

    @staticmethod
    def _row(r) -> dict[str, Any]:
        # A JSON-corrupt payload must not make _row raise: pending() maps every
        # row through here, so one bad row would otherwise block ALL resumes on
        # every boot (round-6 review). payload=None routes it to the
        # malformed-row terminal-fail path instead.
        try:
            payload = json.loads(r["payload"])
        except (TypeError, ValueError):
            payload = None
        return {
            "id": r["id"],
            "kind": r["kind"],
            "state": r["state"],
            "payload": payload,
            "video_id": r["video_id"],
            "error": r["error"],
            "result": json.loads(r["result"]) if r["result"] else None,
            "attempts": r["attempts"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
