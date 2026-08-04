# Agent review — approve

date: 2026-08-03T22:14:53.544034
range: origin/main..HEAD
branch: loop/ws4a-motion-source
findings: 5

- **minor** `src/va/adapters/motion_source/lnr_eventlog_inproc.py:84` — Default tz fallback captures a fixed UTC offset at construction time, not DST-aware local rules
  - scenario: With no tz/VA_NVR_TZ configured on a DST-observing host, querying a date in the opposite DST phase (verified: Jan 15 on this box) yields epochs off by 3600s, shifting every motion window and any future footage pull by an hour
- **minor** `src/va/adapters/motion_source/lnr_eventlog_inproc.py:156` — Pagination breaks on len(items) < 100, assuming the device always fills the requested count per page
  - scenario: If this firmware caps doFind responses below 100 items, the loop stops after one page and silently drops the rest of the day's motion entries; the tests never exercise a second loop iteration
- **minor** `src/va/adapters/motion_source/lnr_eventlog_inproc.py:126` — startFind token is extracted as the first digit-run anywhere in the response body
  - scenario: An HTTP-200 error body containing digits (e.g. 'Error\ncode=287') is mistaken for a token; doFind then parses nothing and events() returns [] — an error silently reported as zero motion
- **minor** `src/va/registry.py:309` — The lnr-eventlog registry branch and its host/tz spec-merge have no test coverage
  - scenario: A typo in the ('host','tz') filter or param plumbing ships green because tests instantiate the adapter directly, surfacing only during the credential-gated live probe (WS4.a2)
- **minor** `src/va/adapters/motion_source/lnr_eventlog_inproc.py:83` — VA_NVR_TZ env var and the tz role-spec key are documented only in the adapter docstring
  - scenario: A session debugging hour-shifted probe output finds no tz knob in CLAUDE.md or the COORDINATION entry, which name only VA_NVR_HOST/USER/PASS

---

## Full review

Confirmed: the fallback tzinfo is a fixed offset (`PDT`), so a winter-date query is off by exactly 3600 s. I have everything I need — the review is complete.

## Review: `origin/main..HEAD` (d1b4e84, MotionSource role — WS4.a1)

**Verdict: approve.** The change is well-scoped and honest about its limits: the Protocol/contract/adapter/registry pattern mirrors every other role, the sidecar stub keeps tests offline, COORDINATION.md is logged with the ⚠ live-validation caveat, all four config dirs got the role, credentials are env-only as the notes require, and the WS4.a1 done-conditions (stub tests + both parser shapes unit-tested) are met. I ran the full suite: **599 passed / 2 skipped**, matching the COORDINATION claim. Auth scheme (Basic) and the log.cgi flow match the proven `nvr-access-notes.md` §5b session. All findings below are minor; most sit on the vendor adapter's untested-against-device edges, which WS4.a2 already exists to shake out — but each has a cheap hardening fix worth applying before that live run.

**Findings:**

1. **minor** — `src/va/adapters/motion_source/lnr_eventlog_inproc.py:84` — When no `tz`/`VA_NVR_TZ` is configured, the fallback `datetime.now().astimezone().tzinfo` is a **fixed offset frozen at process start** (verified: `PDT` now), so log timestamps from the opposite DST phase convert to epochs one hour off. Scenario: in January, `va motion-probe` on this host (default config) reports every motion window 3600 s late, and the future WS4.c pull would fetch the wrong hour of footage. Safe path: when no zone is configured, convert per-datetime with DST-aware local rules (`datetime.strptime(...).astimezone()` / `datetime.fromtimestamp(epoch)` with no tzinfo) instead of a captured fixed offset.

2. **minor** — `src/va/adapters/motion_source/lnr_eventlog_inproc.py:156` — Pagination breaks on `len(items) < 100`, which silently truncates results if this firmware returns fewer than the requested `count` per page (per-page caps vary across Dahua firmwares; the notes session never paged past one response). Scenario: device caps doFind at 20 items/page → a busy day's log yields only the first 20 entries, motion windows silently missing. Safe path: loop until an empty page (or parse the response's `found=` count), and add a multi-page unit test — the current tests never exercise a second iteration of this loop.

3. **minor** — `src/va/adapters/motion_source/lnr_eventlog_inproc.py:126` — The startFind token is the **first digit-run anywhere** in the response (`re.search(r"\d+", ...)`); an HTTP-200 error body containing digits (e.g. `Error\ncode=287`) becomes a bogus token, doFind then parses nothing, and `events()` returns `[]` — an error silently reported as "no motion". Safe path: anchor on `token=(\d+)` and raise the existing RuntimeError otherwise (the notes' `grep '[0-9]+' | head -1` was a shell one-liner, not a contract).

4. **minor** — `src/va/registry.py:309` — The `lnr-eventlog` registry branch (including the `host`/`tz` spec-key merge into `params`) has zero test coverage; tests construct `LnrEventLogMotionSource` directly, so a typo in the `("host", "tz")` filter or the param plumbing would ship green and only surface during the human-gated live probe. Safe path: one registry test with monkeypatched `VA_NVR_USER/PASS` and a config setting `model: lnr-eventlog` + `host:`, asserting adapter type and `src.host`.

5. **minor** — `src/va/adapters/motion_source/lnr_eventlog_inproc.py:83` — Documentation parity: the `VA_NVR_TZ` env var and the `tz` role-spec key exist only in the adapter's module docstring; CLAUDE.md's new motion-probe line and the COORDINATION entry name only HOST/USER/PASS. Scenario: the next session debugging an hour-shifted probe (finding 1) has no discoverable knob. Safe path: add `tz`/`VA_NVR_TZ` to the CLAUDE.md motion-probe comment or the COORDINATION WS4.a1 entry.

Suspicions checked and dropped: the flat-Detail regex correctly stops values before the next capitalized label and handles times' internal colons (traced against the notes' confirmed `Channel No.: 2 Start Time: … End Time: …` shape); `cfg.role()` raises `KeyError` so the registry's missing-role fallback to sidecar is sound for older config dirs; `--cluster-gap 0` falsy-skip matches its documented "0 = raw" semantics; the display-vs-API channel-index trap from notes §5c is explicitly deferred to WS4.c in both the docstring and COORDINATION; the provisional `need_agent_review:` subject is exempt from the commit-clarity rule.

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "src/va/adapters/motion_source/lnr_eventlog_inproc.py", "line": 84, "issue": "Default tz fallback captures a fixed UTC offset at construction time, not DST-aware local rules", "scenario": "With no tz/VA_NVR_TZ configured on a DST-observing host, querying a date in the opposite DST phase (verified: Jan 15 on this box) yields epochs off by 3600s, shifting every motion window and any future footage pull by an hour"},
{"severity": "minor", "file": "src/va/adapters/motion_source/lnr_eventlog_inproc.py", "line": 156, "issue": "Pagination breaks on len(items) < 100, assuming the device always fills the requested count per page", "scenario": "If this firmware caps doFind responses below 100 items, the loop stops after one page and silently drops the rest of the day's motion entries; the tests never exercise a second loop iteration"},
{"severity": "minor", "file": "src/va/adapters/motion_source/lnr_eventlog_inproc.py", "line": 126, "issue": "startFind token is extracted as the first digit-run anywhere in the response body", "scenario": "An HTTP-200 error body containing digits (e.g. 'Error\\ncode=287') is mistaken for a token; doFind then parses nothing and events() returns [] — an error silently reported as zero motion"},
{"severity": "minor", "file": "src/va/registry.py", "line": 309, "issue": "The lnr-eventlog registry branch and its host/tz spec-merge have no test coverage", "scenario": "A typo in the ('host','tz') filter or param plumbing ships green because tests instantiate the adapter directly, surfacing only during the credential-gated live probe (WS4.a2)"},
{"severity": "minor", "file": "src/va/adapters/motion_source/lnr_eventlog_inproc.py", "line": 83, "issue": "VA_NVR_TZ env var and the tz role-spec key are documented only in the adapter docstring", "scenario": "A session debugging hour-shifted probe output finds no tz knob in CLAUDE.md or the COORDINATION entry, which name only VA_NVR_HOST/USER/PASS"}
]}
```
