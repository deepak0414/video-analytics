"""FastAPI app: REST API + the static single-page UI.

`create_app(workdir)` is the factory used by `va serve` and by the tests.
Endpoints are sync `def`s on purpose: FastAPI runs them on a threadpool and the
pipeline is synchronous (search is fast; ingest is offloaded to the job queue).
"""
from __future__ import annotations

import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Iterator
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from va.pipeline.paths import Workspace
from va.web.jobs import AskQueue, IngestQueue

_STATIC = Path(__file__).parent / "static"
_CHUNK = 1024 * 1024


class IngestRequest(BaseModel):
    uri: str
    fps: float = 1.0


class AskRequest(BaseModel):
    question: str
    k: int = 5


def _fmt_t(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def _column(fn: Callable[[], list[dict[str, Any]]]) -> dict[str, Any]:
    """Run one search modality; a failing path yields an empty column with a
    note instead of failing the whole request."""
    try:
        return {"hits": fn(), "note": None}
    except Exception as e:  # noqa: BLE001
        return {"hits": [], "note": str(e) or e.__class__.__name__}


def create_app(workdir: str = ".va") -> FastAPI:
    ws = Workspace(workdir)
    jobs = IngestQueue(workdir)
    asks = AskQueue(workdir)

    def media_path(v) -> Path | None:
        """Resolve a catalog row to a playable file.

        `local_path` is stored as written by whichever session ingested the
        video — possibly relative to THAT session's CWD. When it doesn't
        resolve from here, fall back to the layout-v2 canonical location
        (videos/<key16>-*/media.*) under this server's workdir, so a video
        ingested by another workflow still plays.
        """
        if v.local_path:
            p = Path(v.local_path)
            if p.is_file():
                return p
        vdir = ws.video_dir(v.source_key)
        for m in sorted(vdir.glob("media.*")):
            if m.is_file():
                return m
        return None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        jobs.start()
        asks.start()
        yield
        asks.stop()
        jobs.stop()

    app = FastAPI(title="va — Ctrl-F for Video", lifespan=lifespan)

    # The page is served with mtime-stamped asset URLs (cache busting): a
    # changed app.js/style.css gets a new URL, so browsers can cache the
    # assets forever yet always load the version matching the backend.
    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        html = (_STATIC / "index.html").read_text()
        for asset in ("style.css", "app.js"):
            v = int((_STATIC / asset).stat().st_mtime)
            html = html.replace(f"/static/{asset}", f"/static/{asset}?v={v}")
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=_STATIC), name="static")

    # --- ingest ------------------------------------------------------------
    @app.post("/api/videos", status_code=202)
    def submit_ingest(req: IngestRequest) -> dict[str, str]:
        job = jobs.submit(req.uri, req.fps)
        return {"job_id": job.id}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        return job.to_dict()

    # --- catalog -----------------------------------------------------------
    @app.get("/api/videos")
    def list_videos() -> list[dict[str, Any]]:
        from va.storage.structured.catalog_sqlite import Catalog

        catalog = Catalog(ws.catalog_db)
        try:
            vids = catalog.list()
        finally:
            catalog.close()
        return [
            {
                "id": str(v.id),
                "source_type": v.source_type.value,
                "source_uri": v.source_uri,
                "source_key": v.source_key,
                "title": v.title,
                "duration_seconds": v.duration_seconds,
                "ingest_status": v.ingest_status.value,
                "has_media": media_path(v) is not None,
            }
            for v in vids
        ]

    # --- search ------------------------------------------------------------
    # All four modalities per query, one normalized hit shape:
    #   {video_id, t (seconds — what the player seeks to), score, label}
    @app.get("/api/search")
    def search(q: str, k: int = 5) -> dict[str, Any]:
        def visual() -> list[dict[str, Any]]:
            from va.pipeline.query import query

            return [
                {"video_id": str(h.video_id), "t": h.timestamp,
                 "score": h.score, "label": h.source_uri}
                for h in query(q, workdir=workdir, k=k)
            ]

        def caption() -> list[dict[str, Any]]:
            from va.pipeline.caption import search_captions

            return [
                {"video_id": str(h.video_id), "t": h.start_time,
                 "score": h.score, "label": h.caption}
                for h in search_captions(q, workdir=workdir, k=k)
            ]

        def transcript() -> list[dict[str, Any]]:
            from va.pipeline.transcript import search_transcripts

            return [
                {"video_id": str(h.video_id), "t": h.start_time, "score": h.score,
                 "label": (f"[{h.speaker}] " if h.speaker else "") + h.text}
                for h in search_transcripts(q, workdir=workdir, k=k)
            ]

        def objects() -> list[dict[str, Any]]:
            from va.pipeline.objects import query_objects

            return [
                {"video_id": str(s.video_id), "t": s.first_seen,
                 "score": s.max_confidence,
                 "label": f"{s.object_class}: {s.frames} frames "
                          f"({_fmt_t(s.first_seen)} → {_fmt_t(s.last_seen)})"}
                for s in query_objects(q, workdir=workdir)
            ]

        return {
            "visual": _column(visual),
            "caption": _column(caption),
            "transcript": _column(transcript),
            "objects": _column(objects),
        }

    # --- ask (Role 11) -------------------------------------------------------
    # Asks run on a background queue, ingest-style: ask() self-escalates to a
    # deep scan when a sparse answer is insufficient, so any question can
    # legitimately take minutes — too long for a synchronous request. The
    # single worker also serializes asks (the in-process LLM reasoner crashes
    # under concurrent generate() calls).
    @app.post("/api/ask", status_code=202)
    def submit_ask(req: AskRequest) -> dict[str, str]:
        job = asks.submit(req.question, req.k)
        return {"ask_id": job.id}

    @app.get("/api/asks/{ask_id}")
    def ask_status(ask_id: str) -> dict[str, Any]:
        job = asks.get(ask_id)
        if job is None:
            raise HTTPException(404, "no such ask")
        return job.to_dict()

    # --- media -------------------------------------------------------------
    # Serves the ingested local copy with HTTP Range support — required for
    # <video> seeking. Hand-rolled 206 handling so behavior doesn't depend on
    # the installed Starlette version.
    @app.get("/api/media/{video_id}")
    def media(video_id: str, request: Request):
        from va.storage.structured.catalog_sqlite import Catalog

        try:
            vid = UUID(video_id)
        except ValueError:
            raise HTTPException(404, "bad video id")
        catalog = Catalog(ws.catalog_db)
        try:
            v = catalog.get(vid)
        finally:
            catalog.close()
        path = media_path(v) if v is not None else None
        if path is None:
            raise HTTPException(404, "no media for this video")
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "video/mp4"

        range_header = request.headers.get("range")
        if not range_header:
            return FileResponse(path, media_type=ctype,
                                headers={"Accept-Ranges": "bytes"})

        try:
            unit, _, rng = range_header.partition("=")
            if unit.strip().lower() != "bytes":
                raise ValueError(range_header)
            start_s, _, end_s = rng.strip().partition("-")
            if start_s:
                start = int(start_s)
                end = int(end_s) if end_s else size - 1
            else:  # suffix range: bytes=-N (last N bytes)
                start = max(0, size - int(end_s))
                end = size - 1
        except ValueError:
            raise HTTPException(416, "malformed Range header")
        if start >= size or start > end:
            raise HTTPException(416, "range out of bounds")
        end = min(end, size - 1)
        length = end - start + 1

        def chunks() -> Iterator[bytes]:
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    data = f.read(min(_CHUNK, remaining))
                    if not data:
                        return
                    remaining -= len(data)
                    yield data

        return StreamingResponse(
            chunks(), status_code=206, media_type=ctype,
            headers={
                "Content-Range": f"bytes {start}-{end}/{size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(length),
            },
        )

    return app
