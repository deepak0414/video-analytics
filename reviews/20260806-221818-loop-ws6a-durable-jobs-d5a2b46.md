# Agent review — request_changes

date: 2026-08-06T22:22:15.036134
range: origin/main..HEAD
branch: loop/ws6a-durable-jobs
findings: 2

- **major** `src/va/web/jobs.py:190` — The poison-job guard calls bump_attempts on every pending row, including `queued` rows that never executed, so jobs queued behind a poison (or merely long-running) job accrue attempts in lockstep and are permanently failed with a false 'repeatedly died mid-run' error.
  - scenario: Poison job at queue head under systemd Restart=always with jobs B/C queued behind it: each restart bumps poison AND B/C (poison crashes the process before they run); on the 4th restart poison is capped out and B/C are failed in the same resume loop despite never having run once. Fix: bump/check attempts only when r['state'] == 'running' (the only crash evidence); re-enqueue queued rows without bumping, and extend test_poison_job_gives_up_after_attempt_cap with a queued neighbor that must survive and complete.
- **minor** `COORDINATION.md:533` — The WS6.a coordination entry predates the round-4 attempts-cap fix: it omits the `attempts` column and the give-up-after-MAX_RESUME_ATTEMPTS policy that qualifies the entry's own 'resumes exactly once' promise and introduces a new user-visible failure string.
  - scenario: The web agent builds the jobs UI from this entry, does not model a pending job transitioning to failed without ever running ('gave up after 3 resume attempts'), and mis-renders or mis-handles that state. Fix: amend the entry with the attempts column and the resume-cap policy.

---

## Full review

Prior rounds verified the shard-unlink placement, schema-v7 recipe, and test integrity; I re-checked those independently and they hold at this HEAD. The round-4 minor (no attempt cap) was addressed by the new `bump_attempts` guard — but the implementation of that fix introduces a new defect, which is my main finding.

## Findings

### 1. major — `src/va/web/jobs.py:190` — the poison-job guard bumps attempts on `queued` rows, so innocent never-run jobs get failed as collateral

`IngestQueue._resume()` calls `bump_attempts` unconditionally for **every** pending row, but only a `running` row is crash evidence — the guard's own comment says so ("a job that KILLS the process … leaves `running` behind"). A `queued` row by definition never executed, yet it accrues a resume attempt on every restart.

Concrete failure — and it is exactly the scenario the guard was built for: a poison job at the head of the queue under systemd `Restart=always`, with jobs B, C submitted behind it. Each restart's resume loop bumps poison **and** B and C (they never get a turn; the poison job crashes the process first, FIFO by `created_at`). On the 4th restart, poison hits `attempts=4 > 3` and is failed — and B and C, also at 4, are failed **in the same loop** with the message "this job repeatedly died mid-run", which is false: they never ran once. The guard designed to contain one poison job instead destroys every job queued behind it, silently, with a misleading error. The same collateral occurs with a benign long-running job at the head plus a few deploy restarts.

Safe path: bump and check attempts only when `r["state"] == "running"`; re-enqueue `queued` rows without bumping (they carry no crash evidence). Extend `test_poison_job_gives_up_after_attempt_cap` with a `queued` neighbor row and assert it is resumed and completes while the poison row is failed — the current test seeds only the single poison row, so it cannot catch this.

### 2. minor — `COORDINATION.md:530` (WS6.a entry) — the logged contract omits the attempts cap that qualifies its own "exactly once" promise

The COORDINATION.md entry (and the CLAUDE.md serve comment) still describe the pre-round-4 behavior: the column list omits `attempts`, and "restart RESUMES queued/running ingest jobs exactly once" is now qualified by a give-up-after-3-resumes policy that surfaces a new user-visible error string ("gave up after 3 resume attempts…") the web agent's UI will display. Scenario: the web agent builds the jobs listing/UI from this entry, doesn't know a pending job can transition to failed without ever running under their poll, and treats the state as impossible. Safe path: amend the WS6.a entry with the `attempts` column and the `MAX_RESUME_ATTEMPTS` policy (one sentence each).

## Verified and dissolved (not findings)

- The `ingest.py:504` shard unlink: sits after the `done`-dedup return, before both the frame store and `_capture_appearance` (line 628), filenames match `NumpyFlatVectorStore`'s `.npz`/`.json` exactly; `reingest` rmtree's the dir and `reprocess` uses temp+`swap_shard`, so neither path conflicts. It also fixes a pre-existing retry-duplication bug for failed-after-persist ingests.
- `JobStore` on a fresh path works because `schema.connect()` applies the schema; migration v7 follows the recipe (`ALL_TABLES` + idempotent `_m7_jobs`, count assert holds).
- The resume test's monkeypatch is effective (`_process` imports `ingest` at call time) and would fail on non-interception (`calls == 0`).
- Degraded-mode, malformed-row, and ask-fail-on-restart policies each have a real test that fails on the pre-fix code.
- Combination coverage: the durability layer is backend/profile-orthogonal (file-level unlink identical across configs); stub-path tests suffice.
- No relevant dispute in `workflow-trust-plan.md` for this branch's findings.

Verdict: request_changes (one major).

```json
{"verdict": "request_changes", "findings": [
  {"severity": "major", "file": "src/va/web/jobs.py", "line": 190,
   "issue": "The poison-job guard calls bump_attempts on every pending row, including `queued` rows that never executed, so jobs queued behind a poison (or merely long-running) job accrue attempts in lockstep and are permanently failed with a false 'repeatedly died mid-run' error.",
   "scenario": "Poison job at queue head under systemd Restart=always with jobs B/C queued behind it: each restart bumps poison AND B/C (poison crashes the process before they run); on the 4th restart poison is capped out and B/C are failed in the same resume loop despite never having run once. Fix: bump/check attempts only when r['state'] == 'running' (the only crash evidence); re-enqueue queued rows without bumping, and extend test_poison_job_gives_up_after_attempt_cap with a queued neighbor that must survive and complete."},
  {"severity": "minor", "file": "COORDINATION.md", "line": 533,
   "issue": "The WS6.a coordination entry predates the round-4 attempts-cap fix: it omits the `attempts` column and the give-up-after-MAX_RESUME_ATTEMPTS policy that qualifies the entry's own 'resumes exactly once' promise and introduces a new user-visible failure string.",
   "scenario": "The web agent builds the jobs UI from this entry, does not model a pending job transitioning to failed without ever running ('gave up after 3 resume attempts'), and mis-renders or mis-handles that state. Fix: amend the entry with the attempts column and the resume-cap policy."}
]}
```
