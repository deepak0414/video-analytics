# Agent review — approve

date: 2026-08-04T14:47:36.115789
range: origin/main..HEAD
branch: feature/nvr-golden-fixtures
findings: 1

- **minor** `tests/golden_queries/nvr0801_clip15_cam2.yaml:13` — The fixture's only assertion is a no_match, so it passes vacuously when the detector silently stores zero detections — the exact batch-ingest YOLO-starvation failure mode this same commit documents — defeating its stated purpose of alerting if the person×7 hallucination returns.
  - scenario: The .va-nvr workdir is rebuilt via a single-process batch ingest; YOLO silently starves after clip 1, clip 15 gets zero object rows, and nvr15-obj-01 stays green even though the detector never ran on the clip — while a returning hallucination it claims to pin would also be invisible. Add a positive-control object query (e.g. car — the parked BMW is in cam 2's static scene per ground-truth line 1, and clip 01's cam-2 fixture already asserts it) so a dead detector turns the fixture red.

---

## Full review

All fixture YAMLs parse cleanly and the counts (24 cases, 8 xfails) match the README's claimed calibration run exactly.

## Review summary

This commit adds nine golden-fixture YAMLs for real NVR footage plus set documentation. I verified it thoroughly and it holds up well:

- **Harness compatibility** — every field used (`modality`, `expect`, `verify`, `xfail`, `provenance`, `default_min_score`, `source_key`) is consumed by `tests/test_golden_queries.py`; no unsupported keys, all YAML parses.
- **Ground-truth fidelity** — the fixture headers quote the owner's untracked `security_footage_golden_truth_august_1_2026_noon_to_2pm` file accurately for all nine clips; no invented content. All nine `source_key` sha256 values match the actual clip files in `/home/debug/nvr-clips-2026-08-01/`, and the `sha256:` prefix matches `sources/local.py`.
- **Profile claim** — the documented narrowed vocabulary `[person, car, truck, bicycle, package, dog, cat]` matches `config/profiles/footage/security.yaml` verbatim.
- **Determinism-vs-correctness** — this change is the ground-truth validation layer itself; xfails cite measured spike-scorecard numbers, and the README correctly distinguishes the in-ingest vs standalone-2fps detector regimes rather than papering over the delta.
- **Counts** — 24 cases / 8 xfails matches the claimed first run (16 passed / 8 xfailed / 0 failed).
- **Documentation parity** — the set README documents the new workdir, ingest recipe, profile, and the batch-ingest YOLO-starvation foot-gun in the same change. The `human-reviewed` label attestation is enforced at PR time by the critical-paths gate, as designed.

One finding survived verification:

**minor — `tests/golden_queries/nvr0801_clip15_cam2.yaml:13`** — the clip-15 fixture's *only* assertion is a `no_match` on `person`, so it passes vacuously if the detector silently stored zero rows for this clip. That is exactly the failure mode this same commit documents (batch ingest "silently starves YOLO after the first clip"): a re-ingested `.va-nvr` hit by that bug would leave the "hallucination returns → red" alert permanently green-and-blind. Clip 01 shows the right pattern — it pairs its `person` no_match with a `car` match anchor, and the same parked BMW is in cam 2's static scene here (ground-truth line 1), so the identical anchor is available. Safe path: add a positive-control query (e.g. `{modality: object, query: "car", expect: match}`) so a dead detector turns the fixture red instead of vacuously green.

Two suspicions I chased and dropped: the `driving` action `no_match` xfail on clip 00 is consistent with the security profile (it gates speech + detector vocab but leaves the action recognizer running with the default vocabulary, so X-CLIP's parked-car "driving" false positive does fire, matching the 8-xfail run count); and clip 14's single xfail'd `match` doesn't share clip 15's vacuous-pass risk, since a dead detector leaves it xfail rather than falsely green.

Verdict: approve — the single finding is minor.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "tests/golden_queries/nvr0801_clip15_cam2.yaml", "line": 13, "issue": "The fixture's only assertion is a no_match, so it passes vacuously when the detector silently stores zero detections — the exact batch-ingest YOLO-starvation failure mode this same commit documents — defeating its stated purpose of alerting if the person×7 hallucination returns.", "scenario": "The .va-nvr workdir is rebuilt via a single-process batch ingest; YOLO silently starves after clip 1, clip 15 gets zero object rows, and nvr15-obj-01 stays green even though the detector never ran on the clip — while a returning hallucination it claims to pin would also be invisible. Add a positive-control object query (e.g. car — the parked BMW is in cam 2's static scene per ground-truth line 1, and clip 01's cam-2 fixture already asserts it) so a dead detector turns the fixture red."}]}
```
