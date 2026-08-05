# Agent review — approve

date: 2026-08-05T09:23:56.230465
range: origin/main..HEAD
branch: loop/ws4b-motion-segments
findings: 0


---

## Full review

Two pytest runs are already live, so per the repo lesson I will not launch another — verification of this round is static, as in round 2.

## Review — round 3, `origin/main..HEAD` (f811d2b, WS4.b motion-episode scene detection)

**Verdict: approve.** All five findings from the previous two review rounds are genuinely fixed in this commit, and my independent trace of the new code found no remaining correctness, contract, test-integrity, or documentation issues.

What I verified:

- **Round-1 major (reingest epoch-blindness) is fixed correctly.** `reingest_video` now pre-attaches `camera_id`/`start_epoch` to the recreated row *before* calling `ingest()` (`src/va/pipeline/manage.py:123-149`), so the motion-episodes backend sees the wall-clock placement during Role 1. The failure path is still covered — the pre-created pending row survives an ingest crash and a later plain `va ingest` completes it with the metadata already on it. The regression test (`test_reingest_keeps_motion_segmentation`) would fail on the old code: post-hoc reattach would have produced a `(0.0, 6.0)` full-span row where it asserts `(1.0, 5.0)`.
- **Round-2 minors are both closed:** the dangling-`camera_id` case now warns before degrading to an unfiltered motion query (`src/va/pipeline/ingest.py:256-265`, with `test_dangling_camera_id_warns_and_still_ingests`), and the real-model + security combination now carries the requested measurement in all four `security.yaml` copies (pyscenedetect produced 1 segment on 21/22 of the `.va-nvr` clips, so the epoch-less full-span fallback is behaviorally identical; the one 2-segment outlier is disclosed).
- **The pad/clamp/merge math is correct on every edge I traced by hand:** straddling-start clamps to 0, beyond-end events collapse to negative spans and drop, padding-induced overlaps re-merge to keep the "ordered, non-overlapping" Role-1 contract, slivers below `min_span_s` drop, and epoch-placed-but-quiet chunks correctly return zero segments (distinct from the two warning degraded modes). Each edge has a literal-ground-truth unit test.
- **The `SceneDetector.detect(video_path, context=None)` extension is source-compatible.** Both existing backends were updated, the only external caller passing the new argument is `ingest.py`, no test doubles of the detector exist (I checked for the 2026-08-03 lambda-double failure mode; `tests/test_scene_detector.py:16` still calls single-arg and works via the default), and the change is logged in COORDINATION.md and documented in CLAUDE.md with its knobs.
- **Best-effort discipline holds:** a MotionSource failure and a missing `start_epoch` both warn and degrade to one full-span segment rather than aborting ingest; the unconfigured-sidecar case now warns (with a test) so a config gap is no longer indistinguishable from a quiet chunk.
- **No tests were deleted or weakened** — the sidecar test split strengthens both branches, and the lnr flat-shape End-Time warning closes the WS4.a round-8 carry-over with a regression test.
- **Registry/config wiring is proven by test:** the profile overlay reaches the raw spec dict the registry reads (`test_spec_knobs_reach_the_backend`), and the security profile selects the backend while generic stays on histogram.

The commit subject is a provisional `need_agent_review:`, exempt from the clarity rule. The diff touches golden-verified critical paths, so the WT.7 label will be mechanically required at PR time — that is the human's attestation to give, not a code finding.

```json
{"verdict": "approve", "findings": []}
```
