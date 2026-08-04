# Agent review — approve

date: 2026-08-03T22:36:36.810429
range: origin/main..HEAD
branch: loop/ws4a-motion-source
findings: 3

- **minor** `src/va/adapters/motion_source/lnr_eventlog_inproc.py:157` — The stuck-cursor guard fingerprints only items[0], so two different consecutive pages that begin with byte-identical duplicated log rows falsely trip the guard and silently drop the remaining events in the window.
  - scenario: A chatty overnight motion log (notes: many entries per minute) contains duplicate rows straddling a 100-row page boundary; page N+1 legitimately starts with the same entry as page N, the guard declares the cursor stuck, and the rest of the day's motion windows are silently lost — WS4.b/c then never pulls that footage. Fix: fingerprint the whole raw page text instead of the first item.
- **minor** `src/va/adapters/motion_source/lnr_eventlog_inproc.py:160` — The 500-page runaway cap has no test coverage (the round-3 coverage finding was only half-addressed: the repeat guard got a test, the page cap did not).
  - scenario: A refactor reorders the cap check after item processing or drops it; against a firmware with pathological pagination the adapter hammers the NVR for thousands of requests, and no test catches the regression. Fix: one test feeding >500 distinct synthetic pages and asserting the loop stops.
- **minor** `tests/test_motion_source.py:182` — `assert src._get("startFind-check") or True` is vacuously true — `or True` makes the assertion pass regardless of the value, so it verifies nothing while reading as a wiring check.
  - scenario: The stub wiring it appears to guard breaks in a refactor; the test stays green because the assertion cannot fail (the repo's 2026-07-28 lesson: a test that cannot fail is decoration). Fix: delete the line or assert the stub's actual return value.

---

## Full review

# Agent review — round 4, `loop/ws4a-motion-source` (828eb70)

**Scope:** `origin/main..HEAD` — one provisional commit: the WS4.a1 MotionSource role (Protocol + `MotionEvent` contract, sidecar stub, LNR608 `log.cgi` adapter, `cluster_events()`, `va motion-probe` CLI, config entries in all four roles dirs, 15 tests, CLAUDE.md/COORDINATION.md updates).

**Prior-round findings are addressed.** This commit is an amend after review round 3 (9d35de6): the `urlencode` `+`-for-space deviation is fixed with `quote_via=urllib.parse.quote` and pinned by `test_vendor_startfind_times_use_percent20_not_plus`, and the repeated-page runaway guard now has a real test asserting no duplicated events and exactly two `doFind` calls.

**What I verified and it held up:**
- `_detail_field` resolves all three call sites ("Channel", "Start Time", "End Time") correctly on both the flat and dotted shapes; the lookahead trick (`[A-Z]` label start vs digit-led time colons) is sound against the fixture strings, and a missing field degrades to `None` → fallbacks, never a crash.
- The startFind token parse correctly distinguishes an HTTP-200 error body from a token (error → raise, not "0 motion"), and `stopFind` runs in a `finally` whose `token` is always bound (assignment precedes the `try`).
- Registry fallback: `Config.role()` raises `KeyError` for a missing role (confirmed in `configuration.py:176`), so older config dirs without `motion_source:` silently get the no-op sidecar — no break for existing `VA_CONFIG_DIR` users; all four repo config dirs got the role anyway.
- Guard ordering: the repeat/page-cap checks run **before** item processing, so a stuck cursor yields one page's events exactly once (matches the test's assertions).
- `cluster_events` is order-insensitive, per-camera, keeps the longer end on containment, and handles the empty list.
- Contract/docs parity: COORDINATION.md carries the full interface entry; `VA_NVR_HOST/USER/PASS/TZ`, the `tz:` spec key, and `motion-probe` are in CLAUDE.md; credentials are env-only in code and stated so in three places. Plan WS4.a1's "Done when" (stub tests + both parser shapes unit-tested) is met, with live validation honestly split into blocked WS4.a2.

**Dissolved suspicions:** overlap-filter boundary logic (inclusive both ends, matches the sidecar and the Protocol docstring); `naive.replace(tzinfo=ZoneInfo)` is correct for zoneinfo (the pytz trap doesn't apply); the footage-profile overlay is merged into `cfg.roles` before the registry reads it.

**Findings (all minor):**

1. `lnr_eventlog_inproc.py:157` — the stuck-cursor guard fingerprints only `items[0]`, so two *different* consecutive pages whose first entries are byte-identical (device-duplicated rows in a log the notes call "many entries per minute overnight" — duplicates landing at a 100-row page boundary) falsely trip the guard and silently drop the rest of the window. Safe path: fingerprint the whole page (the raw `page` text) instead of the first item — same cost, no false positive.
2. `lnr_eventlog_inproc.py:160` — the 500-page cap is the still-untested half of round 3's coverage finding (only the repeat guard got a test). A refactor that moves the cap after item processing regresses unnoticed. Safe path: one test feeding >500 synthetic distinct pages.
3. `tests/test_motion_source.py:182` — `assert src._get("startFind-check") or True` is vacuously true (`or True` makes any value pass) — decoration by the repo's own 2026-07-28 lesson; it reads as verifying stub wiring but asserts nothing. Safe path: delete the line or assert the actual return value.

**Caveat:** test execution was denied in this session (same as round 3), so COORDINATION's "605 passed / 2 skipped" is unverified by me; the `offline-tests` CI gate will check it.

**Verdict: approve** — no critical or major findings.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/adapters/motion_source/lnr_eventlog_inproc.py", "line": 157, "issue": "The stuck-cursor guard fingerprints only items[0], so two different consecutive pages that begin with byte-identical duplicated log rows falsely trip the guard and silently drop the remaining events in the window.", "scenario": "A chatty overnight motion log (notes: many entries per minute) contains duplicate rows straddling a 100-row page boundary; page N+1 legitimately starts with the same entry as page N, the guard declares the cursor stuck, and the rest of the day's motion windows are silently lost — WS4.b/c then never pulls that footage. Fix: fingerprint the whole raw page text instead of the first item."}, {"severity": "minor", "file": "src/va/adapters/motion_source/lnr_eventlog_inproc.py", "line": 160, "issue": "The 500-page runaway cap has no test coverage (the round-3 coverage finding was only half-addressed: the repeat guard got a test, the page cap did not).", "scenario": "A refactor reorders the cap check after item processing or drops it; against a firmware with pathological pagination the adapter hammers the NVR for thousands of requests, and no test catches the regression. Fix: one test feeding >500 distinct synthetic pages and asserting the loop stops."}, {"severity": "minor", "file": "tests/test_motion_source.py", "line": 182, "issue": "`assert src._get(\"startFind-check\") or True` is vacuously true — `or True` makes the assertion pass regardless of the value, so it verifies nothing while reading as a wiring check.", "scenario": "The stub wiring it appears to guard breaks in a refactor; the test stays green because the assertion cannot fail (the repo's 2026-07-28 lesson: a test that cannot fail is decoration). Fix: delete the line or assert the stub's actual return value."}]}
```
