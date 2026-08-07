# Agent review — approve

date: 2026-08-06T22:40:02.740946
range: origin/main..HEAD
branch: loop/ws6a-durable-jobs
findings: 2

- **minor** `src/va/web/jobs.py:153` — stop()'s unconditional requeue can land after the worker's terminal write (join timed out milliseconds early), reverting a done/failed row to queued and clearing its result — the 'last write wins' comment misses this interleaving.
  - scenario: An ingest finishes just as the 5s join expires: the worker persists 'done', then stop() persists 'queued' (result nulled); the next boot re-resumes an already-complete job (benign only via catalog dedup), and a raced ask is mislabeled 'resubmit'. Fix: guard the requeue with WHERE state='running' so it cannot overwrite a terminal state.
- **minor** `src/va/pipeline/ingest.py:504` — The resume-safety unlink deletes appearance.npz while prior-attempt object_tracks rows keeping appearance_ref survive if the tracker fails on the resumed run, leaving durable dangling refs into a nonexistent store.
  - scenario: Attempt 1 writes tracks+appearance, crashes during text indexing; the resume deletes appearance.npz, then the tracker OOMs (best-effort — old track rows kept): appearance_ref now points at a deleted shard, breaking the WS4.d invariant a future Role-12 consumer relies on. Fix: null appearance_ref (or purge prior-attempt tracks) for the video when deleting the appearance shard.

---

## Full review

No disputes touch WS6.a. My review is complete.

# Review — origin/main..HEAD (`aa19e6e`, WS6.a durable job queue, round 7)

## Prior-round follow-up

All three round-6 minors are fixed at this HEAD, each with a real test:

- Terminal-failed resume paths now rebuild pollable in-memory jobs via `IngestQueue._fail_in_memory` (`src/va/web/jobs.py:237`), covered by `test_malformed_row_costs_one_job_not_the_server` and the poison-cap test.
- A JSON-corrupt payload no longer blocks all resumes: `JobStore._row` catches the parse error and returns `payload=None` (`src/va/storage/structured/jobs_store.py:179-182`), routing the row to the terminal-fail path; `test_corrupt_json_payload_blocks_no_one` pins it.
- Graceful `stop()` now knocks the in-flight row back to `queued` so deliberate restarts don't march `attempts` toward the poison cap (`src/va/web/jobs.py:146-153`); `test_graceful_stop_requeues_without_attempt_guilt` pins it.

No disputes in `workflow-trust-plan.md` apply to this branch.

## Verified and dissolved (not findings)

- **Test execution:** a pytest run was already live (two PIDs), so per the 2026-08-04 lesson I did not launch another; I verified all eight new tests by inspection instead. Each monkeypatch interception is real (`_process` imports `ingest`/`ask` at call time), the corrupt-row/poison/innocent orderings work out (`pending()` orders by `created_at`; the crashed row is oldest), and the exactly-once oracle plus the partial-shard test would each fail on the pre-fix code.
- The poison cap's off-by-one is coherent: bumps 1–3 resume, the 4th restart terminal-fails with a message that matches ("gave up after 3 resume attempts").
- The `ingest.py:504` unlink covers exactly the two load-and-append shards; the text shard rebuilds via `swap_shard`, and `va reingest` is remove+ingest, so neither needs it. The unlink order (`.npz` before `.json`) is safe against concurrent readers, which gate on both files.
- Schema v7 follows the migration recipe (base DDL + idempotent `_m7_jobs` + count assert); `connect()` gives `JobStore` row access and migrations, so a v6 workdir picks up `jobs` with `attempts` on next open.
- Degraded mode (broken store → memory-only), the ask fail-on-restart policy, and the web-owned-file COORDINATION ⚠ entry all check out; API surface unchanged; combination coverage is genuinely config-orthogonal (SQLite + file level), so stub-path tests suffice; docs updated in-change; the `need_agent_review:` subject is exempt from the clarity rule.

## Findings

**1. minor — `src/va/web/jobs.py:153` — `stop()`'s requeue can overwrite a terminal state the worker wrote just after the join timed out.** The code comment claims "last write wins either way," but in the interleaving where the worker persists `done`/`failed` between the join timeout and `stop()`'s unconditional `update(current, "queued")`, stop's write is last: a completed job reverts to `queued` with its `result` cleared (`update()` nulls `error`/`result` when not passed). Consequence is small — the next boot re-resumes and ingest dedups on the done catalog row, and a raced ask is failed as "resubmit" — but the row is durably wrong until then. Safe path: make the requeue a guarded update (`UPDATE jobs SET state='queued' WHERE id=? AND state='running'`) so it can never clobber a terminal state; that also makes the comment true.

**2. minor — `src/va/pipeline/ingest.py:504` — deleting `appearance.npz` on resume can strand dangling `object_tracks.appearance_ref` pointers.** The prior crashed attempt may have already written track rows (with `appearance_ref`) plus the appearance shard; the resume unlinks the shard up front, and if the detector/tracker then fails on the resumed run (best-effort — old rows are kept, not purged), the surviving track rows reference a store that no longer exists. Latent today (no query path reads `appearance_ref` — it's Role-12 schema insurance), but it silently breaks the WS4.d invariant that a non-NULL ref points into `appearance.npz`, exactly the kind of workdir a future ReID consumer trips on. Safe path: when unlinking the appearance shard, also null `appearance_ref` on that video's track rows (or purge the prior-attempt tracks), so refs and store stay consistent under any resume outcome.

No critical or major findings.

```json
{"verdict": "approve", "findings": [
  {"severity": "minor", "file": "src/va/web/jobs.py", "line": 153,
   "issue": "stop()'s unconditional requeue can land after the worker's terminal write (join timed out milliseconds early), reverting a done/failed row to queued and clearing its result — the 'last write wins' comment misses this interleaving.",
   "scenario": "An ingest finishes just as the 5s join expires: the worker persists 'done', then stop() persists 'queued' (result nulled); the next boot re-resumes an already-complete job (benign only via catalog dedup), and a raced ask is mislabeled 'resubmit'. Fix: guard the requeue with WHERE state='running' so it cannot overwrite a terminal state."},
  {"severity": "minor", "file": "src/va/pipeline/ingest.py", "line": 504,
   "issue": "The resume-safety unlink deletes appearance.npz while prior-attempt object_tracks rows keeping appearance_ref survive if the tracker fails on the resumed run, leaving durable dangling refs into a nonexistent store.",
   "scenario": "Attempt 1 writes tracks+appearance, crashes during text indexing; the resume deletes appearance.npz, then the tracker OOMs (best-effort — old track rows kept): appearance_ref now points at a deleted shard, breaking the WS4.d invariant a future Role-12 consumer relies on. Fix: null appearance_ref (or purge prior-attempt tracks) for the video when deleting the appearance shard."}
]}
```
