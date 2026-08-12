# Agent review — approve

date: 2026-08-11T21:44:43.602566
range: origin/main..HEAD
branch: fix/nvr-dav-direct-decode
findings: 0


---

## Full review

I've completed my review of the range. Let me consolidate.

**Scope:** one commit, `dfe05b0` — removes the `-c copy` mpegts remux from `_fetch_window` (the raw `.dav` is now consumed directly by both downstream ffmpeg consumers with `-fflags +genpts`), surfaces curl's exit code and discards non-zero-exit downloads in the retry loop, raises the transfer ceiling to 180 s, plus a regression test and a dispute record in workflow-trust-plan.md.

**Re-judging the disputed round-1 finding (retry loop lost its payload-validity gate).** The rubric requires me to re-judge on the merits rather than repeat. The dispute holds up:

- The removed "gate" was the mpegts remux — the exact component this branch removes for cause, since it rejected valid footage (the measured 47 MB × 4 "no data" reproduction).
- Retrying a 47–65 MB download because a *decode* failed spends device budget on the least likely remedy, and the measured failure mode from that traffic pattern (wedging the recorder's HTTP service) is worse than the failure it would prevent.
- The downstream failure is loud ("only N decodable frames of M sampled"), and per COORDINATION.md's watcher semantics a failed window HOLDS the watermark, so the operational layer re-pulls on its own schedule — the failure is recoverable, not silent.
- The proposed future fix (cheap dhav magic-bytes sniff, not an in-loop decode) is the right shape if a large-but-undecodable payload is ever actually observed.

I accept the dispute and do not repeat the finding.

**Round-1 minor 2 (silent `--max-time` truncation) is genuinely fixed.** `_curl` now returns the exit code, `_fetch_window` discards and retries on non-zero, and the new test constructs the exact scenario (5000-byte partial file + exit 28), asserts the discard/retry/cleanup behavior, and would fail against the dav-direct code without the rc gate. The fake curl, patched `_conn`/`_stop_load`/`time`, and the `len(calls) == 4` retry assertion are all sound — I verified the control flow by hand since two pytest runs are already live (per the 2026-08-04 lesson I did not launch another).

**Suspicions that dissolved on inspection:**

- *Clock consistency:* both remaining ffmpeg consumers (`_frame_hashes`, `_trim_encode`) use identical `-fflags +genpts` input flags, so the sampling index→time mapping and the trim bounds stay mutually consistent across the format change; the docstrings carry ground-truth measurements (28.00 s from a `-ss 2 -to 30` trim), satisfying the determinism-is-not-correctness rule.
- *`_snapshot_hash` ignoring the new return value:* explicitly sanctioned by the `_curl` docstring (best-effort caller) and it independently size-checks the snapshot file.
- *curl without `--fail`* (HTTP 500 error body with rc 0 passing the size gate): pre-existing, not introduced by this diff, and the same class of event the accepted dispute covers (loud downstream failure, container-sniff as the future fix).
- *Contract/docs:* the external surface (cached mp4, `source_key`, schema, env vars) is unchanged — no COORDINATION.md entry owed, no documentation-parity gap; the 180 s ceiling is hardcoded *structure* (a budget) with its sizing derivation in a comment, compliant with the hardcoding rule. The stale "remux to mpegts" mentions live in `nvr-access-notes.md`/`chat-interface-plan.md`, which are untracked notes files outside this commit.
- *Combination coverage:* the change is confined to the real-NVR device layer (stubbed in all offline tests, exercised only via `nvr://` pulls under the security profile); the dhav-direct decode path cannot be tested offline and is validated by the live measurements the docstrings record. No stub/real divergence is introduced.
- *Commit message:* provisional `need_agent_review:` subject — exempt from the clarity rule until finalize.

**Verdict: approve** — the disputed finding is accepted as resolved, the actionable finding from prior rounds is fixed with a real regression test, and nothing new surfaced.

```json
{"verdict": "approve", "findings": []}
```
