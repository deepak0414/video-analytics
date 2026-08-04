# Agent review — approve

date: 2026-08-03T22:43:07.438484
range: origin/main..HEAD
branch: loop/ws4a-motion-source
findings: 0


---

## Full review

Basic auth is confirmed as the proven-working scheme for this device's CGI (notes §2/§5: "HTTP CGI = Basic auth, not Digest") — that suspicion dissolves. My review is complete.

# Agent review — round 5, `loop/ws4a-motion-source` (aff5b0c)

**Scope:** `origin/main..HEAD` — one provisional commit: the WS4.a1 MotionSource role (`MotionSource` Protocol + `MotionEvent` contract, sidecar stub adapter, LNR608 `log.cgi` event-log adapter, `cluster_events()`, `va motion-probe` CLI, `motion_source:` entries in all four config dirs, 17 tests, CLAUDE.md/COORDINATION.md updates). This is an amend following review round 4 (828eb70, approved with 3 minor findings).

**All three round-4 findings are addressed in this amend — verified in the code, not just claimed:**

1. **Stuck-cursor fingerprint** now compares the *whole raw page* (`page == prev_page`, `lnr_eventlog_inproc.py:159`), not `items[0]`, with a comment explaining exactly the boundary-duplicate false positive the round-4 finding described. Ordering is correct: the repeat check runs before `prev_page = page` and before item processing, so a stuck cursor yields one page's events exactly once — matching `test_vendor_runaway_guards_stop_on_repeated_page`'s assertions (2 doFind calls, no duplicated events).
2. **The 500-page cap has a test** (`test_vendor_page_cap_stops_pathological_pagination`): 520 pages that I verified are pairwise distinct (minute = i%60, second = i//60 — unique pairs for i < 3600, so the repeat guard cannot fire early), asserting exactly 501 fetches and 500 processed pages. I re-derived the loop arithmetic: iteration 501 increments `pages` to 501, passes the distinct-page check, hits `pages > 500`, and breaks before processing — the assertions match the code precisely.
3. **The vacuous `assert ... or True`** is gone; the stopFind wiring check is now a real assertion (`assert any("stopFind" in c for c in calls)` in `test_vendor_events_filters_types_and_maps_epochs`).

**Fresh scrutiny this round (new eyes on everything, per the plan's regurgitation lesson):**

- **Auth scheme:** the adapter sends preemptive HTTP Basic. I suspected Dahua's usual Digest requirement, but `nvr-access-notes.md` (lines 42, 149, 182) explicitly proves this unit's CGI is Basic-not-Digest, and the log.cgi flow was exercised with `--anyauth` resolving to Basic. Dissolved.
- **Token parse vs. the proven flow:** the notes extracted the startFind token with `grep -oE '[0-9]+' | head -1`; the adapter prefers explicit `token=`, raises on an error body (so an HTTP-200 `Error\ncode=287` can't read as "0 motion"), and falls back to a bare-number/`result=N` full-match. A mismatch on live firmware fails **loud** (RuntimeError), which is the right failure mode for the human-gated WS4.a2 probe. Dissolved.
- **Detail fallback matches the notes:** the proven doFind output shows `items[i].Time + Type=Motion Detect` without a guaranteed Detail block; the adapter correctly degrades (`Start Time` → `Time`, missing end → start, unparseable start → skip-with-warning, never abort).
- `_detail_field`'s three call sites resolve correctly on both fixture shapes; the lookahead (`\s+[A-Z]` label start vs. digit-led time colons) cannot terminate the capture inside a timestamp.
- Timezone handling: `naive.replace(tzinfo=ZoneInfo)` is correct for zoneinfo (no pytz trap); the tz=None path uses per-date system-local rules, avoiding the frozen-offset DST bug the constructor comment warns about.
- Registry: older/custom config dirs without `motion_source:` hit the `KeyError` fallback → sidecar no-op; spec-level `host`/`tz` override profile `load` params; both branches are registry-tested. `enabled:` gating is irrelevant until WS4.b consumes the role at ingest.
- Overlap filters are boundary-inclusive and identical in both adapters, matching the Protocol docstring and pinned by tests.
- Plan conformance: WS4.a1's "Done when" (stub tests return synthetic windows; vendor parser unit-tested on both plausible shapes) is met; live validation is honestly split into blocked WS4.a2 with the exact human command recorded. Docs parity holds: `VA_NVR_HOST/USER/PASS/TZ`, `tz:`, `host:`, `motion-probe` in CLAUDE.md; `events_file` and the full interface entry in COORDINATION.md. Credentials are env-only in code and stated so in three places.
- Commit message is a provisional `need_agent_review:` subject — exempt from the clarity rule until finalize.

**Caveat:** test execution was denied in this session (as in rounds 3–4), so COORDINATION's "606 passed / 2 skipped" is unverified by me — though the count is arithmetically consistent (589 on main + 17 new tests), and the `offline-tests` CI gate will verify it.

**Verdict: approve** — no findings survived verification this round.

```json
{"verdict": "approve", "findings": []}
```
