# Agent review — approve

date: 2026-08-19T19:34:56.351272
range: origin/main..HEAD
branch: fix/nvr-delivery-verification
findings: 1

- **minor** `src/va/sources/nvr.py:211` — _parse_main_stream pools resolutions and fps values across comma-separated VA_NVR_MAIN_STREAM entries instead of keeping (resolution, fps) pairs, so a delivery matching one entry's resolution and another entry's fps passes the stream-identity check.
  - scenario: A two-camera install sets VA_NVR_MAIN_STREAM="2688x1520@20,1920x1080@15" (the documented multi-entry form, required because the var is shared across channels); a wholly-foreign wrong-feed delivery at 2688x1520@15 or 1920x1080@20 passes the stream gate, and the self-referential head check passes trivially on a self-consistent foreign clip, so the wrong feed ingests.

---

## Full review

The prior two review rounds' findings are all addressed at this HEAD — I verified each fix in the code rather than re-litigating. Now writing up my full assessment.

# Review: `a54dcff` — NVR delivery verification (origin/main..HEAD, round 3)

**Scope reviewed:** one commit — `src/va/sources/verify.py` (new), `src/va/sources/nvr.py`, `src/va/media/frames.py`, `src/va/media/synth.py`, `tests/test_nvr_contamination.py` (new), `tests/test_nvr_source.py`, `CLAUDE.md`, `COORDINATION.md`. A pytest run from another session is live (PID 826857), so per the repo lesson I did not launch one; I verified test arithmetic and wiring by tracing.

## Prior findings — verified resolved

- **Round 1 major (clock-gate overclaim):** claims in CLAUDE.md, COORDINATION.md, and the module docstring are now narrowed to the honest coverage statement (same-camera wrong-week caught only by the not-yet-shipped clock reader; "do not treat re-pulls as fully safe"). Resolved along the offered safe path.
- **Round 1 minors:** both test files now carry autouse `delenv("VA_NVR_MAIN_STREAM")` fixtures; `fetch()` re-verifies cache hits (tested); the `TimestampReader` seam is exercised end-to-end through `NvrRecordedSource` by two stub-reader tests including the recheck's shifted-epoch flow.
- **Round 2 minors:** the `_pull_window` docstring now says "fetch() RE-VERIFIES an existing cache file… but the atomic rename must still hold" (nvr.py:502); a cache-hit `DeliveryRejected` now sets the bad clip aside as `.rejected.mp4` and falls through to a fresh, re-verifying pull (nvr.py:270-280), with `test_fetch_sets_aside_a_verified_bad_cache_file_and_repulls` pinning both the set-aside and the re-pull, and `test_fetch_reuses_a_verified_clean_cache_file` pinning the flip side.

## Suspicions traced and dismissed

- The set-aside `out.with_suffix(".rejected.mp4")` is safe: the source-key-derived stem contains no dots, so the suffix swap can't mangle the name, and `os.replace` handles a stale aside from an earlier failure.
- If the re-pull after a cache set-aside itself fails, the `RuntimeError` propagates and ingest lands `failed` — same contract as any unpullable window; nothing half-written remains at the trusted path.
- The `_det_harness` pass-through stub of `_verify_and_trim` is a justified scope split (its 4 KB fake cuts can't decode), and the pull-loop × verification integration is separately covered by `test_pull_fails_closed_when_delivery_cannot_be_verified`.
- Trim math and the recheck's `start_epoch + trim` shift are internally consistent and now covered by the stub clock-reader test; a second-round "trim" verdict on the recheck correctly fails closed rather than looping.
- Single-frame body reference at the clip midpoint: the identity band (same-camera ≤18, cross-camera ≥24, threshold 20) was measured on the census's real motion-episode clips, so motion-in-head is inside the calibrated band; the threshold is flagged, calibrated, and pinned by `test_identity_band_constant_separates_the_census_bands` — satisfies the hardcoded-content rule.
- `frames_at` raises rather than returning empty on an undecodable file, so the `if body:` guard cannot silently fail open on a corrupt clip.
- Test env hygiene: the autouse `delenv` fixtures run before each test body, and the tests that need `VA_NVR_MAIN_STREAM` set it explicitly afterward — ordering is correct.

## Finding (1)

**1. minor — `src/va/sources/nvr.py:211` — multi-entry `VA_NVR_MAIN_STREAM` cross-products resolutions × fps instead of matching (resolution, fps) pairs.** `_parse_main_stream` pools all resolutions into one set and all fps values into another, so with the documented comma form — the form a multi-channel install with differing main streams *must* use, since the env var is shared across channels — a delivery matching entry A's resolution and entry B's fps passes the stream-identity check. *Failure scenario:* operator sets `VA_NVR_MAIN_STREAM="2688x1520@20,1920x1080@15"` for a two-camera setup; a census-style wholly-foreign delivery of camera 2's main stream (1920x1080, but served at 20 fps by a re-encoded path, or vice versa: 2688x1520@15) is a wrong feed the check was built to refuse, yet it passes because each half matches a different entry — and the self-referential head check passes trivially on a self-consistent foreign clip. *Safe path:* parse into a frozenset of `(w, h, fps)` tuples (fps `None` when omitted) and have the verifier match a delivered `(resolution, fps)` against one entry; or document that the comma form means "any listed resolution × any listed fps" next to the per-channel-config backlog note in COORDINATION.md. Single-entry configs — the only current deployment — are unaffected, hence minor.

## Verdict

**approve** — no critical or major findings. The pure-verifier design, fail-closed pull integration, cache-hit re-verification with set-aside recovery, honest coverage disclosure, regression tests that reproduce the original blindness, and same-change documentation (env var, COORDINATION ⚠ entry) are all sound. The one minor is a looseness in a config form no current deployment uses.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/sources/nvr.py", "line": 211, "issue": "_parse_main_stream pools resolutions and fps values across comma-separated VA_NVR_MAIN_STREAM entries instead of keeping (resolution, fps) pairs, so a delivery matching one entry's resolution and another entry's fps passes the stream-identity check.", "scenario": "A two-camera install sets VA_NVR_MAIN_STREAM=\"2688x1520@20,1920x1080@15\" (the documented multi-entry form, required because the var is shared across channels); a wholly-foreign wrong-feed delivery at 2688x1520@15 or 1920x1080@20 passes the stream gate, and the self-referential head check passes trivially on a self-consistent foreign clip, so the wrong feed ingests."}]}
```
