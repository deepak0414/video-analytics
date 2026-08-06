# Agent review — approve

date: 2026-08-05T11:25:10.353967
range: origin/main..HEAD
branch: loop/ws4c-nvr-chunk-source
findings: 2

- **minor** `src/va/sources/nvr.py:23` — Module docstring (and _curl_config comment) claim the NVR speaks digest auth, but nvr-access-notes.md records the CGI endpoints are Basic-only and --digest silently 401s.
  - scenario: A future session hardens the transport to --digest per the docstring; every transfer 401s silently and pulls fail with the misleading 'could not fetch a live reference frame' error, with the correct recipe contradicted by the code's own comments.
- **minor** `src/va/sources/nvr.py:265` — The corrupt-JPEG dirty-sentinel path added for the round-5 finding has no test — the device-layer stub bypasses _frame_hammings entirely.
  - scenario: A later cleanup removes the 'redundant' try/except; no test goes red, and the next flaky-device pull with a torn >500-byte frame JPEG aborts the whole ingest again instead of counting the frame dirty.

---

## Full review

## Review: `origin/main..HEAD` (WS4.b motion-episode scene detection + WS4.c nvr_recorded chunk source)

I could not execute the suite myself — another pytest run is live (`pgrep` shows two), and the repo's no-pile-up lesson forbids stacking a third. This review is full static analysis of both commits, the surrounding pipeline/provenance/stale/manage code, the loop plan's done-when items, prior review rounds, and the NVR experiment notes.

**Prior-round verification.** All four round-5 findings are genuinely addressed at HEAD, each with the failure mode I'd probe for closed:

1. **Missed stale (round-5 major):** `provenance.py:91-97` folds the `motion_source` spec into the scene_detector fingerprint only when `model == "motion-episodes"`, with the default `"sidecar"` matching `get_motion_source`'s fallback. `stale.py:54` computes the current fingerprint under the video's *recorded* footage profile (`config_for(v.profile, ...)`), so the fold actually triggers for security-profile chunks — the sidecar→lnr-eventlog switch now reads stale. Test `test_motion_source_config_is_part_of_scene_provenance` covers both the fold and the visual-model independence.
2. **Corrupt-JPEG abort:** `nvr.py:265` wraps the per-frame hash in try/except appending the dirty sentinel — but see finding 2 below.
3. **Env-dependent identity:** `resolve()` stores a canonical fully-UTC URI; `test_stored_uri_is_canonical_utc_and_env_independent` re-resolves under a different `VA_NVR_TZ` and asserts identical identity.
4. **120 s cap:** documented in the new CLAUDE.md `nvr://` block.

**What else I checked and found sound:** the `SceneDetector.detect` widening is defaulted and logged in COORDINATION.md, with both existing visual backends accept-and-ignore and no test doubles of `detect` broken (grepped); both degraded modes (no epoch, MotionSource failure) warn and never abort ingest, with tests; the reingest rework attaches chunk metadata *before* ingest so Role 1 runs epoch-aware, keeps the failure-path retry property, and the NVR branch parks preserved media where `fetch()` looks so an expired window never forces a re-pull (all three tested, including the deleted-camera degrade); `chunk_bounds`/`longest_clean_run` arithmetic is correct at the boundaries the tests pin; credential handling (stdin `--config -`, quote/backslash escaping, newline rejection, no argv leakage) is tested; `_pull_window` uses a pull-private temp dir plus atomic `os.replace` so the trusted cache can never hold a torn clip, and the finally block always stops the load session; the four `security.yaml` copies are byte-identical; the timeline-drift limitation is disclosed in CLAUDE.md/COORDINATION.md and recorded in the loop backlog rather than presented as accurate; both done-when items have literal ground-truth tests; new env vars/URIs/knobs (`VA_NVR_TZ`, `nvr://` form, `query_margin_s`, the profile default) are documented in the same change.

**Findings (both minor):**

1. **Minor — `src/va/sources/nvr.py:23` — the module docstring's auth claim contradicts the proven recipe.** It states "the NVR speaks digest auth for these binary endpoints" (echoed at line 211), but `nvr-access-notes.md` records the opposite, twice: "HTTP CGI = Basic auth, not Digest → `curl --anyauth`/`--basic`. `--digest` silently returns 401." The code works because `--anyauth` negotiates, but the comment is a foot-gun: a future session "hardening" the transport to `--digest` per the docstring would 401 every transfer silently, surfacing only as the misleading "could not fetch a live reference frame" error. Safe path: correct both comments to say the CGI endpoints are Basic-auth (negotiated via `--anyauth`), citing the notes.

2. **Minor — `src/va/sources/nvr.py:265` — the round-5 corrupt-JPEG fix has zero coverage.** The `except Exception → dirty sentinel` path in `_frame_hammings` is the fix for a review-confirmed ingest-abort bug, but no test exercises it (the device-layer stub bypasses `_frame_hammings` entirely; only the pure helpers are tested). Per the repo's own lesson, a fix without a test that fails on the old code is one refactor away from silently regressing to the abort. Safe path: a unit test that monkeypatches `PIL.Image.open` (or plants a >500-byte garbage JPEG via a stubbed ffmpeg call) and asserts the frame is counted dirty rather than raising.

Verdict: approve — no critical or major findings.

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "src/va/sources/nvr.py", "line": 23, "issue": "Module docstring (and _curl_config comment) claim the NVR speaks digest auth, but nvr-access-notes.md records the CGI endpoints are Basic-only and --digest silently 401s.", "scenario": "A future session hardens the transport to --digest per the docstring; every transfer 401s silently and pulls fail with the misleading 'could not fetch a live reference frame' error, with the correct recipe contradicted by the code's own comments."},
{"severity": "minor", "file": "src/va/sources/nvr.py", "line": 265, "issue": "The corrupt-JPEG dirty-sentinel path added for the round-5 finding has no test — the device-layer stub bypasses _frame_hammings entirely.", "scenario": "A later cleanup removes the 'redundant' try/except; no test goes red, and the next flaky-device pull with a torn >500-byte frame JPEG aborts the whole ingest again instead of counting the frame dirty."}
]}
```
