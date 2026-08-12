# Agent review — approve

date: 2026-08-11T21:47:31.791422
range: origin/main..HEAD
branch: fix/nvr-dav-direct-decode
findings: 0


---

## Full review

Two pytest runs are already live — per the 2026-08-04 lesson I won't launch another; I verified the new test's control flow by hand instead (patched `_conn`/`_stop_load`/`time`, fake curl returning 28 with a 5000-byte partial: the rc-gate discards each attempt, `calls == 4` matches `MAX_TRIES = 4`, and without the rc gate the size gate would return the partial on attempt 1, so the test genuinely reproduces the bug).

## Review — `origin/main..dd9ddbf` (fix/nvr-dav-direct-decode)

**Scope:** one commit. It removes the `-c copy` mpegts remux from `_fetch_window` (downstream consumers `_frame_hashes` and `_trim_encode` now read the raw `.dav` directly with `-fflags +genpts`), surfaces curl's exit code and discards non-zero-exit downloads in the retry loop, raises the transfer ceiling to 180 s with a sizing derivation, adds a regression test for the truncated-download case, and extends the dispute record in `workflow-trust-plan.md` with an occurrence log. The code is byte-identical to the previously approved `dfe05b0`; the only delta since that review is the occurrence-log paragraph.

**Re-judging the recorded dispute** (round-1 minor 1: the retry loop lost its payload-validity gate). The rubric requires judging on merits, not repetition. The dispute holds: the removed "gate" was the mpegts remux this branch removes for cause (it rejected valid footage — the measured 47 MB × 4 "no data" reproduction), in-loop decode retries spend device budget on the least likely remedy and reproduce the traffic pattern that wedged the recorder, the downstream failure is loud (`only N decodable frames of M sampled` RuntimeError), and the watch driver re-pulls failed windows. The newly added occurrence log honestly records ONE observed instance of the failure class in 300+ pulls, with a concrete retraction trigger (persistent recurrence on the same window → add the dhav magic-bytes sniff). That is the dispute mechanism working as designed. Not repeated.

**Suspicions checked that dissolved:**

- *Truncation fix integrity:* `_curl` now returns the exit code; rc ≠ 0 discards before the size gate; the test constructs the exact scenario and would fail without the gate. Genuine fix for round-1 minor 2.
- *Clock consistency across the format change:* both remaining ffmpeg consumers use identical `+genpts` input flags, so the frame-index→time mapping and trim bounds stay mutually consistent; the docstrings carry ground-truth measurements (`-ss 2 -to 30` → 28.00 s), satisfying the determinism-is-not-correctness rule.
- *`_snapshot_hash` ignoring rc:* sanctioned best-effort caller with its own size gate.
- *curl without `--fail`* (HTTP error body, rc 0): pre-existing, caught by the size gate or the loud decode failure — same class the dispute covers.
- *Contracts/docs/combinations:* no schema, env var, CLI, or config surface changed; `src/va/sources/` is not on the critical-paths list; the change is confined to the real-NVR device layer (stubbed in all offline tests), and the dav-direct path is validated by the recorded live measurements. The 180 s ceiling is hardcoded *structure* (a budget) with its derivation in a comment — compliant.
- *Commit message:* provisional `need_agent_review:` subject — exempt until finalize.

**Verdict: approve.** The one actionable prior finding is fixed with a test that reproduces it, the disputed finding is re-judged and accepted with an honest occurrence log, and nothing new surfaced.

```json
{"verdict": "approve", "findings": []}
```
