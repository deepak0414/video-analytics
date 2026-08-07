# Agent review — approve

date: 2026-08-06T23:52:25.027236
range: origin/main..HEAD
branch: loop/ws6b-watermark-backfill
findings: 1

- **minor** `src/va/pipeline/watch.py:92` — va watch is advertised as cron-friendly (--interval 0) but has no single-instance guard, so overlapping cron passes read the same watermark and concurrently double-pull/double-ingest the same windows.
  - scenario: Cron fires every minute; a backlogged pass (50-window default budget at ~90 s/clip with real models) is still running when the next fires; both pull the same episodes — doubled NVR/GPU work and SQLite lock contention that surfaces as spurious 'ingest failed — watermark held' retries. Fix: take a non-blocking flock on <workdir>/watch.lock at the top of catch_up and skip the pass if held, or document that cron entries must wrap the command in `flock -n`.

---

## Full review

Review complete. This branch (WS6.a durable job queue + WS6.b catch-up watcher) has been through 13+ prior review rounds; I verified the fixes claimed at HEAD independently rather than trusting the annotations, and probed the fresh surface for new defects.

**What I verified holds:**

- Both prior-round minor findings are genuinely fixed at HEAD: `_window_uri` now floors start / ceils end with a degenerate-window guard (pinned by `test_fractional_subsecond_event_pulls_widened_window`), and the `--max-windows` help text now states the per-camera split and the ≥1-per-camera floor.
- Both plan "Done when" oracles exist and are exact: `test_crashed_running_job_resumes_exactly_once` (kill-mid-job, resume once, second restart no-op) and `test_outage_backfills_exactly_the_gap_once` (exact gap windows, idempotent second pass). The ~6-day ring SLA is documented in CLAUDE.md, COORDINATION.md, and the module docstring.
- Schema goes v6→v8 via ordered migrations; fresh-create (`ALL_TABLES` includes `JOBS`, `CAMERAS` includes the watermark column) and migrated paths agree; `assert len(MIGRATIONS) == SCHEMA_VERSION` still holds.
- The deferral logic runs on **raw** events before clustering (I confirmed `cluster_events` keeps only the first event's attributes, so the round-3 merge-loses-open-signature critical is real and the fix is correctly placed); the lnr adapter emits lost-End episodes as `open: True` zero-length instants and its "neither marker" instants have `end == start`, both caught by the watcher's `open_instant` predicate. The nvr-access-notes confirm paired Start/End entries are the norm, so the defer-then-recover path is the exception, not steady state.
- The straddle filter (`start_epoch >= watermark`) composes correctly with deferral because the watermark is capped **at** the deferred start, so `>=` re-includes it next pass; the quiet-advance target is always ≥ any per-episode advance, and `set_watermark`'s SQL is itself monotonic, so the stale-read comparison at `watch.py:225` can't rewind.
- Camera registration at nvr resolve does set `source_ref`, so registered cameras pass the watcher's filter; `get_or_create` is INSERT OR IGNORE and never clobbers the watermark.
- On the jobs side: bump-only-on-`running` keeps queued innocents guilt-free (pinned), the revert-to-queued-at-resume prevents flap-driven cap exhaustion (pinned), `requeue_if_running` is guarded against the terminal-write race (pinned), corrupt JSON payloads cost one job not the boot (pinned), and `update(..., "queued")` wiping error/result is correct for a re-run. The cross-boundary edit of web-owned `jobs.py` is flagged in COORDINATION.md.
- The partial-shard deletion in `ingest.py` is safe for fresh ingests (missing_ok unlink, no-op UPDATE) and the appearance-ref nulling has its own regression test that defeats the swallow-all except.

Things I probed that dissolved: monkeypatched `ingest` works because both call sites import inside the function at call time; `_wait` timeouts vs real stub ingests; deduped replays not consuming budget (pinned by `len(pulls) == 5`); vector-store duplication under concurrent same-video ingest resolves to last-writer-wins full rewrites (each process's store loads once at construction after its own deletion), not doubling.

I did not launch a test run — two pytest processes are already live (repo lesson: never stack suite runs); the session log records 678 passed / 2 skipped at the WS6.a provisional and the prior review rounds ran green.

**One new finding (minor):**

`va watch --interval 0` is explicitly advertised as cron-friendly (CLI help, CLAUDE.md), but there is no single-instance guard anywhere in `watch.py`/`cli.py` — no flock, no pidfile. With real models, a backlogged pass (default budget 50 windows × ~90 s/clip real-model ingest) will far outlast any sane cron period; the next cron firing reads the same not-yet-advanced watermark and pulls+ingests the same windows concurrently. I verified this can't corrupt state (shards are last-writer-wins, the watermark is monotonic in SQL), so the cost is doubled NVR pulls and GPU-hours plus SQLite lock contention surfacing as spurious "ingest failed — watermark held" retries. Safe path: take a non-blocking exclusive lock (e.g. `flock` on `<workdir>/watch.lock`) at the top of `catch_up` and skip the pass if held — or document in CLAUDE.md's watch entry that cron installs must wrap the command in `flock -n`.

Both commits are provisional `need_agent_review:` subjects — exempt from the finalized-message clarity rule; note for the finalize digest that the eventual squashed message should describe both the durable queue and the watcher in plain terms.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/watch.py", "line": 92, "issue": "va watch is advertised as cron-friendly (--interval 0) but has no single-instance guard, so overlapping cron passes read the same watermark and concurrently double-pull/double-ingest the same windows.", "scenario": "Cron fires every minute; a backlogged pass (50-window default budget at ~90 s/clip with real models) is still running when the next fires; both pull the same episodes — doubled NVR/GPU work and SQLite lock contention that surfaces as spurious 'ingest failed — watermark held' retries. Fix: take a non-blocking flock on <workdir>/watch.lock at the top of catch_up and skip the pass if held, or document that cron entries must wrap the command in `flock -n`."}]}
```
