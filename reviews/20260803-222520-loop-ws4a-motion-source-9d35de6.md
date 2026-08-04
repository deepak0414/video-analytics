# Agent review — approve

date: 2026-08-03T22:29:56.108213
range: origin/main..HEAD
branch: loop/ws4a-motion-source
findings: 2

- **minor** `src/va/adapters/motion_source/lnr_eventlog_inproc.py:127` — urlencode emits '+' for spaces in condition.StartTime/EndTime, but the proven-working access in nvr-access-notes.md used %20 and old embedded CGI servers often don't decode '+' as space.
  - scenario: The human-gated WS4.a2 live probe runs against the real LNR608 and startFind rejects or mis-parses the time condition, wasting the credential-gated validation run on an encoding artifact; fix by passing quote_via=urllib.parse.quote.
- **minor** `tests/test_motion_source.py:744` — The pagination runaway guards (repeated-page detection and the 500-page cap) in LnrEventLogMotionSource.events have no test coverage.
  - scenario: A future refactor reorders the guard checks after item processing; a firmware whose doFind cursor doesn't advance then duplicates events or hammers the NVR, and no test catches the regression (manual probe today confirms the current code stops after one duplicate page).

---

## Full review

The Basic-auth suspicion dissolves — the notes explicitly record "HTTP CGI = Basic auth, not Digest" for this firmware, so the adapter matches the proven mechanism. One related detail did surface while comparing against the proven curl commands: the notes' working commands encode spaces in the time condition as `%20`, while the adapter's `urllib.parse.urlencode` defaults to `quote_plus`, emitting `+` for the space in `2026-07-21 00:00:00`. Old embedded CGI servers frequently do not decode `+` as a space in query strings — a deviation from the only encoding proven to work on this device.

## Review summary

**Scope reviewed:** `origin/main..HEAD` — one commit (`9d35de6`, provisional `need_agent_review:` subject): the WS4.a1 MotionSource role — Protocol + `MotionEvent` contract, sidecar stub adapter, LNR608 `log.cgi` event-log adapter, `cluster_events()`, `va motion-probe` CLI, config entries in all four roles dirs, tests, and COORDINATION/CLAUDE.md updates.

**What I verified (and it held up):**
- `_detail_field`/`_parse_items` behave correctly on both flat and dotted shapes, including the tricky `End Time` query against a start-only dotted item (returns `None`, adapter then falls back to `end = start`) — probed directly, no mis-matches.
- The repeat-page runaway guard stops after processing the duplicated page once (1 event, not 2 — probed), `cluster_events` handles empty/single input, and the error-body-vs-token distinction raises instead of reading "0 motion."
- The registry reads `cfg.roles` for the spec, which I confirmed *does* include the footage-profile overlay (merged at load time in `configuration.py`), so there's no overlay-bypass bug there.
- Plan conformance: WS4.a1's "Done when" (stub tests return synthetic windows; vendor parser unit-tested on both plausible shapes) is met; live validation is honestly split out as blocked WS4.a2 and logged in COORDINATION with the ⚠ unverified-parser caveat — exactly the determinism-≠-correctness posture the repo requires.
- Documentation parity: `VA_NVR_HOST/USER/PASS/TZ`, the `motion-probe` command, and the `host:`/`tz:` spec keys are all in CLAUDE.md/config comments; credentials-never-in-config is stated in three places. COORDINATION.md carries the full interface log entry.
- Combination coverage: `motion_source` added to all four roles.yaml dirs; the role isn't ingest-wired yet so footage-profile gating doesn't apply (and `GATEABLE_ROLES` correctly excludes it).

**Findings (both minor):**
1. `lnr_eventlog_inproc.py:127` — `urlencode` sends `+` for spaces in `condition.StartTime`/`EndTime` where every proven-working command in `nvr-access-notes.md` used `%20`; if the 2015 firmware doesn't decode `+`, the live probe fails or the device mis-filters the range. Safe path: pass `quote_via=urllib.parse.quote` to match the proven encoding before the human burns their one credential-gated WS4.a2 run on an encoding artifact.
2. `tests/test_motion_source.py` — the two runaway-pagination guards (repeated page, 500-page cap) have no test; I probed the repeat guard manually and it works, but a regression test would pin behavior that exists precisely because the firmware's cursor semantics are unverified.

**Caveat:** I could not run the test suite (command approval denied in this session), so COORDINATION's "603 passed / 2 skipped" claim is unverified by me; the CI `offline-tests` gate will check it.

**Verdict: approve** — no critical or major findings.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/adapters/motion_source/lnr_eventlog_inproc.py", "line": 127, "issue": "urlencode emits '+' for spaces in condition.StartTime/EndTime, but the proven-working access in nvr-access-notes.md used %20 and old embedded CGI servers often don't decode '+' as space.", "scenario": "The human-gated WS4.a2 live probe runs against the real LNR608 and startFind rejects or mis-parses the time condition, wasting the credential-gated validation run on an encoding artifact; fix by passing quote_via=urllib.parse.quote."}, {"severity": "minor", "file": "tests/test_motion_source.py", "line": 744, "issue": "The pagination runaway guards (repeated-page detection and the 500-page cap) in LnrEventLogMotionSource.events have no test coverage.", "scenario": "A future refactor reorders the guard checks after item processing; a firmware whose doFind cursor doesn't advance then duplicates events or hammers the NVR, and no test catches the regression (manual probe today confirms the current code stops after one duplicate page)."}]}
```
