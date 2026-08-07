# Agent review — approve

date: 2026-08-06T22:15:08.013152
range: origin/main..HEAD
branch: loop/ws6a-durable-jobs
findings: 1

- **minor** `src/va/web/jobs.py:171` — Resume has no attempt cap and no terminal transition for unrunnable jobs: a job that kills the process (OOM-kill, native segfault — paddle precedent on this box) leaves a `running` row that every restart resumes and re-crashes, and a malformed row stays `queued` in the table forever, skipped-with-warning on every boot.
  - scenario: A huge video OOM-kills the server mid-ingest; the failed update never persists, the row stays `running`, and under systemd Restart=always the server enters a crash loop resuming the same poison job — pre-WS6.a a restart cleared it. Add an `attempts` column incremented on resume with a small cap (mark `failed` with 'gave up after N attempts'), and mark unresumable rows `failed` inside the per-row except instead of leaving them pending.

---

## Full review

All prior-round findings are verifiably closed at this HEAD, and my independent re-derivation of the load-bearing claims held up. One new minor remains. Summary of what I verified and the one finding:

**Verified sound (re-derived, not trusted from prior rounds):**
- The round-1 major (append-on-resume doubling vector shards) is fixed by the unlink at `src/va/pipeline/ingest.py:504-506`: it sits after the `done` dedup return (`ingest.py:232`) and before both the frame store construction (`ingest.py:509`) and the appearance capture (`ingest.py:628` → store built in `_capture_appearance` at line 124), and the filenames match `NumpyFlatVectorStore._vec_file`/`._payload_file` exactly. `va reingest` rmtree's the video dir and `va reprocess` rebuilds via temp+`swap_shard` (`reprocess.py:134-173`), so neither conflicts with the unlink.
- Schema v7 follows the migration recipe: `JOBS` in `ALL_TABLES` for fresh DBs, idempotent `CREATE IF NOT EXISTS` via `_m7_jobs` for v6 DBs under `BEGIN IMMEDIATE`, the `MIGRATIONS`-count assert holds, and `connect()` provides the `row_factory` that `JobStore._row` depends on.
- The round-3 minor is closed: `test_malformed_row_costs_one_job_not_the_server` seeds a payload-`{}` row beside a valid one for both queues and would fail on the pre-guard code (the `KeyError` escaped `start()`), satisfying the fail-on-old-code lesson.
- The resume test's monkeypatch intercepts (`_process` imports `ingest` at call time); the exactly-once assertions are race-free because the queue is FIFO (resumed ids precede the stop sentinel); the web-facing surface is unchanged (`app.py:52-53` constructor calls still valid); COORDINATION.md carries the cross-boundary ⚠ entry with the corrected precise wording; CLAUDE.md documents the new serve semantics; no new env vars or CLI flags exist. No relevant dispute in workflow-trust-plan.md. Combination coverage is genuinely orthogonal here — the durability layer touches no role backend, and the unlink is file-level and identical across configs, so stub-path tests suffice.

**One minor finding — no terminal transition for jobs that can never finish (`src/va/web/jobs.py:171-193`).** Two flavors: (a) a job whose processing kills the *process* rather than raising — OOM-kill on a huge video, a native-lib segfault (real precedent on this box: paddlepaddle), CUDA aborts — never reaches the `failed` update, so its row stays `running` and every restart resumes it and crashes again; under systemd `Restart=always` that is a hot crash loop, and pre-WS6.a a crash-inducing job vanished on restart. (b) A malformed row is skipped-with-warning but left `queued` in the table forever, re-logged on every boot. Safe path for both: an `attempts` column incremented on resume with a small cap (mark `failed` with "gave up after N attempts"), and mark unresumable rows `failed` in the per-row except. This bounds the blast radius of a poison job without weakening the resume guarantee.

No critical or major findings, so this passes.

```json
{"verdict": "approve", "findings": [
  {"severity": "minor", "file": "src/va/web/jobs.py", "line": 171,
   "issue": "Resume has no attempt cap and no terminal transition for unrunnable jobs: a job that kills the process (OOM-kill, native segfault — paddle precedent on this box) leaves a `running` row that every restart resumes and re-crashes, and a malformed row stays `queued` in the table forever, skipped-with-warning on every boot.",
   "scenario": "A huge video OOM-kills the server mid-ingest; the failed update never persists, the row stays `running`, and under systemd Restart=always the server enters a crash loop resuming the same poison job — pre-WS6.a a restart cleared it. Add an `attempts` column incremented on resume with a small cap (mark `failed` with 'gave up after N attempts'), and mark unresumable rows `failed` inside the per-row except instead of leaving them pending."}
]}
```
