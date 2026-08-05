# Agent review — approve

date: 2026-08-05T14:38:40.021783
range: origin/main..HEAD
branch: loop/ws4b-motion-segments
findings: 0


---

## Full review

All my checks are complete. A note on process: the committing session has a live `pytest -q` full-suite run in flight (which will amend the commit when green), so per the repo's own convention from earlier review rounds I did not launch a competing suite run — my verification of this round is static, by tracing code and contracts.

## Review — round 4, `origin/main..HEAD` (7cace69, WS4.b motion-episode scene detection)

**Verdict: approve.** I independently re-traced the whole change rather than trusting the three earlier review rounds, and found no correctness, contract, test-integrity, plan-conformance, combination, or documentation issues remaining.

What I verified independently:

- **The episode→segment math is correct.** In `motion_episodes_inproc.py` I hand-traced: straddling-start events clamp to 0; entirely-after-chunk events produce negative spans and are dropped by the `min_span_s` filter; padding-induced overlaps (and cross-camera overlaps when `camera_ref` is None) are re-merged so the Role-1 "ordered, non-overlapping" contract holds; an epoch-placed quiet chunk returns zero segments while the two degraded modes (no epoch, source failure) warn and return one full-span segment. Every one of these edges has a literal-ground-truth test.
- **The round-1 major (reingest epoch-blindness) is genuinely fixed.** `manage.py` now pre-attaches `camera_id`/`start_epoch` via `get_or_create` + `set_camera`/`set_start_epoch` *before* `ingest()` runs. I confirmed the pre-created pending row flows correctly through ingest's dedup gate (only `done` rows short-circuit), that `update_metadata`/`set_paths` do not clobber the two columns, and that the failure path still works (the pending row survives a crashed ingest with metadata already on it). The regression test would fail on the old code: post-hoc reattach yields `(0.0, 6.0)` where it asserts `(1.0, 5.0)`.
- **The provenance fold is stale-safe end to end.** `role_fingerprint` folds `motion_source.*` into the scene-detector fingerprint only when the model is `motion-episodes`, and — the part I specifically checked — `va stale` computes current fingerprints per video via `config_for(v.profile, ...)` (`stale.py:52-55`), so a security-profile chunk is compared under the profile that selects motion-episodes, not the base config. Switching sidecar→lnr-eventlog or changing `events_file`/spec knobs reads stale; purely visual backends stay independent (both directions tested). Scene-detector staleness resolves through `va reingest`, which now preserves the epoch — the loop is closed.
- **Round-2 minors are closed with tests:** dangling `camera_id` warns before widening the motion query (`ingest.py:256-265`), the unconfigured sidecar warns (with the configured-but-absent case kept deliberately silent — the test split strengthens both branches), and the real-model combination question is answered by a recorded measurement in all four `security.yaml` copies (pyscenedetect produced 1 segment on 21/22 of the real NVR clips, so the epoch-less full-span fallback is behaviorally identical and the golden fixtures are unaffected).
- **The Protocol extension breaks no caller or double.** `get_scene_detector(...).detect(...)` in `ingest.py:271` is the only production call site; both visual backends accept-and-ignore the context; `tests/test_scene_detector.py` still calls single-arg via the default; no lambda/test doubles of the detector exist (checked for the 2026-08-03 lesson's failure mode). The change is logged in COORDINATION.md and documented in CLAUDE.md with the knobs and degraded modes.
- **Best-effort discipline holds:** MotionSource I/O failure warns and degrades rather than aborting ingest; the lnr flat-shape unparseable-End-Time path now warns (WS4.a round-8 carry-over) with a regression test asserting both the zero-length fallback and the warning.
- **No hardcoded content:** `pad_s`/`gap_s`/`min_span_s` are structural budget knobs, flagged as such in comments and overridable via the spec (proven by `test_spec_knobs_reach_the_backend`); the `"sidecar"` fallback in the provenance fold mirrors the registry's own default.

The commit subject is a provisional `need_agent_review:`, exempt from the clarity rule. The untracked fourth review file (14:27, `fb80488`) is outside the commit range.

```json
{"verdict": "approve", "findings": []}
```
