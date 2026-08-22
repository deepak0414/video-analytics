"""Single-worker job queues for the web server.

GPU-heavy work is serialized: each queue processes jobs strictly one at a time
on its own daemon thread. Since WS6.a job records are DURABLE (the `jobs`
table in the catalog DB): every submit and state transition persists, and a
restarted server resumes queued/running INGEST jobs exactly once — ingest()
is the idempotency point, so a job that died mid-run re-runs against the same
catalog row and either completes it or dedups on `done`. Ask jobs persist for
history but are failed on restart (re-running a stale question would burn
minutes of LLM time nobody asked for); their failed records are rebuilt into
memory so a polling browser sees the failure instead of a 404. Persistence is
best-effort by design: a broken jobs table degrades to the old memory-only
behavior with a warning, never a dead queue.

Asks get the same treatment as ingests because `ask()` can legitimately take
minutes (deep-scan sweeps, including self-escalation re-runs) — far too long
for a synchronous HTTP request. The single ask worker also serializes
concurrent asks, which the in-process LLM reasoner requires (overlapping
`generate()` calls crash).
"""
from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

log = logging.getLogger("va.web")

# Resume-attempt cap (poison-job guard): a job whose run KILLS the process can
# never persist its own `failed` state, so the cap is the only terminal exit.
# Structure/budget knob, not content.
MAX_RESUME_ATTEMPTS = 3


class JobState(str, Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


@dataclass
class IngestJob:
    uri: str
    fps: float = 1.0
    id: str = field(default_factory=lambda: uuid4().hex)
    state: JobState = JobState.queued
    video_id: Optional[str] = None
    error: Optional[str] = None
    result: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "uri": self.uri,
            "state": self.state.value,
            "video_id": self.video_id,
            "error": self.error,
            "result": self.result,
        }


@dataclass
class AskJob:
    question: str
    k: int = 5
    id: str = field(default_factory=lambda: uuid4().hex)
    state: JobState = JobState.queued
    error: Optional[str] = None
    result: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ask_id": self.id,
            "question": self.question,
            "state": self.state.value,
            "error": self.error,
            "result": self.result,
        }


class SerialQueue:
    """One daemon thread; subclasses implement `_process(job)`."""

    name = "va-queue"
    kind = "job"

    def __init__(self, workdir: Optional[str] = None) -> None:
        self._jobs: dict[str, Any] = {}
        self._q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self.workdir = workdir

    # --- durability (WS6.a) -------------------------------------------------
    def _store(self):
        """A fresh short-lived JobStore, or None when there is no workdir (the
        queue then behaves exactly as the pre-WS6.a memory-only version)."""
        if self.workdir is None:
            return None
        from va.pipeline.paths import Workspace
        from va.storage.structured.jobs_store import JobStore

        db = Workspace(self.workdir).catalog_db
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        return JobStore(db)

    def _persist(self, action, *args, **kwargs) -> Any:
        """Best-effort store call: durability must never kill the worker —
        a broken jobs table degrades to memory-only with a warning."""
        try:
            store = self._store()
            if store is None:
                return None
            try:
                return getattr(store, action)(*args, **kwargs)
            finally:
                store.close()
        except Exception:  # noqa: BLE001
            log.warning("job persistence failed (%s) — continuing in-memory",
                        action, exc_info=True)
            return None

    def _resume(self) -> None:  # pragma: no cover - overridden
        """Restart policy hook, run once before the worker starts."""

    def start(self) -> None:
        if self._thread is not None:
            return
        self._resume()
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()

    JOIN_TIMEOUT = 5.0

    def stop(self) -> None:
        if self._thread is None:
            return
        self._q.put(None)  # sentinel: drain then exit
        self._thread.join(timeout=self.JOIN_TIMEOUT)
        self._thread = None
        # Graceful shutdown is NOT crash evidence: if a job is still in flight
        # when we give up joining, knock its row back to `queued` so deliberate
        # restarts don't march attempts toward the poison cap (round-6 review).
        # A daemon thread that later finishes anyway overwrites this with its
        # terminal state — last write wins either way.
        current = self._current
        if current is not None:
            self._persist("requeue_if_running", current)

    _current: Optional[str] = None

    def get(self, job_id: str) -> Optional[Any]:
        return self._jobs.get(job_id)

    def _submit(self, job: Any, payload: dict[str, Any]) -> Any:
        self._jobs[job.id] = job
        self._persist("record", job.id, self.kind, payload)
        self._q.put(job.id)
        return job

    def _run(self) -> None:
        while True:
            job_id = self._q.get()
            if job_id is None:
                return
            self._current = job_id
            try:
                self._process(self._jobs[job_id])
            finally:
                self._current = None

    def _process(self, job: Any) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


class IngestQueue(SerialQueue):
    name = "va-ingest"
    kind = "ingest"

    def __init__(self, workdir: str):
        super().__init__(workdir)

    def submit(self, uri: str, fps: float = 1.0) -> IngestJob:
        job = IngestJob(uri=uri, fps=fps)
        return self._submit(job, {"uri": job.uri, "fps": job.fps})

    def _resume(self) -> None:
        # Crash artifacts: `queued` never ran; `running` died mid-job. Both
        # re-enqueue — ingest() is idempotent (same catalog row; dedup on
        # done), so a resumed job runs to completion exactly once.
        rows = self._persist("pending", self.kind) or []
        resumed = 0
        for r in rows:
            # One malformed row (hand-edited payload, future-build shape) must
            # cost one job, not the server: this runs inside the FastAPI
            # lifespan, where an escape would kill startup (round-2 review).
            try:
                # Poison-job guard (round-4/5 review): a job that KILLS the
                # process (OOM, native segfault) leaves `running` behind and
                # would otherwise resume-and-recrash on every restart forever.
                # ONLY `running` rows bump — that state is the crash evidence;
                # `queued` rows never executed (they may sit behind the poison
                # job through many restarts) and must not accrue guilt.
                attempts = (self._persist("bump_attempts", r["id"])
                            if r["state"] == "running" else None)
                if attempts is not None and attempts <= MAX_RESUME_ATTEMPTS:
                    # Revert the bumped row to `queued` NOW: while it waits in
                    # the resume queue, unrelated server flaps must not re-bump
                    # it toward the cap — only a job that actually STARTS
                    # re-marks itself running, preserving poison detection
                    # (round-3 review).
                    self._persist("update", r["id"], "queued")
                if attempts is not None and attempts > MAX_RESUME_ATTEMPTS:
                    msg = (f"gave up after {MAX_RESUME_ATTEMPTS} resume "
                           "attempts — this job repeatedly died mid-run")
                    self._persist("update", r["id"], "failed", error=msg)
                    self._fail_in_memory(r, msg)
                    log.warning("jobs row %r exceeded %d resume attempts — "
                                "marked failed", r["id"], MAX_RESUME_ATTEMPTS)
                    continue
                job = IngestJob(uri=r["payload"]["uri"],
                                fps=r["payload"].get("fps", 1.0), id=r["id"])
                job.video_id = r["video_id"]
                self._jobs[job.id] = job
                self._q.put(job.id)
                resumed += 1
            except Exception:  # noqa: BLE001
                # Terminal, not skip-forever: an unresumable row left pending
                # would be re-warned on every boot for eternity.
                msg = "unresumable jobs row (malformed payload)"
                self._persist("update", r["id"], "failed", error=msg)
                self._fail_in_memory(r, msg)
                log.warning("could not resume jobs row %r — marked failed",
                            r.get("id"), exc_info=True)
        if resumed:
            log.info("resumed %d pending ingest job(s) from the jobs table",
                     resumed)

    def _fail_in_memory(self, r: dict[str, Any], msg: str) -> None:
        """Rebuild a terminally-failed row as a pollable in-memory job — the
        browser watching this job_id must see state=failed + the failure
        string, not a 404 (round-6 review; mirrors AskQueue._resume)."""
        payload = r.get("payload") or {}
        job = IngestJob(uri=payload.get("uri", "<unknown>"),
                        fps=payload.get("fps", 1.0), id=r["id"])
        job.state = JobState.failed
        job.error = msg
        job.video_id = r.get("video_id")
        self._jobs[job.id] = job

    def _process(self, job: IngestJob) -> None:
        job.state = JobState.running
        self._persist("update", job.id, "running")
        try:
            # Cheap pre-resolve so the UI can show the catalog row while the
            # heavy fetch/embed work is still running. Failures here are
            # ignored — ingest() below redoes this and reports the real error.
            try:
                from va.pipeline.paths import Workspace
                from va.sources.base import resolve_source
                from va.storage.structured.catalog_sqlite import Catalog

                resolved = resolve_source(job.uri).resolve(job.uri)
                catalog = Catalog(Workspace(self.workdir).catalog_db)
                try:
                    video, _ = catalog.get_or_create(resolved)
                    job.video_id = str(video.id)
                finally:
                    catalog.close()
            except Exception:
                pass

            from va.pipeline.ingest import ingest

            res = ingest(job.uri, workdir=self.workdir, fps=job.fps)
            job.video_id = str(res.video.id)
            job.result = {
                "deduped": res.deduped,
                # Distinguishes a quarantined dedup (deliberately excluded, NOT searchable)
                # from a normal already-ingested dedup — the UI must not render both as
                # "done (already ingested)" (mirrors the CLI's `[quarantined]` note).
                "ingest_status": res.video.ingest_status.value,
                "frames_indexed": res.frames_indexed,
                "segments": res.segments,
                "captioned_segments": res.captioned_segments,
                "transcript_lines": res.transcript_lines,
                "detections": res.detections,
            }
            job.state = JobState.done
            self._persist("update", job.id, "done", video_id=job.video_id,
                          result=job.result)
        except Exception as e:  # noqa: BLE001 - any ingest failure ends the job
            job.error = str(e) or e.__class__.__name__
            job.state = JobState.failed
            self._persist("update", job.id, "failed", video_id=job.video_id,
                          error=job.error)


class AskQueue(SerialQueue):
    name = "va-ask"
    kind = "ask"

    def __init__(self, workdir: str):
        super().__init__(workdir)

    def submit(self, question: str, k: int = 5) -> AskJob:
        job = AskJob(question=question, k=k)
        return self._submit(job, {"question": job.question, "k": job.k})

    def _resume(self) -> None:
        # Restart policy for asks: FAIL, don't re-run — a stale question would
        # silently burn minutes of LLM/GPU time nobody is waiting for. The
        # failed records are rebuilt in memory so a polling browser sees the
        # failure rather than a 404.
        msg = "server restarted before this ask completed — resubmit it"
        rows = self._persist("pending", self.kind) or []
        self._persist("fail_pending", self.kind, msg)
        for r in rows:
            try:
                job = AskJob(question=r["payload"]["question"],
                             k=r["payload"].get("k", 5), id=r["id"])
                job.state = JobState.failed
                job.error = msg
                self._jobs[job.id] = job
            except Exception:  # noqa: BLE001
                # Still pollable: mirror the ingest side — a browser watching
                # this ask_id must see the failure, not a 404 (round-2 review).
                job = AskJob(question="<unrecoverable>", id=r["id"])
                job.state = JobState.failed
                job.error = "unresumable ask row (malformed payload)"
                self._jobs[job.id] = job
                log.warning("could not rebuild ask row %r — marked failed",
                            r.get("id"), exc_info=True)

    def _process(self, job: AskJob) -> None:
        job.state = JobState.running
        self._persist("update", job.id, "running")
        try:
            from va.pipeline.ask import ask

            res = ask(job.question, workdir=self.workdir, k=job.k)
            job.result = {
                "question": res.question,
                "rendered": res.rendered,
                "evidence": [
                    {
                        "modality": i.modality,
                        "video_id": str(i.video_id) if i.video_id else None,
                        "t": i.time_start,
                        "score": i.score,
                        "content": i.content,
                    }
                    for i in res.evidence.items
                ],
                "notes": list(res.evidence.notes),
            }
            job.state = JobState.done
            self._persist("update", job.id, "done", result=job.result)
        except Exception as e:  # noqa: BLE001 - any ask failure ends the job
            log.exception("ask failed: %s", job.question)
            job.error = f"{e.__class__.__name__}: {e}"
            job.state = JobState.failed
            self._persist("update", job.id, "failed", error=job.error)
