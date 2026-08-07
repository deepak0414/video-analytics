# Agent review — request_changes

date: 2026-08-06T23:03:52.409343
range: origin/main..HEAD
branch: loop/ws6b-watermark-backfill
findings: 4

- **critical** `src/va/pipeline/watch.py:141` — An episode straddling the settle horizon is clipped (sidecar: tail lost) or skipped as a zero-length lnr open-instant (whole episode lost), then the watermark advances past its start so the line-133 filter drops it forever on every later pass.
  - scenario: Real lnr-eventlog backend, `va watch --interval 60`: a 5-minute driveway episode is still open at some pass's horizon; the adapter emits a zero-length start-anchored instant, line 142 skips it, the watermark advances to the horizon, and neither that pass nor any future pass ever pulls the footage — silent permanent loss of exactly the long, interesting events.
- **major** `src/va/pipeline/watch.py:138` — The max_windows budget is checked only between episodes, never inside the per-episode window loop, so one long clustered episode ignores the cap entirely.
  - scenario: After a multi-day outage, the LNR's chatty overnight log clusters (gap_s=30) into one multi-hour episode; a single `va watch` pass pulls hundreds of 120 s windows despite --max-windows 50, violating the plan's bounded-catch-up Done-when and starving other cameras for hours.
- **minor** `src/va/pipeline/watch.py:397` — run_watch's forever loop does not guard catch_up, so any exception escaping it (e.g. a transient sqlite error in CameraStore/_advance) kills the long-running watcher daemon.
  - scenario: A busy-timeout-exceeded 'database is locked' during _advance while a heavy web ingest holds the writer propagates out of catch_up and the unattended watcher process exits; no footage is pulled until a human notices.
- **minor** `src/va/pipeline/watch.py:113` — When a camera's first episode fails to ingest, CameraReport.watermark_after remains None even though the durable watermark is unchanged, so the CLI prints 'watermark -> None'.
  - scenario: One flaky pull on the first episode of a pass makes `va watch --interval 0` report 'watermark -> None' for that camera, misreporting the real held watermark to an operator checking catch-up progress.

---

## Full review

Review complete. I examined the WS6.a durable-job-queue and WS6.b catch-up-watcher commits, read the supporting motion-source adapters (sidecar + LNR event log), the schema migration machinery, and both new test files, and checked WS6 "Done when" items in `architecture-evolution-loop.md`.

**WS6.a (durable jobs) held up well** — the resume/poison-cap/graceful-stop races I chased (requeue vs. terminal write, corrupt payloads, degraded mode, the monkeypatch targets in the tests) all dissolve on reading: the guards exist and the tests genuinely exercise them. Schema v7/v8 migration is correctly ordered and idempotent.

**WS6.b (watcher) has a real footage-loss hole** plus a bound violation:

### Finding 1 (critical) — episodes straddling the horizon are permanently lost

`src/va/pipeline/watch.py:141-143` clips an episode to the horizon (`end = min(ep.end_epoch, horizon)`), ingests only `[start, horizon]`, then advances the watermark to the horizon (line 366/371 `_advance`). Next pass, line 133 filters `e.start_epoch >= watermark` — the episode's start is now behind the watermark, so its tail is never pulled. The comment on 131-132 ("already covered by the cycle that set the watermark") is false for this case: that cycle covered only up to the old horizon.

On the **real `lnr-eventlog` backend it's worse than a tail**: an episode still open at the horizon has no End marker in the queried range, so the adapter emits a start-anchored **zero-length instant** (`lnr_eventlog_inproc.py` range-end flush, `open: True`). Line 142 (`end <= ep.start_epoch`) skips it, the watermark advances past its start, and the later End-only marker also comes back as a zero-length `[end, end]` instant — skipped again. **The entire episode is never ingested.** With `--interval 60`, essentially every motion episode longer than the pass cadence (a delivery, someone mowing the lawn) lands mid-horizon on some pass and is silently dropped — the exact footage the A-LSSRVF product exists to capture. The tests cover only the sidecar source with episodes conveniently fully-inside or fully-beyond the horizon; no test has an episode straddling it, and the lnr open-instant interplay has zero coverage.

Safe path: treat an episode whose `end_epoch >= horizon` (or an lnr `open`/zero-length instant) as not yet settled — stop that camera's pass there and hold the watermark at the last fully-settled episode, exactly like the failed-window path, so the next pass re-queries and pulls it complete once closed. Add a straddling-episode test on both the sidecar shape and the lnr open-instant shape.

### Finding 2 (major) — `--max-windows` is not enforced within an episode

`watch.py:138` checks the budget only between episodes; the window loop at 145 decrements but never breaks. `cluster_events` with `gap_s=30` over the LNR's documented chatty overnight log ("many entries per minute" — its own docstring) can merge hours into one episode; after a long outage a single episode can expand into hundreds of 120 s windows, all pulled in one pass regardless of `--max-windows 50`. That defeats the "bounded catch-up" Done-when item and the docstring's starvation claim. Safe path: check the budget inside the window loop, break mid-episode with `truncated=True` and the watermark held at the last complete episode — re-pulled windows dedup cheaply on `source_key`.

### Minor findings

- `watch.py:397` — `run_watch`'s forever loop has no exception guard around `catch_up`; anything not caught inside (e.g. a transient `CameraStore`/`_advance` sqlite error) kills the unattended daemon. Wrap the pass in try/except-log-continue.
- `watch.py:113` — if a camera's first episode fails to ingest, `CameraReport.watermark_after` stays `None` and `va watch` prints "watermark -> None" even though the durable watermark is unchanged; initialize `watermark_after` to `watermark_before`.

Everything else checked out: COORDINATION.md logs both changes (including the cross-ownership ⚠ on `web/jobs.py`), CLAUDE.md documents the new command and durability semantics, schema versioning migrates v6→v8 correctly, WS6.a's Done-when oracle is genuinely satisfied, and the commit subjects are provisional `need_agent_review:` (exempt from the plain-description rule).

```json
{"verdict": "request_changes", "findings": [
{"severity": "critical", "file": "src/va/pipeline/watch.py", "line": 141, "issue": "An episode straddling the settle horizon is clipped (sidecar: tail lost) or skipped as a zero-length lnr open-instant (whole episode lost), then the watermark advances past its start so the line-133 filter drops it forever on every later pass.", "scenario": "Real lnr-eventlog backend, `va watch --interval 60`: a 5-minute driveway episode is still open at some pass's horizon; the adapter emits a zero-length start-anchored instant, line 142 skips it, the watermark advances to the horizon, and neither that pass nor any future pass ever pulls the footage — silent permanent loss of exactly the long, interesting events."},
{"severity": "major", "file": "src/va/pipeline/watch.py", "line": 138, "issue": "The max_windows budget is checked only between episodes, never inside the per-episode window loop, so one long clustered episode ignores the cap entirely.", "scenario": "After a multi-day outage, the LNR's chatty overnight log clusters (gap_s=30) into one multi-hour episode; a single `va watch` pass pulls hundreds of 120 s windows despite --max-windows 50, violating the plan's bounded-catch-up Done-when and starving other cameras for hours."},
{"severity": "minor", "file": "src/va/pipeline/watch.py", "line": 397, "issue": "run_watch's forever loop does not guard catch_up, so any exception escaping it (e.g. a transient sqlite error in CameraStore/_advance) kills the long-running watcher daemon.", "scenario": "A busy-timeout-exceeded 'database is locked' during _advance while a heavy web ingest holds the writer propagates out of catch_up and the unattended watcher process exits; no footage is pulled until a human notices."},
{"severity": "minor", "file": "src/va/pipeline/watch.py", "line": 113, "issue": "When a camera's first episode fails to ingest, CameraReport.watermark_after remains None even though the durable watermark is unchanged, so the CLI prints 'watermark -> None'.", "scenario": "One flaky pull on the first episode of a pass makes `va watch --interval 0` report 'watermark -> None' for that camera, misreporting the real held watermark to an operator checking catch-up progress."}
]}
```
