# Agent review — approve

date: 2026-08-05T11:32:44.343782
range: origin/main..HEAD
branch: loop/ws4c-nvr-chunk-source
findings: 1

- **minor** `COORDINATION.md:490` — The WS4.c coordination-log entry describes the pull as 'curl digest-auth loadfile', contradicting nvr-access-notes.md and nvr.py's own corrected docstring (endpoints are Basic-only; --digest silently 401s).
  - scenario: An agent in another session reads the COORDINATION entry as the transport recipe and hardens the curl calls to --digest; every transfer 401s silently and pulls fail with the misleading 'could not fetch a live reference frame' error. Fix the wording in this same branch before finalize.

---

## Full review

Review complete. I read both commits in full (WS4.b motion-episode scene detection, WS4.c NVR chunk source), the surrounding pipeline/provenance/manage/storage code, all six prior review rounds for this branch, the loop plan's done-when items, and the NVR access notes. Other pytest runs are live (`pgrep` shows four), so per the repo's pile-up lesson this is a static-analysis review, like round 6.

## Prior-round verification

Both round-6 minors are genuinely fixed at HEAD:

1. The `nvr.py` docstring and `_curl_argv` comment now state the CGI endpoints are **Basic-only** and explicitly warn "do NOT harden to `--digest`, it silently 401s" — matching nvr-access-notes.md.
2. The corrupt-JPEG dirty-sentinel path now has a real test (`test_torn_frame_jpeg_counts_dirty_not_fatal`), which plants a >500-byte garbage JPEG exactly where `_frame_hammings`'s loop looks and asserts the `[10**6]` sentinel instead of a raise.

## What I probed and found sound

- **Time/identity arithmetic:** naive-time → `VA_NVR_TZ`/system-local → UTC conversion, canonical `+00:00` stored URI (env-independent re-resolve, tested), `source_key` floor-consistency between original and canonical forms, `chunk_bounds` tail clipping, `longest_clean_run` inclusive bounds and the `(run[1]+1)/FPS_SAMPLE` trim math, output-side `-ss`/`-to` (frame-accurate).
- **Degraded modes never abort ingest:** missing `start_epoch`, MotionSource failure, dangling `camera_id`, torn frame JPEGs, deleted camera at reingest — each warns, each has a test.
- **Reingest ordering:** `_preattach_chunk_metadata` now runs BEFORE `ingest()` (the round-1 major), with the epoch-first / camera-best-effort ordering reasoned and tested; the NVR branch parks preserved media at exactly the filename `fetch()` checks, and the dead-device test proves no re-pull.
- **Provenance:** the `motion_source` spec folds into the scene_detector fingerprint only for `motion-episodes` (default "sidecar" matches `get_motion_source`'s fallback); `events_file`/`host`/`tz` are output-affecting keys, credentials excluded via `_NON_OUTPUT_KEYS`; visual-model independence tested.
- **Contract changes:** `SceneDetector.detect` widening is defaulted, both visual backends accept-and-ignore, no test doubles broken; `Catalog.set_camera` now raising `ValueError` has exactly two callers, both updated; all changes logged in COORDINATION.md.
- **Credential handling:** stdin `--config -`, quote/backslash escaping, newline rejection, no argv leakage — all tested.
- **Atomicity:** pull-private temp dir + `os.replace`, `INSERT OR IGNORE` camera creation, `finally`-guaranteed `stopLoad`.
- **Plan conformance:** WS4.b's done-when (known windows → exactly those segments, literal ground truth) and WS4.c's (end-to-end ingest lands media + segments + start_epoch; idempotent re-ingest) both have direct tests; both WS3.a carry-overs (FK validation, atomic get_or_create) delivered. Timeline drift from dropped/trimmed chunks is disclosed (CLAUDE.md, warning log) and recorded in the loop backlog, not presented as accurate.
- **Documentation parity:** `nvr://` form, `VA_NVR_TZ` semantics, 120 s cap, security-profile default, `query_margin_s`, and the lighting-match gotcha all landed in CLAUDE.md in this same range; the four `security.yaml` copies are identical.

## Finding (one, minor)

**minor — `COORDINATION.md:490`** — the WS4.c log entry says "curl **digest-auth** loadfile," which is the exact factually-wrong claim round 6 flagged: nvr-access-notes.md records these endpoints as Basic-only (`--digest` silently 401s), and `nvr.py`'s corrected docstring now says so. The fix corrected the code comments but left the contradiction in the cross-agent coordination log — the one place another session goes to learn the recipe. Scenario: an agent in a parallel session reads the COORDINATION entry, "hardens" the transport to `--digest`, and every pull fails with the misleading "could not fetch a live reference frame" error. Safe path: this entry was added by this same unfinalized range, so correct the wording to "Basic auth via `--anyauth`" before finalize (no append-only concern — it's fixing this branch's own uncommitted-to-main text).

Verdict: approve — no critical or major findings.

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "COORDINATION.md", "line": 490, "issue": "The WS4.c coordination-log entry describes the pull as 'curl digest-auth loadfile', contradicting nvr-access-notes.md and nvr.py's own corrected docstring (endpoints are Basic-only; --digest silently 401s).", "scenario": "An agent in another session reads the COORDINATION entry as the transport recipe and hardens the curl calls to --digest; every transfer 401s silently and pulls fail with the misleading 'could not fetch a live reference frame' error. Fix the wording in this same branch before finalize."}
]}
```
