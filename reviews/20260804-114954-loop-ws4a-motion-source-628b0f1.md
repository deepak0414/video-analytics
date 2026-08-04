# Agent review — approve

date: 2026-08-04T11:55:13.851506
range: origin/main..HEAD
branch: loop/ws4a-motion-source
findings: 1

- **minor** `src/va/adapters/motion_source/lnr_eventlog_inproc.py:392` — Flat-shape entries with a present-but-unparseable End Time silently collapse to a zero-length window (end=start, no warning), unlike the marker-pairing path which logs unparseable End Times.
  - scenario: A flat-Detail firmware emits a corrupt 'End Time: garbled'; the episode is recorded as an instant, WS4.b pulls only a sliver of the footage, and no log line explains the truncation. Fix: warn when end_s is non-empty but _epoch(end_s) is None before falling back to start.

---

## Full review

Verification blocked on writing scratch files — I'll finish the remaining edge-case checks by direct code reading, which I've now done. Here is my review.

## Review: `origin/main..HEAD` (WS4.a1/a2 — MotionSource role)

**What I checked:** the full diff (909 added lines, no deletions); `_parse_items`/`_detail_field` regex behavior traced by hand against both fixture shapes and the live multi-line fixture; timezone handling (`ZoneInfo.replace` is DST-correct per date, unlike pytz — the code and comments are right); pagination/runaway-guard ordering (repeat check fires before item processing, so no duplicates); the start/end marker pairing including the displaced-start, unparseable-end, and range-end-flush paths; registry plumbing against `configuration.py` (`Config.roles` carries the footage overlay, so the `spec` read is overlay-aware; the KeyError fallback covers older config dirs); packaging (auto-discovery under `src`, new subpackage included); plan conformance against `architecture-evolution-loop.md` WS4.a1's done-when (stub tests + both parser shapes — both satisfied, plus the live third shape); COORDINATION.md contract entry (matches the implemented signature); documentation parity (all four env vars, `host:`/`tz:`/`events_file` spec keys, and the CLI documented in CLAUDE.md/COORDINATION/docstrings); and the full offline suite, which I ran: **610 passed / 2 skipped** — matching the claimed evidence. The ground-truth rule is satisfied (25 probe windows vs 22 golden clips recorded in the plan with the delta explained).

Several suspicions dissolved on inspection: the repeated-page guard cannot double-emit (break precedes processing); an End-only flat entry cannot mismatch `Start Time` (the regex requires the literal label); hardcoded `pages > 500` and `gap_s=30.0` are structure/budgets, not content, and are flagged in comments; the sidecar's missing-file-is-quiet behavior is deliberate and documented.

**One finding survived:**

- **minor** — `src/va/adapters/motion_source/lnr_eventlog_inproc.py:392` — In `_emit`, a flat-shape entry whose `End Time` is present but unparseable silently collapses the window to a zero-length instant (`end = start` with no log), while the equivalent marker-pairing path (line 352) logs a warning. Scenario: a flat-Detail firmware emits a corrupt `End Time`; the episode is stored as an instant, WS4.b later pulls a sliver of footage instead of the episode, and there is no log line to explain the truncation. Safe path: mirror the marker path — `logger.warning` when `end_s` is non-empty but `_epoch` returns None, keeping the current start-anchored emit.

This is a warning-asymmetry with graceful degradation, not a data-loss path, so it does not block approval.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/adapters/motion_source/lnr_eventlog_inproc.py", "line": 392, "issue": "Flat-shape entries with a present-but-unparseable End Time silently collapse to a zero-length window (end=start, no warning), unlike the marker-pairing path which logs unparseable End Times.", "scenario": "A flat-Detail firmware emits a corrupt 'End Time: garbled'; the episode is recorded as an instant, WS4.b pulls only a sliver of the footage, and no log line explains the truncation. Fix: warn when end_s is non-empty but _epoch(end_s) is None before falling back to start."}]}
```
