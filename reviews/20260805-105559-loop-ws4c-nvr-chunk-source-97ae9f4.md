# Agent review — approve

date: 2026-08-05T11:00:09.449356
range: origin/main..HEAD
branch: loop/ws4c-nvr-chunk-source
findings: 2

- **minor** `src/va/sources/nvr.py:205` — _curl_config escapes backslash and double-quote but not CR/LF, so a credential containing a newline still silently mangles the curl config line (the remainder becomes a stray config directive).
  - scenario: VA_NVR_PASS containing a newline works for the in-process lnr adapter but breaks every curl transfer: digest auth 401s and the pull dies with the misleading 'could not fetch a live reference frame' error — the same masked-credential failure the escaping fix targeted; safe path is rejecting \r/\n in user/password with a clear RuntimeError when composing the config line.
- **minor** `src/va/sources/nvr.py:270` — _pull_window calls _reference_hash (and _ffmpeg) after mkdtemp but before the try/finally that removes the temp dir, so a reference-fetch failure leaks the pull-private directory.
  - scenario: An offline camera (snapshot.cgi returns nothing or <2000 bytes) raises RuntimeError before the try block, leaving a '<key>.pull-*' dir with the partial ref jpg in cache/ on every failed attempt; repeated retries accumulate junk dirs. Safe path: move the reference fetch inside the existing try so the finally's rmtree covers it.

---

## Full review

Review complete. This is the fourth round on this branch; the two commits in range are WS4.b (motion-episode scene detection) and WS4.c (the `nvr_recorded` chunk source). The only change since the last approved round is the curl-config credential-escaping fix (`_curl_config` + `test_curl_config_escapes_quote_and_backslash`), which I verified correct for backslash and double-quote (escape ordering is right, and the test's expected literal decodes to the correct config line).

## What I verified as clean

- **Prior-round fixes hold in HEAD.** Credentials travel via `--config -` on stdin with argv asserted credential-free; `_preattach_chunk_metadata` runs before `ingest()` with epoch-first ordering and a warn-not-crash on a deleted camera (regression-tested); `set_camera` validates the camera row; `CameraStore.get_or_create` is atomic and never clobbers a rename; NVR reingest re-parks the preserved clip exactly where `fetch()` looks (`cache/<source_key with : → _>.mp4` — I confirmed the two path constructions match) and is tested against a dead-device stub.
- **Correctness sweep.** `longest_clean_run`/`chunk_bounds`/clamp-and-merge logic hand-traced against the tests, including the beyond-chunk drop and padding-overlap merge; the `query_margin_s` widen-then-clamp behavior has both a forwarding test and the live-repro clamp test; `_frame_hammings` keeps the index→time mapping intact for unreadable frames; frame-count can't hit the `%03d` lexicographic-sort hazard (10 s × 4 fps ≈ 40 frames). The reingest-of-a-vanished-local-file worry dissolves: the old code failed at the same effective point inside `ingest()`.
- **Contracts & plan.** `SceneDetector.detect`'s new optional `context` is defaulted, all backends accept it, and no test doubles of the detector exist (checked for the lambda-double lesson). Both interface changes are logged in COORDINATION.md. WS4.b/WS4.c "Done when" items are covered by literal-ground-truth tests (`[(1.0, 4.0)]` end-to-end oracle, idempotent re-ingest, epoch+camera on the row); the WS3.a carry-overs (FK enforcement, atomic get_or_create) and the WS4.a round-8 carry-over (unparseable End Time warns) are all landed with tests. The timeline-drift limitation is honestly recorded as backlog, warned at runtime, and documented.
- **Best-effort discipline & combinations.** MotionSource failure and missing epoch degrade to a warned full-span segment; the security profile switch is replicated in all four config dirs with a measured 21/22-clip equivalence note; the device layer is stubbed offline with live validation stated. Docs parity is good (`nvr://` form, env reuse, `query_margin_s`, sidecar-warns caveat all in CLAUDE.md within the change). No disputes in workflow-trust-plan.md touch these findings.

## Findings (2 minor)

**minor — `src/va/sources/nvr.py:205`** — `_curl_config` escapes `\` and `"` but not CR/LF, so a `VA_NVR_PASS` containing a newline still silently mangles the credential — the remainder of the password becomes a stray curl config directive. Every transfer then 401s and the ingest dies with the misleading "could not fetch a live reference frame" error, the exact failure mode the escaping fix was meant to close (the prior round's recommendation explicitly included rejecting embedded newlines). Safe path: raise a clear `RuntimeError` from `_curl_config` (or `_conn`) when user/password contain `\r`/`\n`.

**minor — `src/va/sources/nvr.py:268-270`** — `_pull_window` creates the pull-private temp dir before the `try`, but `_reference_hash` (which raises on a documented real failure mode: camera offline / undersized snapshot) runs before the `try` too, so that failure leaks the `*.pull-*` dir into `cache/`. Repeated failed pulls from an offline camera accumulate junk dirs in the workdir cache. Safe path: move the `refh = self._reference_hash(...)` (and `_ffmpeg()`) inside the existing `try` so the `finally`'s `rmtree` covers them.

**Verdict: approve** — no critical or major findings.

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "src/va/sources/nvr.py", "line": 205, "issue": "_curl_config escapes backslash and double-quote but not CR/LF, so a credential containing a newline still silently mangles the curl config line (the remainder becomes a stray config directive).", "scenario": "VA_NVR_PASS containing a newline works for the in-process lnr adapter but breaks every curl transfer: digest auth 401s and the pull dies with the misleading 'could not fetch a live reference frame' error — the same masked-credential failure the escaping fix targeted; safe path is rejecting \\r/\\n in user/password with a clear RuntimeError when composing the config line."},
{"severity": "minor", "file": "src/va/sources/nvr.py", "line": 270, "issue": "_pull_window calls _reference_hash (and _ffmpeg) after mkdtemp but before the try/finally that removes the temp dir, so a reference-fetch failure leaks the pull-private directory.", "scenario": "An offline camera (snapshot.cgi returns nothing or <2000 bytes) raises RuntimeError before the try block, leaving a '<key>.pull-*' dir with the partial ref jpg in cache/ on every failed attempt; repeated retries accumulate junk dirs. Safe path: move the reference fetch inside the existing try so the finally's rmtree covers it."}
]}
```
