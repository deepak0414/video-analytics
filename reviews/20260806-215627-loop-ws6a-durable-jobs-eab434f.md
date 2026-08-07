# Agent review — approve

date: 2026-08-06T22:01:32.988798
range: origin/main..HEAD
branch: loop/ws6a-durable-jobs
findings: 2

- **minor** `src/va/web/jobs.py:177` — The per-row rebuild loops in IngestQueue._resume/AskQueue._resume are outside the best-effort _persist guard, so a jobs row whose payload parses but lacks the expected key (uri/question) raises KeyError out of start() inside the FastAPI lifespan and the server fails to boot — contradicting the stated 'broken jobs table degrades to memory-only, never a dead queue' promise.
  - scenario: A hand-edited or future-build jobs row with kind='ingest' and payload '{}' survives json.loads, then r['payload']['uri'] raises KeyError during lifespan startup and va serve dies before serving a single request; wrap each row's rebuild in try/except-log-continue so one bad row costs one job, not the server.
- **minor** `COORDINATION.md:543` — The cross-agent log entry claims the web agent 'should only notice that job history survives restarts', but SerialQueue.get() reads memory only and pre-restart done/failed jobs are never rebuilt into it — only the DB rows survive, which no web-facing path reads.
  - scenario: The web agent, following the entry, builds a job-history panel that re-polls GET /api/jobs/<id> after a server restart; every pre-restart completed job returns 404 exactly as before WS6.a. Reword the entry (rows survive; endpoints still serve memory) or add a store fallback in get() and document that instead.

---

## Full review

Review complete. This is the round-2 state of WS6.a, and it holds up: round 1's major (resume re-running `ingest()` against a persisted partial shard, doubling every frame/appearance vector via the load-and-append stores) is fixed by deleting `vectors.*`/`appearance.*` at the top of the embed section for any not-`done` row — I verified the placement is sound (the `done` dedup return at `ingest.py:233` precedes it, the frame store is constructed at `ingest.py:509` after the unlink, `_capture_appearance` builds its store at `ingest.py:124` from the call at line 628, also after it, and the text index already uses temp+`swap_shard` so it needs no cleanup). Round 1's minor is fixed by `test_resume_after_kill_with_partial_shard_does_not_duplicate_vectors`, which forges exactly the mid-kill state (persisted shard + `processing` row + `running` job) and would fail on the pre-fix code (baseline 3 payloads would double to 6), satisfying the fail-on-old-code lesson and the plan's "idempotency asserted" Done-when at the data level, not just call-count.

Things I checked that dissolved: schema v7 migration is by-the-book (fresh DB gets `JOBS` via `ALL_TABLES`, v6 DB gets it via `_m7_jobs` under `BEGIN IMMEDIATE`, `CREATE IF NOT EXISTS` makes the overlap idempotent, and the `MIGRATIONS`-count assert still holds); the older-build-opens-v7 case hits the existing additive-schema warning path; `_persist` opens a fresh short-lived connection per call so no SQLite cross-thread sharing; a broken store degrades with a warning (pinned by `test_no_workdir_store_failure_degrades_to_memory`); the second-restart assertion is race-free because the queue is FIFO (resumed ids precede the stop sentinel); `IngestJob(..., id=...)` keyword construction is valid dataclass usage; the unlink filenames match `NumpyFlatVectorStore._vec_file`/`_payload_file` exactly; the shard cache keys on `.npz` mtime so the delete-and-rebuild invalidates correctly; the web layer's constructor and endpoint surface are genuinely unchanged (`app.py` untouched); no new env vars or CLI flags exist to document, and CLAUDE.md/COORDINATION.md carry the new semantics; the provisional `need_agent_review:` subject is exempt from the clarity rule.

Two minors remain:

**1. Minor — `src/va/web/jobs.py:177` (and the ask twin at 249): the per-row rebuild in `_resume` sits outside the best-effort guard, so one malformed-but-parseable jobs row kills server startup.** `_persist("pending", ...)` protects against a broken table and unparseable JSON, but a row whose payload parses and lacks the expected key — `r["payload"]["uri"]` / `r["payload"]["question"]` (hand-edited row, or a row written by a future build with a different payload shape) — raises `KeyError` out of `_resume()`, which runs inside `start()` in the FastAPI lifespan: the server fails to boot. That contradicts the module's explicit promise ("a broken jobs table degrades to the old memory-only behavior with a warning, never a dead queue"). Safe path: wrap each row's rebuild in try/except-log-continue (optionally marking that row failed in the store) so one bad row costs one job, not the whole server.

**2. Minor — `COORDINATION.md:543`: the cross-agent log overstates what survives a restart, in the one place the web agent is told what changed.** "the web agent should only notice that job history survives restarts" — but `SerialQueue.get()` reads only `self._jobs` (memory), and jobs that were already `done`/`failed` before the restart are never rebuilt into memory (only pending ingests and restart-failed asks are). `GET /api/jobs/<id>` for a pre-restart completed job still 404s exactly as pre-WS6.a; only the DB rows survive, which nothing web-facing reads. Risk: the web agent builds UI (e.g. a persistent job-history panel polling old ids) on a durability the endpoints don't provide. Safe path: reword the entry to "rows survive in the jobs table; the polling endpoints still serve memory only," or make `get()` fall back to `_persist("get", job_id)` and say so.

Neither is critical or major, so this passes.

```json
{"verdict": "approve", "findings": [
  {"severity": "minor", "file": "src/va/web/jobs.py", "line": 177,
   "issue": "The per-row rebuild loops in IngestQueue._resume/AskQueue._resume are outside the best-effort _persist guard, so a jobs row whose payload parses but lacks the expected key (uri/question) raises KeyError out of start() inside the FastAPI lifespan and the server fails to boot — contradicting the stated 'broken jobs table degrades to memory-only, never a dead queue' promise.",
   "scenario": "A hand-edited or future-build jobs row with kind='ingest' and payload '{}' survives json.loads, then r['payload']['uri'] raises KeyError during lifespan startup and va serve dies before serving a single request; wrap each row's rebuild in try/except-log-continue so one bad row costs one job, not the server."},
  {"severity": "minor", "file": "COORDINATION.md", "line": 543,
   "issue": "The cross-agent log entry claims the web agent 'should only notice that job history survives restarts', but SerialQueue.get() reads memory only and pre-restart done/failed jobs are never rebuilt into it — only the DB rows survive, which no web-facing path reads.",
   "scenario": "The web agent, following the entry, builds a job-history panel that re-polls GET /api/jobs/<id> after a server restart; every pre-restart completed job returns 404 exactly as before WS6.a. Reword the entry (rows survive; endpoints still serve memory) or add a store fallback in get() and document that instead."}
]}
```
