# Agent review — approve

date: 2026-08-07T12:07:56.172841
range: origin/main..HEAD
branch: loop/ws6b-watermark-backfill
findings: 1

- **minor** `src/va/pipeline/watch.py:299` — A never-watched camera with lookback_s <= settle_s derives a watermark at/past the horizon and is silently skipped on every pass with no warning and a still-NULL watermark
  - scenario: va watch --lookback-hours 0.03 (108s) with default --settle 120: horizon = now-120 <= watermark = now-108, the loop hits the early continue each pass, and the camera is never backfilled while the report shows it as merely quiet

---

## Full review

Static review is complete. I attempted to run the two changed test files myself, but test execution was permission-blocked in this session; the analysis below is static, cross-checked against the adapters, stores, plan docs, and the five prior review rounds (whose findings are all addressed in-code with regression tests).

## Review summary — `origin/main..HEAD` (WS6.b catch-up watcher)

**What I verified holds up:**

- **Watermark state machine** (`src/va/pipeline/watch.py`): I attacked the deferral/advance logic with several scenarios — straddling episodes, deferred-open instants ordered before/after settled episodes, budget truncation mid-episode, failure mid-split-episode, fractional epochs at the split boundary — and each is handled correctly and has a dedicated regression test in `tests/test_watch.py`. The `_window_uri` floor/ceil/clamp interplay is sound: a split window's clamp to `lo + MAX_WINDOW_S` is lossless because the next window's floored start is exactly that value, and the <1 s unsplit-tail loss is an acknowledged, in-code-documented tolerance.
- **Contracts**: `MotionSource.events(start_epoch, end_epoch, camera_ref)` matches both adapters (lnr compares the display-channel string; sidecar filters on `camera_ref` — and `NvrRecordedSource.resolve` registers cameras with `source_ref=str(chan)`, so `_window_uri(cam.source_ref, …)` produces valid `nvr://<chan>/…` URIs). `IngestResult.deduped` exists and means what the budget logic assumes. `ingest()` derives the `security` profile from the nvr source type internally, so watcher-driven ingests get the right footage profile without the CLI's `--profile` plumbing.
- **Schema v8**: additive `add_column` migration, `MIGRATIONS` length assertion updated, and `tests/test_migrations.py` is version-driven so v8 is covered by the existing fresh-DB/migrated-DB equivalence tests. `set_watermark` is monotonic in SQL, so concurrent/stale writers can't rewind. Logged in COORDINATION.md.
- **`web/jobs.py` changes**: the revert-to-queued preserves `attempts` (`JobStore.update` doesn't touch that column), the poison-cap and innocent-job tests are intact, and the new revert behavior and the pollable-failed-ask behavior each gained a test.
- **Plan conformance**: WS6.b's "Done when" (simulated-outage test backfills exactly the gap windows once) is met by `test_outage_backfills_exactly_the_gap_once`; the ~6-day SLA is documented in CLAUDE.md, COORDINATION.md, and the module docstring (digest is a finalize-time item).
- **Docs**: all seven new CLI flags plus the watch command are in CLAUDE.md; no new env vars.

**Known carried item (not re-reported):** the missing single-instance guard for `va watch`/`va serve` is already recorded as a carried minor in `architecture-evolution-loop.md` (round-6 approve).

**One new minor finding:** in `catch_up`, a never-watched camera whose derived watermark (`now - lookback_s`) lands at or past the horizon (`now - settle_s`) — i.e. `lookback_s <= settle_s`, reachable via `va watch --lookback-hours 0.03` with the default `--settle 120` — is silently skipped every pass, forever: `horizon <= watermark` hits the early `continue`, the watermark stays NULL, and no warning distinguishes this from "quiet camera." Safe path: warn (or clamp `lookback_s` to at least `settle_s`) when a NULL-watermark camera's derived range is empty.

The provisional `need_agent_review:` subject is exempt from the commit-clarity rule. Note the caveat that I could not execute the test suite in this session (sandbox denied `pytest`); the Stop-gate and CI `offline-tests` check remain the executing gates for suite state.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/watch.py", "line": 299, "issue": "A never-watched camera with lookback_s <= settle_s derives a watermark at/past the horizon and is silently skipped on every pass with no warning and a still-NULL watermark", "scenario": "va watch --lookback-hours 0.03 (108s) with default --settle 120: horizon = now-120 <= watermark = now-108, the loop hits the early continue each pass, and the camera is never backfilled while the report shows it as merely quiet"}]}
```
