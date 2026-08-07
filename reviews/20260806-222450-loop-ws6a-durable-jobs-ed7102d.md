# Agent review — approve

date: 2026-08-06T22:30:15.065921
range: origin/main..HEAD
branch: loop/ws6a-durable-jobs
findings: 3

- **minor** `src/va/web/jobs.py:196` — Give-up and malformed-row resume paths terminal-fail the jobs row without rebuilding the job in memory, so GET /api/jobs/{id} returns 404 instead of the failure string COORDINATION tells the web agent to expect on polled jobs (the ask side rebuilds in memory precisely to avoid this).
  - scenario: A poison ingest job crash-loops the server under systemd; after the cap fires, the browser still polling its job_id gets '404 no such job' rather than state=failed with 'gave up after 3 resume attempts', and a UI built from the COORDINATION entry mishandles it. Fix: insert a failed IngestJob into self._jobs in both terminal paths, mirroring AskQueue._resume, or amend the COORDINATION sentence.
- **minor** `src/va/storage/structured/jobs_store.py:179` — pending() json.loads every row's payload inside _row, so one JSON-corrupt payload raises before IngestQueue._resume's per-row guard can run — _persist swallows it, zero pending jobs resume, and the corrupt row is never terminal-failed, defeating the 'terminal, not skip-forever' rule and blocking all innocent pending jobs on every boot.
  - scenario: A hand-edited jobs row leaves payload as invalid JSON next to two healthy queued ingest jobs; every restart logs 'job persistence failed (pending)' and resumes nothing, and the healthy jobs stay queued forever. Fix: return the raw payload string from pending() and parse per-row inside _resume's try, or catch JSONDecodeError in _row and return payload=None so the malformed-row path fails it.
- **minor** `src/va/web/jobs.py:37` — Graceful shutdown is indistinguishable from a crash — stop() joins only 5s, so a long ingest survives as a 'running' row and every deliberate restart mid-job bumps attempts toward the cap.
  - scenario: A user restarts `va serve` four times while iterating on config during one hour-long NVR ingest; the healthy job is terminal-failed as 'repeatedly died mid-run' and must be resubmitted. Fix: on graceful stop(), knock the in-flight job's row back to 'queued' so attempts counts only genuine crash evidence.

---

## Full review

Test execution isn't permitted in this session (and another pytest run is live), so I verified the new tests by inspection instead; the round-5 delta (running-only bump + innocent-neighbor test + amended COORDINATION entry) is correctly implemented. Here is my review.

# Review — origin/main..HEAD (`ed7102d`, WS6.a durable job queue)

## Prior-round follow-up

Round 5's major (bumping `attempts` on `queued` rows) is fixed at this HEAD: `jobs.py:193-194` bumps only `running` rows, and `test_poison_job_gives_up_after_attempt_cap` now seeds the queued innocent neighbor and asserts `attempts == 0` plus completion. Round 5's minor (stale COORDINATION entry) is fixed by the AMENDED paragraph. No disputes in `workflow-trust-plan.md` apply to this branch.

## Verified and dissolved (not findings)

- The `ingest.py:504` shard unlink covers exactly the two load-and-append stores (`vectors`, `appearance`; filenames match `NumpyFlatVectorStore`'s `.npz`/`.json`), sits after the done-dedup return, and the third per-video shard (`text_vectors`) is safe without it — `index_text` rebuilds via temp + `swap_shard`.
- Schema v7 follows the migration recipe (base DDL + idempotent `_m7_jobs` + count assert); a v6 workdir picks up the `jobs` table with `attempts` on next open.
- The exactly-once oracle test monkeypatches `va.pipeline.ingest.ingest`, which `_process` imports at call time — the interception is real, and `calls == 1` plus the partial-shard test would each fail on the pre-fix code.
- Degraded mode (broken store → memory-only), malformed-row-costs-one-job, and ask-fail-on-restart each have genuine tests.
- Combination coverage: the durability layer is backend/profile-orthogonal (SQLite + file-level, identical across configs); stub-path tests suffice.
- Web-owned `src/va/web/jobs.py` edit is logged in COORDINATION with the ⚠ flag; API surface (submit/get/to_dict shapes) is unchanged.

## Findings

**1. minor — `src/va/web/jobs.py:196` (also :212) — terminal-failed resume paths produce 404s, contradicting the logged contract.** The give-up and malformed-row paths mark the row failed in the table but never rebuild the job into `self._jobs`, and `GET /api/jobs/{job_id}` reads memory only (`app.py:105-107`). COORDINATION explicitly tells the web agent "expect both new failure strings on polled jobs" — but a browser polling a poison job across restarts gets `404 no such job`, never the failure string. The ask side rebuilds failed records into memory precisely to avoid this 404. Safe path: insert a failed `IngestJob` into `self._jobs` in both terminal paths (mirroring `AskQueue._resume`), or amend the COORDINATION sentence to say these failures are table-only.

**2. minor — `src/va/storage/structured/jobs_store.py:179` — one JSON-corrupt payload blocks resume for all pending jobs, forever.** `pending()` parses every row's payload in `_row`, so a payload that isn't valid JSON (hand-edited row — the exact threat the per-row guard was built for) raises inside `pending()` itself, before `_resume`'s per-row try can run; `_persist` swallows it and returns None, so zero jobs are resumed, and the corrupt row is never terminal-failed — it stays pending and re-warns on every boot, with every innocent pending job blocked alongside it. Safe path: return the raw payload string from `pending()` and parse per-row inside `_resume`'s try (the existing except then terminal-fails it), or catch `json.JSONDecodeError` in `_row` and yield `payload=None`.

**3. minor — `src/va/web/jobs.py:37` (with `stop()` at :138-143) — deliberate restarts are indistinguishable from crashes, so the cap can terminal-fail a healthy long job.** `stop()` joins the worker for only 5 s; any real-model ingest outlives that, the process exits, and the row stays `running` — so every graceful restart mid-job bumps `attempts` exactly like a crash. Four config-iteration restarts during one long NVR ingest terminal-fail a healthy job with the false message "repeatedly died mid-run" (recovery is a manual resubmit). Safe path: on graceful `stop()`, knock the in-flight job's row back to `queued` (the queue knows this shutdown is deliberate), reserving `attempts` for genuine crash evidence.

No critical or major findings; the commit subject is a provisional `need_agent_review:` and exempt from the clarity rule.

```json
{"verdict": "approve", "findings": [
  {"severity": "minor", "file": "src/va/web/jobs.py", "line": 196,
   "issue": "Give-up and malformed-row resume paths terminal-fail the jobs row without rebuilding the job in memory, so GET /api/jobs/{id} returns 404 instead of the failure string COORDINATION tells the web agent to expect on polled jobs (the ask side rebuilds in memory precisely to avoid this).",
   "scenario": "A poison ingest job crash-loops the server under systemd; after the cap fires, the browser still polling its job_id gets '404 no such job' rather than state=failed with 'gave up after 3 resume attempts', and a UI built from the COORDINATION entry mishandles it. Fix: insert a failed IngestJob into self._jobs in both terminal paths, mirroring AskQueue._resume, or amend the COORDINATION sentence."},
  {"severity": "minor", "file": "src/va/storage/structured/jobs_store.py", "line": 179,
   "issue": "pending() json.loads every row's payload inside _row, so one JSON-corrupt payload raises before IngestQueue._resume's per-row guard can run — _persist swallows it, zero pending jobs resume, and the corrupt row is never terminal-failed, defeating the 'terminal, not skip-forever' rule and blocking all innocent pending jobs on every boot.",
   "scenario": "A hand-edited jobs row leaves payload as invalid JSON next to two healthy queued ingest jobs; every restart logs 'job persistence failed (pending)' and resumes nothing, and the healthy jobs stay queued forever. Fix: return the raw payload string from pending() and parse per-row inside _resume's try, or catch JSONDecodeError in _row and return payload=None so the malformed-row path fails it."},
  {"severity": "minor", "file": "src/va/web/jobs.py", "line": 37,
   "issue": "Graceful shutdown is indistinguishable from a crash — stop() joins only 5s, so a long ingest survives as a 'running' row and every deliberate restart mid-job bumps attempts toward the cap.",
   "scenario": "A user restarts `va serve` four times while iterating on config during one hour-long NVR ingest; the healthy job is terminal-failed as 'repeatedly died mid-run' and must be resubmitted. Fix: on graceful stop(), knock the in-flight job's row back to 'queued' so attempts counts only genuine crash evidence."}
]}
```
