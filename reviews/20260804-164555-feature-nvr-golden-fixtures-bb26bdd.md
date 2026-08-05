# Agent review — approve

date: 2026-08-04T16:50:53.179057
range: origin/main..HEAD
branch: feature/nvr-golden-fixtures
findings: 2

- **minor** `tests/golden_queries/nvr0801_clip20_cam1.yaml:15` — Strict-xfail caption query 'a child running' contains the stopword 'a', which overlaps every caption under the token-overlap search, making the xfail outcome depend on DB insertion order under the global k=20 truncation across 22 near-identical clips.
  - scenario: Re-ingesting .va-nvr in a different order (or with a different clip subset) puts clip20's static-scene caption into the global top-20; the harness counts any in-range hit as a match, the strict xfail XPASSes red, and the alert is misread as the captioner having learned to see the running girl. Fix: phrase the query without stopwords ('child running'), which has zero token overlap with the static captions and fails deterministically.
- **minor** `tests/golden_queries/README.md:4` — The stated layout ('Each video has a human-readable <video_id>.md and a machine-readable <video_id>.yaml') is now inaccurate for the nine NVR fixtures, which share the single set doc nvr_2026-08-01_1200-1400.md.
  - scenario: A future fixture author (human or agent) follows the README layout and creates per-clip .md files, or flags the NVR set as malformed; amend the layout paragraph to permit set-level docs and cite the NVR set as the example.

---

## Full review

Review complete. This commit adds nine golden-fixture YAMLs for the 2026-08-01 NVR clips plus set-level docs — no code changes. I verified it deeply rather than taking the fixtures at face value:

**What checks out (verified, not assumed):**
- All nine `source_key` sha256 values match the actual clip files in `/home/debug/nvr-clips-2026-08-01/` (recomputed them).
- Every "human truth" quote is faithful to the owner's ground-truth file, and every `xfail` reason matches the spike scorecard row it cites (`security-footage-spike-findings.md` #00/#02/#14/#15/#16/#19/#20).
- The harness (`test_golden_queries.py`) supports every field used: `source_key` resolution via `Catalog.get_by_source_key` (local `sha256:` prefix format matches `local.py`), `modality: object/action/caption`, `verify: true`, strict `xfail`, and skip-when-not-ingested (so the `.va-shots` run is untouched, as claimed).
- The claimed calibration arithmetic is internally consistent: 25 cases, 8 xfails, matching the "17 passed / 8 xfailed" run; the "85 passed / 1 xfailed" `.va-shots` baseline matches the loop log's post-PR-#26 baseline (CLAUDE.md's "83" is stale from before, not this commit's doing).
- The vacuous-pass trap on object `no_match` fixtures is explicitly defended (clips 01/15 carry a `car` positive control) — good test integrity.
- `.gitignore`'s `.va-*/` covers the new `.va-nvr` workdir; the security profile really does carry the documented `[person, car, truck, bicycle, package, dog, cat]` vocab.

**Findings (both minor):**
1. `nvr0801_clip20_cam1.yaml:15` — the strict-xfail caption query "a child running" is order-dependent. `SegmentStore.search_captions` does bare token overlap with no stopword filtering, so the token "a" overlaps essentially every caption in a 22-clip corpus of near-identical scene captions; ~dozens of rows tie at score 1/3 and the global `k=20` truncation then picks among ties by DB insertion order. Whether clip20's static-scene caption ("A woman…") lands in the top-20 — which flips this strict xfail to a red XPASS misread as "the captioner now sees the running girl" — depends on ingest order, not model behavior. Safe path: phrase the query without stopwords ("child running" has zero overlap with the static captions → deterministic), like the already-robust "children playing outside" on clip 02.
2. `README.md:4` — "Each video has a human-readable `<video_id>.md` … and a machine-readable `<video_id>.yaml`" is now inaccurate: the nine NVR fixtures share one set-level doc. A future fixture author following the stated layout will produce inconsistent structure. Safe path: amend that paragraph to permit set-level docs and point to `nvr_2026-08-01_1200-1400.md` as the example.

**Question for the human (not a block):** the provenance source `security_footage_golden_truth_august_1_2026_noon_to_2pm` stays untracked while its content is quoted verbatim into committed fixture comments — the privacy rationale (keep footage out) doesn't obviously apply to the text file whose words are already in the repo. Committing it would make the human-verified provenance auditable in-repo; this echoes the earlier "notes files not gitignored" hygiene gap.

```json
{"verdict": "approve", "findings": [
  {"severity": "minor", "file": "tests/golden_queries/nvr0801_clip20_cam1.yaml", "line": 15, "issue": "Strict-xfail caption query 'a child running' contains the stopword 'a', which overlaps every caption under the token-overlap search, making the xfail outcome depend on DB insertion order under the global k=20 truncation across 22 near-identical clips.", "scenario": "Re-ingesting .va-nvr in a different order (or with a different clip subset) puts clip20's static-scene caption into the global top-20; the harness counts any in-range hit as a match, the strict xfail XPASSes red, and the alert is misread as the captioner having learned to see the running girl. Fix: phrase the query without stopwords ('child running'), which has zero token overlap with the static captions and fails deterministically."},
  {"severity": "minor", "file": "tests/golden_queries/README.md", "line": 4, "issue": "The stated layout ('Each video has a human-readable <video_id>.md and a machine-readable <video_id>.yaml') is now inaccurate for the nine NVR fixtures, which share the single set doc nvr_2026-08-01_1200-1400.md.", "scenario": "A future fixture author (human or agent) follows the README layout and creates per-clip .md files, or flags the NVR set as malformed; amend the layout paragraph to permit set-level docs and cite the NVR set as the example."}
]}
```
