# Agent review — request_changes

date: 2026-08-06T23:13:37.747623
range: origin/main..HEAD
branch: loop/ws6b-watermark-backfill
findings: 4

- **major** `src/va/pipeline/watch.py:188` — Settled episodes occurring after a deferred (open/zero-length) episode advance the watermark past deferred_start; the later capped write is silently rejected by the monotonic guard, so the deferred episode's events fail the start_epoch >= watermark filter next pass and it is never ingested.
  - scenario: lnr emits a displaced Start,Start 'open' instant at T+30 (End marker lost to a log wrap/NVR reboot) plus a settled episode [T+65,T+90]; the pass ingests [65,90], writes watermark=T+90, and the T+30 episode is silently lost forever — the round-1-critical failure class resurfacing in the multi-episode ordering. Fix: cap per-episode _advance at deferred_start; add the mid-list open-instant + settled-episode case to tests/test_watch.py.
- **major** `src/va/pipeline/watch.py:149` — A permanently-open displaced instant (lost End marker) re-emits every pass and pins deferred_start at its start indefinitely, so once finding 1 is fixed the camera's watermark wedges — no new footage ingested — until the log entry ages out of the NVR ring, silently.
  - scenario: After an NVR reboot loses an End marker, the Start,Start displaced instant recurs in every [watermark, horizon] query; the watcher defers it forever, stalling that camera for days, and footage older than the ~6-day ring becomes unrecoverable. Fix: age out open instants (defer only near the horizon; older ones pulled as a padded window or terminal-skipped with a loud warning) — flag the age bound for human sign-off.
- **minor** `src/va/pipeline/watch.py:103` — A --camera id that matches no registered camera (typo) or a camera lacking source_ref is silently dropped when other cameras remain — the aggregate 'no cameras' warning only fires when the filtered list is empty.
  - scenario: va watch --camera nvr-ch9 (typo for nvr-ch1) exits 0 printing only other cameras' lines; the operator reads the target camera as quiet when it was never watched. Fix: warn per requested-but-unmatched camera id.
- **minor** `src/va/web/jobs.py:350` — AskQueue._resume's except path skips rebuilding a malformed ask row in memory, so a polling browser gets a 404 — contradicting the pollability guarantee IngestQueue._fail_in_memory claims to mirror.
  - scenario: A hand-edited/future-shape ask payload row: fail_pending marks it failed in the table, but the watching browser polls its ask_id and receives 404 instead of state=failed. Fix: rebuild with a placeholder question, as the ingest side does with uri='<unknown>'.

---

## Full review

Review complete. Two commits (WS6.a durable job queue, WS6.b catch-up watcher) with prior review rounds already folded in — the WS6.a queue code is in good shape, but the WS6.b watcher has a real data-loss hole in its deferred-episode handling that the existing tests don't reach.

## Findings

**1. Major — `src/va/pipeline/watch.py:188` — a settled episode occurring after a deferred episode advances the watermark past `deferred_start`, permanently losing the deferred episode.**

The classification pass computes `deferred_start` as the earliest unsettled episode's start, but the settled-episode loop then ingests *every* settled episode and calls `_advance(workdir, cam.id, ep.end_epoch)` unconditionally — including for episodes that start *after* `deferred_start`. The quiet-remainder write of `min(horizon, deferred_start)` comes afterwards, and `CameraStore.set_watermark` is monotonic, so it's a silent no-op. Next pass, the deferred episode's events fail the `e.start_epoch >= watermark` filter and are dropped forever.

Concrete failing input, on the real backend: the lnr adapter emits a mid-range `open` instant when an End marker is lost to a log wrap/NVR reboot (`lnr_eventlog_inproc.py:207-217` — the Start,Start displaced case). Events: displaced open instant at T+30, then a closed episode [T+65, T+90] (gap 35 s > gap_s 30). The pass ingests [65,90] and writes watermark=T+90; the capped target T+30 is rejected by the monotonic guard; the T+30 episode is never pulled — exactly the round-1-critical failure class the deferral was built to prevent, resurfacing in the multi-episode ordering. Note this is expressible in the sidecar stub: `test_open_lnr_instant_defers_not_skips` covers an open instant *alone*, not one followed by a settled episode.

Safe path: when `deferred_start` is set, cap every per-episode `_advance` at `deferred_start` (still ingesting later settled episodes is fine — source_key dedup makes the re-cluster next pass free), and add the mid-list-open-instant-plus-settled-episode case to `tests/test_watch.py`.

**2. Major — `src/va/pipeline/watch.py:145-155` — a permanently-open displaced instant wedges its camera's watermark forever, silently.**

The lnr Start,Start displaced instant (End marker lost — this end never arrives) re-emits on every pass as long as its Start entry remains in the queried log range, always with `start_epoch >= watermark`, so `deferred_start` pins the watermark at its start indefinitely. Today finding 1 masks this by losing the episode and moving on; once finding 1 is fixed, the camera instead stops advancing (and stops ingesting anything newer) until the log entry ages out of the NVR's ring — potentially days of silent stall, and with the ~6-day footage ring, stalled-past-the-ring footage becomes unrecoverable. The clustering variant makes it worse: `cluster_events` keeps the first event's attributes, so an open instant that clusters as the first member taints the whole merged episode `open`, deferring genuinely-settled footage with it.

Safe path: age out open instants — defer only while the instant's start is within some bound of the horizon (say, settle + max expected episode length); older than that, pull it as a padded window (the adapter docstring already anticipates "the consumer may pad") or terminal-skip it with a loud per-camera warning. The bound is a structure/budget knob, but flag its value for human sign-off per the hardcoded-values convention.

**3. Minor — `src/va/pipeline/watch.py:100-108` — a `--camera` id that matches nothing (typo, or a camera row lacking `source_ref`) is silently dropped from the pass.**

The "no cameras" warning fires only when the filtered list is empty overall; with other cameras registered, `va watch --camera nvr-ch9` (typo) or a source_ref-less camera prints per-camera lines for the others and exits 0 — the operator reads "quiet camera" where the truth is "not watched at all". Safe path: warn per requested-but-unmatched id.

**4. Minor — `src/va/web/jobs.py:344-352` — malformed ask rows are 404s to a polling browser, contradicting the stated mirror of `_fail_in_memory`.**

`IngestQueue._fail_in_memory`'s docstring says it "mirrors AskQueue._resume", and its purpose (round-6) is that a watched job_id must poll as failed, never 404. But `AskQueue._resume`'s except path only logs "skipped" — the DB row is failed by `fail_pending`, yet the in-memory record is never rebuilt, so the browser watching that ask gets a 404. Safe path: rebuild with a placeholder question, as the ingest side does with `uri="<unknown>"`.

Not reported after verification: the `+00:00` ISO URIs parse fine (`datetime.fromisoformat`, and windows of exactly 120 s pass the strict `>` cap check); the intermediate-window truncation math can't exceed the cap; `queued` rows correctly accrue no attempts; `requeue_if_running`'s guard closes the stop/terminal-write race; migrations v7/v8 follow the established idempotent pattern with the version-count assert intact; documentation parity is good (CLAUDE.md, COORDINATION.md — including the explicit web-ownership warning for `jobs.py`); unbounded jobs-table history is disclosed in COORDINATION.md as deliberate future work. I did not run the suites — another session's pytest is live (repo lesson: never pile on a concurrent run) and the post-commit hook already reviewed both commits with green suites.

```json
{"verdict": "request_changes", "findings": [
{"severity": "major", "file": "src/va/pipeline/watch.py", "line": 188, "issue": "Settled episodes occurring after a deferred (open/zero-length) episode advance the watermark past deferred_start; the later capped write is silently rejected by the monotonic guard, so the deferred episode's events fail the start_epoch >= watermark filter next pass and it is never ingested.", "scenario": "lnr emits a displaced Start,Start 'open' instant at T+30 (End marker lost to a log wrap/NVR reboot) plus a settled episode [T+65,T+90]; the pass ingests [65,90], writes watermark=T+90, and the T+30 episode is silently lost forever — the round-1-critical failure class resurfacing in the multi-episode ordering. Fix: cap per-episode _advance at deferred_start; add the mid-list open-instant + settled-episode case to tests/test_watch.py."},
{"severity": "major", "file": "src/va/pipeline/watch.py", "line": 149, "issue": "A permanently-open displaced instant (lost End marker) re-emits every pass and pins deferred_start at its start indefinitely, so once finding 1 is fixed the camera's watermark wedges — no new footage ingested — until the log entry ages out of the NVR ring, silently.", "scenario": "After an NVR reboot loses an End marker, the Start,Start displaced instant recurs in every [watermark, horizon] query; the watcher defers it forever, stalling that camera for days, and footage older than the ~6-day ring becomes unrecoverable. Fix: age out open instants (defer only near the horizon; older ones pulled as a padded window or terminal-skipped with a loud warning) — flag the age bound for human sign-off."},
{"severity": "minor", "file": "src/va/pipeline/watch.py", "line": 103, "issue": "A --camera id that matches no registered camera (typo) or a camera lacking source_ref is silently dropped when other cameras remain — the aggregate 'no cameras' warning only fires when the filtered list is empty.", "scenario": "va watch --camera nvr-ch9 (typo for nvr-ch1) exits 0 printing only other cameras' lines; the operator reads the target camera as quiet when it was never watched. Fix: warn per requested-but-unmatched camera id."},
{"severity": "minor", "file": "src/va/web/jobs.py", "line": 350, "issue": "AskQueue._resume's except path skips rebuilding a malformed ask row in memory, so a polling browser gets a 404 — contradicting the pollability guarantee IngestQueue._fail_in_memory claims to mirror.", "scenario": "A hand-edited/future-shape ask payload row: fail_pending marks it failed in the table, but the watching browser polls its ask_id and receives 404 instead of state=failed. Fix: rebuild with a placeholder question, as the ingest side does with uri='<unknown>'."}
]}
```
