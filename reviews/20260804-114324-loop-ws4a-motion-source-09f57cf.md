# Agent review — request_changes

date: 2026-08-04T11:47:34.461269
range: origin/main..HEAD
branch: loop/ws4a-motion-source
findings: 3

- **major** `src/va/adapters/motion_source/lnr_eventlog_inproc.py:215` — An HTTP-200 error body from doFind is parsed as an empty page and silently ends pagination, while the identical error body on startFind raises — the adapter's own stated principle (line 149) applied inconsistently.
  - scenario: The NVR expires or auto-closes the find token mid-pagination (Dahua code=287) on a multi-page window; events() returns only the pages fetched before the error with no warning, so the motion log is silently under-reported and WS4.b would never pull that footage. Fix: check the page for an error body before the empty-items break and raise.
- **minor** `src/va/adapters/motion_source/lnr_eventlog_inproc.py:201` — A second Start marker on the same channel overwrites the open Start without emitting it, silently losing the first episode — asymmetric with the unmatched-End and range-end-flush paths, which both emit instants.
  - scenario: This firmware logs episodes as separate Start/End entries; if an End marker is lost (log wrap, NVR reboot mid-motion), the resulting Start,Start sequence drops the first episode with no window and no log line. Fix: emit the displaced open Start as a start-anchored instant with open:true, as the range-end flush does.
- **minor** `src/va/adapters/motion_source/lnr_eventlog_inproc.py:203` — open_by_chan.pop() runs before the End-Time parse check, so an unparseable End Time discards the paired open Start along with the End entry.
  - scenario: A malformed End marker (truncated page line, firmware quirk) hits the continue at line 207 after the pop; the episode's Start is no longer in open_by_chan, so it neither emits nor reaches the range-end flush — the whole episode vanishes with only the End-marker warning. Fix: parse end_s first and pop only on success.

---

## Full review

I've now verified everything I need. The parsing and pairing logic added by the WS4.a2 amend is largely sound — I traced the multi-line continuation parser, the `_detail_field` regex against both fixture shapes (the lazy capture + `[A-Z]` lookahead cannot terminate inside a timestamp), the pagination guards' ordering (repeat check before processing, cap arithmetic matches the test's 501/500 assertions), the timezone paths (no frozen-offset DST bug, no pytz trap), the registry KeyError fallback for configs lacking `motion_source:`, and the combination story (role present in all four config dirs, both registry branches tested). Plan conformance (WS4.a1 done-when + live-validated WS4.a2 with ground-truth comparison), documentation parity, and COORDINATION logging all check out. Suspicions that dissolved on inspection: Basic-vs-Digest auth (proven Basic in the notes), the `token=` parse (fails loud), the sidecar's overlap filter (boundary-inclusive, matches the Protocol).

Three findings survived, all in the vendor adapter's error paths:

**1. major — `src/va/adapters/motion_source/lnr_eventlog_inproc.py:215` — an HTTP-200 error body from `doFind` silently truncates results, while the identical failure on `startFind` raises.** The code's own comment (line 149) states the principle: an `"Error\ncode=287"` body "must raise, not … read as 0 motion." That guard is applied only to `startFind`. If a `doFind` page returns an error body — e.g. the find token expires or the NVR auto-closes the find mid-pagination (code 287 is exactly Dahua's "find record not found") — `_parse_items` yields `[]`, the loop hits the empty-page break at line 215, and the call returns the pages fetched so far as if they were the complete window. No warning, no exception. Once WS4.b gates ingest on these windows, that's footage silently never pulled. Safe path: before treating an empty-items page as the terminator, apply the same `re.search(r"\berror\b", page, re.IGNORECASE)` check and raise (the `finally` already handles `stopFind`); `test_vendor_error_body_raises_not_zero_motion` extends naturally with a doFind-error variant.

**2. minor — `lnr_eventlog_inproc.py:201` — a second Start marker on the same channel silently overwrites the first, losing that episode entirely.** The pairing state handles an unmatched End (emitted as an instant, line 209) and an unmatched trailing Start (flushed as a start-anchored instant), but two consecutive Starts — which is exactly what a lost/rotated-away End marker produces on this two-entry-per-episode firmware — drop the first episode with no window and no log line. Safe path: on overwrite, emit the displaced open Start the same way the range-end flush does (start-anchored instant with `"open": True`).

**3. minor — `lnr_eventlog_inproc.py:203-207` — an unparseable End Time discards the already-popped open Start.** `open_by_chan.pop(chan, None)` runs before the `self._epoch(end_s) is None` check; on the `continue` path the paired Start marker is gone, so the episode neither emits nor survives to the range-end flush. Safe path: parse first and pop only on success (or re-insert / emit the start-anchored instant on failure), keeping the skip-with-warning behavior for the End marker itself.

Caveat: test execution was denied in this session (as in prior rounds), so the "607 passed / 2 skipped" count is unverified by me; it is arithmetically consistent (589 on main + 18 new tests) and the `offline-tests` CI gate will verify it. The commit subject is a provisional `need_agent_review:` — exempt from the clarity rule until finalize.

**Verdict: request_changes** — one major finding (silent doFind truncation).

```json
{"verdict": "request_changes", "findings": [
  {"severity": "major", "file": "src/va/adapters/motion_source/lnr_eventlog_inproc.py", "line": 215,
   "issue": "An HTTP-200 error body from doFind is parsed as an empty page and silently ends pagination, while the identical error body on startFind raises — the adapter's own stated principle (line 149) applied inconsistently.",
   "scenario": "The NVR expires or auto-closes the find token mid-pagination (Dahua code=287) on a multi-page window; events() returns only the pages fetched before the error with no warning, so the motion log is silently under-reported and WS4.b would never pull that footage. Fix: check the page for an error body before the empty-items break and raise."},
  {"severity": "minor", "file": "src/va/adapters/motion_source/lnr_eventlog_inproc.py", "line": 201,
   "issue": "A second Start marker on the same channel overwrites the open Start without emitting it, silently losing the first episode — asymmetric with the unmatched-End and range-end-flush paths, which both emit instants.",
   "scenario": "This firmware logs episodes as separate Start/End entries; if an End marker is lost (log wrap, NVR reboot mid-motion), the resulting Start,Start sequence drops the first episode with no window and no log line. Fix: emit the displaced open Start as a start-anchored instant with open:true, as the range-end flush does."},
  {"severity": "minor", "file": "src/va/adapters/motion_source/lnr_eventlog_inproc.py", "line": 203,
   "issue": "open_by_chan.pop() runs before the End-Time parse check, so an unparseable End Time discards the paired open Start along with the End entry.",
   "scenario": "A malformed End marker (truncated page line, firmware quirk) hits the continue at line 207 after the pop; the episode's Start is no longer in open_by_chan, so it neither emits nor reaches the range-end flush — the whole episode vanishes with only the End-marker warning. Fix: parse end_s first and pop only on success."}
]}
```
