"""Single-worker job queues for the web server.

GPU-heavy work is serialized: each queue processes jobs strictly one at a time
on its own daemon thread. Job records live in memory only — for ingest the
durable record is the catalog's `ingest_status`; jobs exist so the browser has
something to poll between submit and done, and to carry the error message of a
failed run back to the UI.

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
from typing import Any, Optional
from uuid import uuid4

log = logging.getLogger("va.web")


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

    def __init__(self) -> None:
        self._jobs: dict[str, Any] = {}
        self._q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._q.put(None)  # sentinel: drain then exit
        self._thread.join(timeout=5)
        self._thread = None

    def get(self, job_id: str) -> Optional[Any]:
        return self._jobs.get(job_id)

    def _submit(self, job: Any) -> Any:
        self._jobs[job.id] = job
        self._q.put(job.id)
        return job

    def _run(self) -> None:
        while True:
            job_id = self._q.get()
            if job_id is None:
                return
            self._process(self._jobs[job_id])

    def _process(self, job: Any) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


class IngestQueue(SerialQueue):
    name = "va-ingest"

    def __init__(self, workdir: str):
        super().__init__()
        self.workdir = workdir

    def submit(self, uri: str, fps: float = 1.0) -> IngestJob:
        return self._submit(IngestJob(uri=uri, fps=fps))

    def _process(self, job: IngestJob) -> None:
        job.state = JobState.running
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
                "frames_indexed": res.frames_indexed,
                "segments": res.segments,
                "captioned_segments": res.captioned_segments,
                "transcript_lines": res.transcript_lines,
                "detections": res.detections,
            }
            job.state = JobState.done
        except Exception as e:  # noqa: BLE001 - any ingest failure ends the job
            job.error = str(e) or e.__class__.__name__
            job.state = JobState.failed


class AskQueue(SerialQueue):
    name = "va-ask"

    def __init__(self, workdir: str):
        super().__init__()
        self.workdir = workdir

    def submit(self, question: str, k: int = 5) -> AskJob:
        return self._submit(AskJob(question=question, k=k))

    def _process(self, job: AskJob) -> None:
        job.state = JobState.running
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
        except Exception as e:  # noqa: BLE001 - any ask failure ends the job
            log.exception("ask failed: %s", job.question)
            job.error = f"{e.__class__.__name__}: {e}"
            job.state = JobState.failed
