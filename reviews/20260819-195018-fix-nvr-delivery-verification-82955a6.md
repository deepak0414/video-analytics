# Agent review — approve

date: 2026-08-19T19:57:42.178731
range: origin/main..HEAD
branch: fix/nvr-delivery-verification
findings: 1

- **minor** `src/va/sources/nvr.py:280` — The fetch() cache-hit TRIM outcome (os.replace of the trimmed clip over the cached original) has no test — only the reject and clean-accept cache paths are covered.
  - scenario: A regression in the two-line trim branch (path comparison or replace direction) would leave a pre-gate contaminated cache clip in place at the trusted path; fetch() returns and ingests it — silent contamination pass-through on the exact upgrade path this commit exists to close — with the suite still green. Safe path: add a fetch()-level test with a fabricated foreign-head cache file asserting the cached clip is replaced by the trimmed one.

---

## Full review

My independent review is complete. Here is the full assessment.

# Review: `82955a6` — NVR delivery verification (origin/main..HEAD, round 4)

**Scope:** one commit — `src/va/sources/verify.py` (new pure verifier + seam), `src/va/sources/nvr.py` (gate wiring, cache re-verification, `_parse_main_stream`), `src/va/media/frames.py` (`first_frames`), `src/va/media/synth.py` (`write_frames_video`), two test files, CLAUDE.md, COORDINATION.md. A pytest run from another session is live (PID 1127082), so per the repo lesson I did not launch one; I verified the test arithmetic and wiring by tracing every case.

## Prior findings — verified resolved at this HEAD

The round-3 minor (multi-entry `VA_NVR_MAIN_STREAM` pooled resolutions × fps) is fixed on the offered safe path: `_parse_main_stream` now returns a frozenset of `(w, h, fps_or_None)` tuples, the verifier matches them as pairs, a non-blank-but-empty spec raises rather than silently deactivating the gate, and both the parser (`test_parse_main_stream_keeps_pairs_and_fails_closed_on_garbage`) and the verifier pairing (`test_stream_identity_matches_resolution_and_fps_as_a_pair`) are pinned — including the exact cross-match scenario (2688x1520@15 rejected against a two-entry profile).

## Suspicions traced and dismissed

- **Recheck epoch math:** after a trim, the recheck's `start_epoch + trim_before_s` is consistent with the documented "t=0 lags start_epoch by the trim" model; under the alternative displacement model the residual skew equals the sub-second trim, well inside the 5 s clock tolerance either way. A second-round "trim" verdict correctly fails closed rather than looping. The stub-clock-reader test exercises the shifted-epoch flow end to end.
- **Fail-open on missing signals:** `frames_at` raises rather than returning empty on an undecodable clip, so `if body:` cannot silently skip the head check; a probe/decode exception propagates and aborts the pull (fail closed, not fail open). `observed.resolution is None` skipping the stream check is unreachable for any mp4 imageio can decode.
- **Trim bounds:** `_trim_encode(cut, trim, window_len, trimmed)` matches the `(input, start_s, end_s, out)` signature used in `_pull_window`; the cut's timeline is `[0, window_len]` so the bounds are right, and ffmpeg stops at EOF if the cut ran short within tolerance.
- **Test arithmetic:** every synthetic case checks out — 0.5 s foreign head at 10 fps = frames 0–4 foreign, trim to t=0.5, kept 5.0 s; the min-kept reject (1.0 s window, trim 0.5 → 0.5 s kept < 1.0); the deterministic-blindness reproduction (frame 0 marker visible to `first_frames`, invisible at t=0.25).
- **`_det_harness` stubbing `_verify_and_trim` to pass-through** is a justified scope split (its 4 KB fake cuts can't decode), and the pull-loop × verification integration is separately covered by `test_pull_fails_closed_when_delivery_cannot_be_verified`, which drives the real gate through both phases to the fail-closed `RuntimeError` with no file landed.
- **Honesty of claims:** the same-camera wrong-week gap (clock gate shipped as a seam with no default OCR reader — the census's mandatory item 1) is disclosed consistently in the module docstring, CLAUDE.md, and the ⚠ COORDINATION.md entry, with "do NOT treat re-pulls as fully safe" stated outright. The identity threshold (20) is calibrated against measured census bands and pinned by a test — this satisfies the hardcoded-content rule.
- **Contract/doc parity:** the pull-contract change is logged with ⚠ in COORDINATION.md; `VA_NVR_MAIN_STREAM`, `first_frames`, and `write_frames_video` are documented in the same change. The commit subject is a provisional `need_agent_review:` — exempt from the plain-description rule.

## Finding (1)

**1. minor — `src/va/sources/nvr.py:280` — the fetch() cache-hit TRIM branch (`os.replace(verified, out)`) has no test.** The cache-hit scenarios covered are reject→set-aside→re-pull and clean→reuse; the third outcome — a cached pre-gate clip that verification *trims*, where the trimmed file must replace the contaminated original in place — is exactly the upgrade path for pre-fix `.va-24h`-era cache files this commit motivates, and it is uncovered. *Failure scenario:* a regression in that two-line branch (e.g. the `Path(verified) != out` comparison or the replace direction) would leave the contaminated original at `out`, which `fetch()` then returns and ingests — silent contamination pass-through on precisely the path the gate was added to close, and no test would go red. *Safe path:* one test fabricating a foreign-head cache file with `write_frames_video` (the pieces already exist in `test_nvr_contamination.py`), calling `fetch()`, and asserting the cache file's head is clean and its duration shortened.

## Verdict

**approve** — no critical or major findings. The pure-verifier seam, fail-closed pull integration, cache set-aside recovery, paired stream-profile matching, honest partial-coverage disclosure, and regression tests that reproduce the original sampler blindness are all sound. The one minor is a coverage gap on a two-line branch whose current implementation I verified correct by reading.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/sources/nvr.py", "line": 280, "issue": "The fetch() cache-hit TRIM outcome (os.replace of the trimmed clip over the cached original) has no test — only the reject and clean-accept cache paths are covered.", "scenario": "A regression in the two-line trim branch (path comparison or replace direction) would leave a pre-gate contaminated cache clip in place at the trusted path; fetch() returns and ingests it — silent contamination pass-through on the exact upgrade path this commit exists to close — with the suite still green. Safe path: add a fetch()-level test with a fabricated foreign-head cache file asserting the cached clip is replaced by the trimmed one."}]}
```
