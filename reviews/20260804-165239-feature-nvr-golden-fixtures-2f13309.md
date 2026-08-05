# Agent review — approve

date: 2026-08-04T16:56:27.184973
range: origin/main..HEAD
branch: feature/nvr-golden-fixtures
findings: 1

- **minor** `tests/golden_queries/nvr_2026-08-01_1200-1400.md:8` — The committed fixture set's ground-truth source (security_footage_golden_truth_august_1_2026_noon_to_2pm) and the spike scorecard backing all eight strict-xfail reasons (security-footage-spike-findings.md) are both untracked, so the provenance chain of a committed test set exists only on this machine.
  - scenario: The untracked files are lost or diverge (they are unversioned working notes); a later session sees an xpass-red strict xfail, cannot consult the cited scorecard row to judge whether the model improved or the fixture was miscalibrated, and either deletes the fixture or re-derives truth from scratch. Fix: commit both text-only files (no imagery is involved) or record in the set doc where they are durably archived.

---

## Full review

Verification is complete. Everything I suspected either checked out or dissolved on inspection:

**Verified clean (not assumed):**
- All nine `source_key` sha256 values match the actual clip files at `/home/debug/nvr-clips-2026-08-01/` — I recomputed them.
- Every YAML parses and uses only fields/modalities the harness (`tests/test_golden_queries.py`) supports; the case arithmetic (25 cases = 17 pass + 8 strict-xfail) matches the claimed calibration run.
- The `security` profile really carries the documented `[person, car, truck, bicycle, package, dog, cat]` vocabulary and does **not** disable the action recognizer, so the clip00 "driving" xfail is coherent under `--profile security`.
- Human-truth quotes in the fixture comments are faithful to the owner's ground-truth file, and the xfail reasons match the spike scorecard rows they cite (#02 person×4/bicycle standalone, #14 0/37 occlusion miss, #15 person×7 standalone false positive).
- My main suspicion — that the clip00 action `no_match` xfail depends on surviving the harness's global k=20 cut across 22 near-identical clips — dissolved on measurement: only 11 "driving a car" events exist in `.va-nvr` (the abstention foil trimmed the rest) and clip00 ranks 4th by confidence, so the cut is not binding. Dissolved, not reported.
- Both findings from the previous review round (bb26bdd) are fixed in this rebuilt commit: the clip20 caption query is now stopword-free (`"child running"`, with the reasoning in a comment) and the README now documents the set-level `.md` convention.
- Vacuous-pass traps on the object `no_match` fixtures are explicitly defended with `car` positive controls (clips 01, 15). The batch-ingest YOLO-starvation foot-gun is documented in the README. No code changes, so no combination-matrix or offline-suite impact; the `.va-shots` set auto-skips as designed.

**One minor finding:** both provenance sources the committed fixtures cite — `security_footage_golden_truth_august_1_2026_noon_to_2pm` (designated in `nvr_2026-08-01_1200-1400.md` as "repo root, untracked") and `security-footage-spike-findings.md` (source of every xfail reason) — are untracked working files. The fixtures quote enough truth verbatim to run, but the audit trail behind eight strict xfails lives only on this machine. Since both are text-only (no footage/imagery), the safe path is to commit them (or an excerpt), or record where they are archived; the privacy rationale that keeps the clips out of the repo doesn't apply to text already quoted in committed fixture comments. This echoes the prior round's unresolved question and the recorded "notes files not gitignored" hygiene gap — flagging it as a finding this time since the set doc now formally designates an untracked file as the ground-truth source of a committed test set.

Not reported (checked and judged noise): the true-positive floor is quoted as ≥0.108 in clip00's comment vs ≥0.114 in the set doc — both are calibration notes safely above the 0.10 threshold.

```json
{"verdict": "approve", "findings": [
  {"severity": "minor", "file": "tests/golden_queries/nvr_2026-08-01_1200-1400.md", "line": 8, "issue": "The committed fixture set's ground-truth source (security_footage_golden_truth_august_1_2026_noon_to_2pm) and the spike scorecard backing all eight strict-xfail reasons (security-footage-spike-findings.md) are both untracked, so the provenance chain of a committed test set exists only on this machine.", "scenario": "The untracked files are lost or diverge (they are unversioned working notes); a later session sees an xpass-red strict xfail, cannot consult the cited scorecard row to judge whether the model improved or the fixture was miscalibrated, and either deletes the fixture or re-derives truth from scratch. Fix: commit both text-only files (no imagery is involved) or record in the set doc where they are durably archived."}
]}
```
