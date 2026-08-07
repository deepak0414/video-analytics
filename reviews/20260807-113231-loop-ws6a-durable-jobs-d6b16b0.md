# Agent review — approve

date: 2026-08-07T11:37:15.999082
range: origin/main..HEAD
branch: loop/ws6a-durable-jobs
findings: 1

- **minor** `src/va/web/jobs.py:192` — Startup resume assumes this process is the sole owner of the jobs table, but nothing documents or enforces one server per workdir — a second `va serve` on the same workdir auto-steals the first server's in-flight running job.
  - scenario: Operator launches a second `va serve` against .va-shots while the first is mid-ingest: the new server's _resume reads the live `running` row as a crash artifact, bumps attempts, and re-runs the same ingest concurrently — duplicated GPU work, and after ~3 accidental double-starts the healthy job's row can cross MAX_RESUME_ATTEMPTS and surface as 'gave up' despite the first server completing it.

---

## Full review

The tree at HEAD is byte-identical to the round-9-approved commit `cb28897` (the `git diff cb28897..HEAD` is empty — `d6b16b0` is a squash of it), and no WS6.a disputes exist in the plan ledger. I still performed an independent pass rather than rubber-stamping. Here is the review.

# Review — origin/main..HEAD (`d6b16b0`, WS6.a durable job queue, round 10)

## Verdict: approve (one minor finding)

## What I verified independently

**Correctness of the restart machinery.** I traced every state transition path in `src/va/web/jobs.py` against `jobs_store.py` and the schema:

- The resume/attempts arithmetic is consistent: only `running` rows bump (crash evidence), the bump-then-`> MAX_RESUME_ATTEMPTS` comparison allows exactly 3 resumes before terminal failure, and `queued` rows behind a poison job accrue nothing (pinned by `test_poison_job_gives_up_after_attempt_cap`, including the innocent-job assertion `attempts == 0`).
- The `stop()`/worker race is correctly guarded: `requeue_if_running` is a conditional UPDATE (`AND state = 'running'`), so a requeue racing the worker's terminal write is a no-op on `done`/`failed` rows — pinned by `test_requeue_if_running_never_reverts_terminal_states`. The residual window (worker between `_q.get()` and `self._current = job_id` at join-timeout) is microseconds wide and self-healing under the cap; round 9 examined the adjacent drain window and declined, and I concur.
- `_persist` degrading to `None` on a broken store correctly disables the cap check (`attempts is not None` guard) rather than crashing or mis-failing — degraded mode has no durable state to crash-loop on.
- One JSON-corrupt payload costs one job, not the boot: `JobStore._row` catches the parse and routes to the malformed-row terminal-fail path; `pending()` order-by on mixed timestamp formats (isoformat vs `datetime('now')`) is cosmetically inconsistent but harmless.
- Monkeypatch targets in the tests are sound: both `_process` methods import `ingest`/`ask` inside the function body, so module-attribute patches take effect.

**The ingest-side resume safety** (`src/va/pipeline/ingest.py:505-521`): the shard delete is correct for every path that reaches it — the `done` dedup return at line 233 filters completed rows, so any surviving shard content is by definition a partial prior attempt, and this also fixes the pre-existing CLI-retry duplication (a mid-kill retry via plain `va ingest` would previously double every appended vector). The appearance-ref nulling lives in a swallow-all except but has a dedicated regression test that fails on pre-fix code (`refs_before >= 1` asserted before forging the mid-kill state).

**Schema/migration**: v7 is additive, `_m7_jobs` is idempotent (`CREATE TABLE IF NOT EXISTS`), fresh DBs get `jobs` via `ALL_TABLES`, v6 DBs via the migration under `BEGIN IMMEDIATE`, and `SCHEMA_VERSION == len(MIGRATIONS)` holds.

**Contract/docs**: the cross-ownership edit of web-owned `jobs.py` is flagged with ⚠ in COORDINATION.md, the web-facing API surface (`submit`/`get`/`to_dict`, endpoint shapes) is genuinely unchanged, the "get() reads memory only / history listing is future web-agent work" limitation is stated rather than hidden, and CLAUDE.md documents the restart semantics at the `va serve` command. `MAX_RESUME_ATTEMPTS` is hardcoded structure (a budget knob), explicitly flagged as such — compliant with the hardcoding rule.

**Test integrity/combination coverage**: the diff is purely additive outside `jobs.py`'s own rewrite; the queue/table layer is config-orthogonal (no role/backend/profile branching), and the one combination the ingest-side fix actually runs under (stub detector/tracker with a footage-profile vocab) is what the appearance-ref test exercises.

## The one finding

**Minor — undocumented single-server-per-workdir assumption.** `IngestQueue._resume` (src/va/web/jobs.py:192) makes cross-process interference automatic where it used to require a user action: a second `va serve` on the same workdir now reads the first server's live `running` row as a crash artifact at startup, bumps its attempts, and re-runs the same ingest concurrently. Consequences are self-healing (idempotent ingest, last-write-wins on the row, the torn-pair detection in the vector store) but include duplicated GPU work and — after repeated accidental double-starts — a healthy job's row crossing the poison cap. Nothing prevents or documents this; the NVR single-recorder assumption got a doc line for the same class of risk. Safe path: one sentence in the WS6.a COORDINATION entry (or the CLAUDE.md serve comment) stating one server per workdir, resume assumes it is the sole owner of the jobs table.

This is below the request-changes bar: the scenario requires operator error that was already unsupported, and every interleaving I traced converges to a correct final state.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/web/jobs.py", "line": 192, "issue": "Startup resume assumes this process is the sole owner of the jobs table, but nothing documents or enforces one server per workdir — a second `va serve` on the same workdir auto-steals the first server's in-flight running job.", "scenario": "Operator launches a second `va serve` against .va-shots while the first is mid-ingest: the new server's _resume reads the live `running` row as a crash artifact, bumps attempts, and re-runs the same ingest concurrently — duplicated GPU work, and after ~3 accidental double-starts the healthy job's row can cross MAX_RESUME_ATTEMPTS and surface as 'gave up' despite the first server completing it."}]}
```
