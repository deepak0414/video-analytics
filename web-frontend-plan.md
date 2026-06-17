# Web Frontend Plan: browser UI for scale-testing ingestion + search

*Created: 2026-06-10 | Status: v1 implemented + Ask/Role-11 box (see COORDINATION.md log); upload + distinct-counts column deferred*
*Owner: web/frontend agent (see [COORDINATION.md](COORDINATION.md) for the split with the roles agent)*

## Goal

A web UI served from this machine, reachable from a laptop browser on the LAN, to make
testing with real YouTube videos fast: paste a URL → ingest → play the video → run a
query → click a result hit → the player seeks to that moment.

## Decisions (locked in discussion 2026-06-10)

| Decision | Choice |
|---|---|
| Stack | FastAPI + uvicorn backend; single-page vanilla HTML/JS/CSS, no build step, no node |
| Pipeline access | In-process imports of `va.pipeline.*` (NOT subprocess to the CLI) — keeps `ModelManager` models warm across requests |
| Network | uvicorn bound to `0.0.0.0`, port configurable (default **8080**) |
| Sources (v1) | YouTube URLs, direct media URLs, paths on *this machine's* disk |
| Laptop file upload | **v2** — explicitly out of scope for v1 |
| Playback | YouTube → IFrame embed API; direct media URL → HTML5 `<video>`; everything else (and fallback) → serve the ingested local copy via `/api/media/{video_id}` |
| Results UX | One query box; runs **all four modalities** (visual / caption / transcript / objects) and renders four labeled columns; every hit row is clickable → seeks the player |
| Ingest | Background job, **single-worker queue** (serialize GPU work); browser polls job status |
| Config | Same conventions as the CLI: `--workdir` flag, `VA_CONFIG_DIR` env var for stub vs real models |

## File layout (all new files owned by the web agent)

```
src/va/web/
  __init__.py
  app.py            # create_app(workdir) — FastAPI app factory + API routes
  jobs.py           # IngestQueue — single worker thread + in-memory job registry
  static/
    index.html      # the whole UI (one page)
    app.js          # fetch calls, polling, player control, result rendering
    style.css
tests/test_web.py   # TestClient tests, stub config, synthetic clips — no GPU/network
```

Plus three small touches to shared files (flagged in COORDINATION.md):
- `pyproject.toml` — new optional extra `[web]`: `fastapi`, `uvicorn`
- `src/va/cli.py` — new `va serve` subcommand (`--port`, `--host`, reuses global `--workdir`)
- `CLAUDE.md` — command snippet for launching the server

## API

All responses JSON unless noted. `video_id` is the catalog UUID as a string.

### `POST /api/videos` — submit a URI for ingest
Request: `{"uri": "<youtube-url | direct-media-url | server-local-path>", "fps": 1.0}`
Behavior: enqueue on the ingest queue, return immediately.
Response `202`: `{"job_id": "<uuid>"}`
The worker simply calls `va.pipeline.ingest.ingest(uri, workdir, fps)` — idempotency,
status transitions, and error recording are already handled there (re-submitting a
`done` video returns `deduped=True` instantly).

### `GET /api/jobs/{job_id}` — poll an ingest job
Response: `{"state": "queued|running|done|failed", "uri": ..., "video_id": <str|null>,
"error": <str|null>, "result": {frames_indexed, segments, captioned_segments,
transcript_lines, detections} | null}`
`video_id` is filled as soon as the worker has resolved the source (so the UI can show
the catalog row while processing continues). Job registry is in-memory — lost on server
restart, which is fine because the catalog's `ingest_status` is the durable record.

### `GET /api/videos` — list the catalog
Response: array of `{"id", "source_type", "source_uri", "source_key", "title",
"duration_seconds", "ingest_status", "has_media"}` — `has_media` = `local_path` exists
on disk (drives the playback fallback). Needs a `Catalog.list()`/`all()` method if one
doesn't exist yet — small, additive (see COORDINATION.md log).

### `GET /api/search?q=<text>&k=5` — run all four modalities
Response:
```json
{
  "visual":     [{"video_id", "t": <timestamp>, "score", "label": "<source_uri>"}],
  "caption":    [{"video_id", "t": <start_time>, "score", "label": "<caption text>"}],
  "transcript": [{"video_id", "t": <start_time>, "score", "label": "[speaker] text"}],
  "objects":    [{"video_id", "t": <first_seen>, "score": <max_confidence>,
                  "label": "<class>: N frames (first→last)"}]
}
```
One normalized hit shape (`video_id`, `t`, `score`, `label`) so the frontend renders all
columns with one component; `t` is what the player seeks to. Each modality is wrapped in
try/except so one failing path (e.g. nothing captioned) returns `[]` plus a per-column
`"note"` instead of failing the whole request.

### `GET /api/media/{video_id}` — serve the ingested file
Streams `videos.local_path` with HTTP **Range** support (required for `<video>` seeking).
Starlette's `FileResponse` handles Range on current versions — verify in the test; if our
pinned version doesn't, hand-roll a 206 handler (~20 lines). 404 if no `local_path`.

## Frontend (one page, three zones)

```
+--------------------------------------------------------------+
| [ video url or server path          ] [Ingest]  status: ●done |
| ingested: [▾ select video — title (status)                  ] |
+--------------------------------------------------------------+
|                      PLAYER                                   |
|   YouTube iframe  /  <video src=/api/media/{id}>              |
+--------------------------------------------------------------+
| [ query                              ] [Search] k:[5]         |
|  VISUAL      | CAPTION     | TRANSCRIPT  | OBJECTS             |
|  0.83  2:14  | 0.71  2:10  | 0.69  2:12  | squirrel: 47 fr     |
|  ...clicking any row seeks the player to its timestamp...     |
+--------------------------------------------------------------+
```

Behavior details:
- **Ingest flow:** submit → poll `/api/jobs/{id}` every 2s → pill shows
  `queued → running → done|failed (error)`; on done, refresh the video list and
  auto-select the new video.
- **Player selection logic** (per selected catalog row):
  `source_type == youtube` → IFrame API with `source_key` (the 11-char YouTube id — this
  is why the catalog's `source_key` semantics matter to the web layer);
  else if `has_media` → `<video src=/api/media/{id}>`;
  else if `source_uri` looks like a direct media URL → `<video src={source_uri}>`.
- **Hit click:** if the hit's `video_id` ≠ selected video, switch the player to that
  video first, then seek (`player.seekTo(t, true)` / `video.currentTime = t`). Search is
  workdir-global on purpose — that's useful when testing several videos at once.
- **Score display:** show raw scores; remember SigLIP scores are small (~0.11–0.18 for
  relevant) — the UI must not hide low-magnitude scores or color them as "bad".

## Ingest job queue (`jobs.py`)

- One `threading.Thread` worker draining a `queue.Queue` — GPU ingest must be serial.
- Job record: `id, uri, state, video_id, error, result, enqueued_at, started_at, ended_at`.
- The worker wraps `ingest()`; on exception it stores `str(e)` and marks `failed`
  (the catalog row is independently marked `failed` by `ingest()` itself).
- FastAPI runs the queue via the app factory's lifespan (start thread on startup,
  drain/stop on shutdown). No asyncio conversion of the pipeline — it stays sync,
  endpoints stay fast because work is on the worker thread.

## Testing (no GPU, no network — same rules as the rest of the suite)

`tests/test_web.py` with `fastapi.testclient.TestClient`, default stub config,
synthetic clips from `media/synth.py` written to tmp paths:

1. `POST /api/videos` with a synthetic clip path → poll job → `done`; catalog row `done`.
2. Re-submit same path → job result shows dedup (idempotency through the web layer).
3. `GET /api/search` returns all four keys; visual hits match the synthetic clip's color
   semantics (same assertion style as `test_e2e.py`).
4. `/api/media/{id}` → 200/206 with bytes; Range header request → 206 + correct slice.
5. Bad URI → job `failed` with error string; `GET /api/videos` still healthy.

## Implementation order

1. `pyproject.toml` `[web]` extra + `va serve` subcommand skeleton (app factory, lifespan).
2. `jobs.py` queue + tests for it directly.
3. API routes (`videos`, `jobs`, `search`, `media`) + TestClient tests.
4. `static/` page: ingest flow first, then player wiring, then search columns + seek.
5. Manual GPU pass: `VA_CONFIG_DIR=run-siglip/config .venv/bin/va serve --workdir .va`,
   ingest a real YouTube video from the laptop browser, verify seek-on-click.
6. Update CLAUDE.md commands block + log completion in COORDINATION.md.

## v2 (explicitly deferred)

- Laptop file upload (`POST /api/upload`, multipart, size cap) → ingest the uploaded file.
- Score threshold / relevance cutoff once golden-query calibration exists.
- Progressive escalation UI (planner-driven tiers streaming in) once Role 11 lands —
  the four-column layout is the natural seed for tier badges.
