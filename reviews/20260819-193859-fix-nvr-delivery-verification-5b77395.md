# Agent review — approve

date: 2026-08-19T19:47:13.983917
range: origin/main..HEAD
branch: fix/nvr-delivery-verification
findings: 2

- **minor** `src/va/sources/nvr.py:216` — A non-blank VA_NVR_MAIN_STREAM that parses to zero profiles (e.g. ",") returns None, silently deactivating the stream-identity gate and logging it as 'unset', while every other malformed spec raises ValueError — the gate fails open on this one config typo.
  - scenario: An operator fat-fingers the var to a bare comma while editing a multi-entry list; a 352x240 sub-stream substitution (the census's wholly-foreign class) then ingests with only an info-level log, exactly what the gate exists to refuse. Safe path: raise the same ValueError as other malformed forms when spec is non-blank but yields no profiles.
- **minor** `src/va/sources/nvr.py:183` — The multi-entry comma form of _parse_main_stream — the exact site of the round-3 pooling bug — has no direct test; the pair-matching regression test pins the pure verifier with a hand-built ExpectedProfile, bypassing the parser.
  - scenario: A later refactor regresses the parser (wrong delimiter, dropped pairing, early None return); the verifier test stays green while the deployed gate mis-parses, and the fail-open variants ingest wrong feeds silently. Safe path: add a parser unit test asserting "2688x1520@20,1920x1080@15" maps to {(2688,1520,20),(1920,1080,15)} plus the fps-omitted and error forms.

---

## Full review

Targeted test execution isn't permitted in this review session, so my verdict rests on code tracing; the prior round's reviewer confirmed the suite wiring, and I traced the final-round delta (the stream-profile pairing fix) line by line.

# Review: `5b77395` — NVR delivery verification (origin/main..HEAD, round 4)

**Scope:** one commit — new `src/va/sources/verify.py`, reworked `src/va/sources/nvr.py`, `va.media.frames.first_frames`, `va.media.synth.write_frames_video`, new `tests/test_nvr_contamination.py`, extended `tests/test_nvr_source.py`, CLAUDE.md + COORDINATION.md updates.

## Prior rounds — verified resolved at this HEAD

- Round 1 (clock-gate overclaim) and round 2 (cache-hit re-verify, set-aside recovery) fixes remain in place.
- **Round 3's minor (pooled resolutions × fps in `_parse_main_stream`) is properly fixed**: profiles are now a frozenset of paired `(w, h, fps_or_None)` tuples, matched atomically in `verify_delivery` (`src/va/sources/verify.py:140-155`), and the cross-match scenario is pinned by `test_stream_identity_matches_resolution_and_fps_as_a_pair`.

## Suspicions traced and dismissed

- Trim math: `_head_trim` trims to the first inspected signal after the last foreign one; `-ss` two-decimal rounding can only over-trim by <5 ms of a *clean* frame, never keep a foreign one (frame intervals ≫ rounding error at any plausible fps).
- The recheck after a trim shifts `start_epoch` by the trim and shrinks `window_len_s` consistently with the documented "t=0 lags start_epoch" caveat; a recheck that is anything but `accept` raises — no trim loop.
- `min_kept_s` guard, `max(trim)` combination of the head and clock gates, and the reject-when-foreign-runs-to-inspection-end semantics all check out against the census bands; the identity threshold (20 between measured ≤18/≥24) is flagged, calibrated against ground truth, and pinned by a test — satisfies both the hardcoded-content and determinism-vs-correctness rules.
- Trim bound: both the head gate and `read_head_clock` inspect only `HEAD_FRAMES` (8) frames, so a trim is ≲0.5 s — the undisclosed-large-shift scenario can't occur.
- `fetch()` cache-verify only runs on ingest/reingest paths where roles recompute, so an in-place trim can't desynchronize existing role rows; a `done` video never reaches `fetch()`.
- `.rejected.mp4`/`.verified.mp4` names can't collide with the cache lookup (`<key>.mp4` exact) or `manage.py`'s reingest parking path.
- Coverage honesty: the wholly-foreign-clip blindness of the self-referential head check is real, but the census's four whole-window substitutions were all sub-streams (caught by the stream gate when configured), and the clock gate is the disclosed catch-all; CLAUDE.md/COORDINATION.md/docstring all carry the "do not treat re-pulls as fully safe" disclosure.
- Other test files (`test_watch.py`, `test_relevance_gate_profile.py`) never reach `_verify_and_trim` (pull stubbed pre-cache, or dedup short-circuits fetch), so the ambient-env fixtures in the two NVR files suffice.
- COORDINATION.md carries the ⚠ pull-contract entry; `VA_NVR_MAIN_STREAM` (including the comma form) is documented in the same change. Commit subject is a provisional `need_agent_review:` — exempt.

## Findings (2, both minor)

**1. minor — `src/va/sources/nvr.py:216` — a non-blank `VA_NVR_MAIN_STREAM` that parses to zero profiles (e.g. `","` or `" , "`) silently deactivates the stream-identity gate instead of failing loudly.** `frozenset(profiles) or None` maps the empty set to None, the "unconfigured" state, and `_expected_profile`'s info log then claims the var is "unset". Every *other* malformed spec raises `ValueError` — this one fails open. *Scenario:* an operator fat-fingers the var to a bare comma while editing a two-entry list; the census's 352x240 sub-stream substitution ingests with only an info-level "unset" log, exactly the class the gate was built to refuse. *Safe path:* if `spec` is non-blank but no profiles parsed, raise the same `ValueError` the other malformed forms get.

**2. minor — `src/va/sources/nvr.py:183` — the multi-entry comma form of `_parse_main_stream`, the exact site of the round-3 pooling bug, has no direct test.** `test_stream_identity_matches_resolution_and_fps_as_a_pair` pins the *pure verifier* with a hand-built `ExpectedProfile`; no test feeds a comma-separated spec through the parser. *Scenario:* a later refactor regresses the parser (wrong delimiter, dropped pairing, swapped w/h, or an early `return None`) — the verifier test stays green while the deployed gate mis-parses; the fail-open variants (None, or profiles that match everything) ingest wrong feeds silently. *Safe path:* add a parser unit test asserting `"2688x1520@20,1920x1080@15"` → `{(2688,1520,20),(1920,1080,15)}`, plus the fps-omitted and error forms.

## Verdict

**approve** — no critical or major findings. The pure-verifier seam, fail-closed pull integration, cache re-verification, honest partial-coverage disclosure (clock reader deferred and loudly flagged as census mandatory-item 1), regression tests that reproduce the original sampler blindness, and same-change documentation are all sound. Both findings are hardening at the edges of the env-var parsing.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/sources/nvr.py", "line": 216, "issue": "A non-blank VA_NVR_MAIN_STREAM that parses to zero profiles (e.g. \",\") returns None, silently deactivating the stream-identity gate and logging it as 'unset', while every other malformed spec raises ValueError — the gate fails open on this one config typo.", "scenario": "An operator fat-fingers the var to a bare comma while editing a multi-entry list; a 352x240 sub-stream substitution (the census's wholly-foreign class) then ingests with only an info-level log, exactly what the gate exists to refuse. Safe path: raise the same ValueError as other malformed forms when spec is non-blank but yields no profiles."}, {"severity": "minor", "file": "src/va/sources/nvr.py", "line": 183, "issue": "The multi-entry comma form of _parse_main_stream — the exact site of the round-3 pooling bug — has no direct test; the pair-matching regression test pins the pure verifier with a hand-built ExpectedProfile, bypassing the parser.", "scenario": "A later refactor regresses the parser (wrong delimiter, dropped pairing, early None return); the verifier test stays green while the deployed gate mis-parses, and the fail-open variants ingest wrong feeds silently. Safe path: add a parser unit test asserting \"2688x1520@20,1920x1080@15\" maps to {(2688,1520,20),(1920,1080,15)} plus the fps-omitted and error forms."}]}
```
