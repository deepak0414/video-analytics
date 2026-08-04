# Agent review — request_changes

date: 2026-08-04T14:52:24.727782
range: origin/main..HEAD
branch: feature/nvr-golden-fixtures
findings: 2

- **major** `tests/golden_queries/nvr_2026-08-01_1200-1400.md:65` — Claimed calibration run (16 passed + 8 xfailed = 24 cases, '24 NVR cases auto-skipping') does not match the 25 query cases shipped (17 non-xfail + 8 xfail) — one non-xfail assertion was never executed.
  - scenario: The next RUN_GOLDEN=1 GOLDEN_WORKDIR=.va-nvr run exercises the never-validated case and fails (or passes for an unmeasured reason), and the doc's calibration evidence cannot be reproduced from the shipped fixture set; re-run against HEAD and update the counts, or remove the unrun query until measured.
- **minor** `tests/golden_queries/README.md:69` — The canonical 'Provenance labels' section omits 'human-verified', the label used by all 25 new queries (it is defined only in the ask_questions paragraph).
  - scenario: A future fixture author or generator workflow consults the label enum, sees only vision-verified/model-regression, and either mislabels owner-verified truth or treats the NVR fixtures' label as invalid; add human-verified to the list with its meaning for queries: blocks.

---

## Full review

The dup-check hit is just the `video_id:` lines truncated at the underscore — all 25 query IDs are unique. My review is complete.

## Review summary

This commit ships 9 golden-fixture YAMLs for the 2026-08-01 NVR clips plus two docs. The fixture design is solid: xfails are strict and sourced from the spike scorecard, no_match assertions carry positive controls against the vacuous-pass mode (detector silently dead), source keys match the `sha256:<hex>` format `LocalSource` actually produces, all asserted object classes are inside the security profile's narrowed vocabulary, and the harness (`test_golden_queries.py`, unchanged) supports every field used (`modality`, `verify`, `xfail`, `provenance`, `default_min_score`). The apparent 83-vs-85 `.va-shots` count discrepancy dissolves: 85 = 83 query cases + 2 ask cases under `-m golden`.

One finding survives verification:

**Major — run evidence doesn't cover the shipped fixture set.** The fixtures define **25** query cases (17 expected-pass + 8 xfail; counted per file: 5+3+4+2+1+2+3+3+2). The calibration section claims "First full run 2026-08-04: **16 passed / 8 xfailed / 0 failed**" and, consistently, "the **24** NVR cases auto-skipping" in the `.va-shots` run (`nvr_2026-08-01_1200-1400.md:65-66`). Both statements describe a 24-case set, so exactly one non-xfail assertion was added *after* the recorded run and has never been executed — no skip pattern explains it (no single clip's absence removes exactly one non-xfail case while keeping all 8 xfails). The most plausible candidate is a late-added anchor like `nvr15-obj-02`, but nothing in the diff identifies it. In a repo whose evidence gate exists precisely to keep "tests pass" claims tied to real pytest counts, a golden-fixture commit whose own calibration numbers can't be reproduced from the shipped set is the exact failure class under review. Safe path: re-run the `.va-nvr` harness against HEAD (expected 17 passed / 8 xfailed / 25 total), update the numbers, or drop the unmeasured query until it's been run.

**Minor — provenance label enum not updated.** Every new query uses `provenance: human-verified`, but the README's canonical "Provenance labels" section (`README.md:69-74`) still lists only `vision-verified` and `model-regression`; `human-verified` is defined only inside the `ask_questions` paragraph. A future fixture author reading the enum won't know it's valid for `queries:` blocks. Safe path: add it to the label list with its meaning (owner-written ground truth).

Verified-and-dissolved (not findings): action/object/caption no_match semantics all hold for these fixtures; the YOLO batch-ingest starvation foot-gun *is* documented in the same change; keeping the raw ground-truth file and clips untracked is an explicit, privacy-justified decision recorded in the set doc, with the relevant truth quoted inline per fixture; the provisional `need_agent_review:` subject is exempt from the commit-message rule.

```json
{"verdict": "request_changes", "findings": [{"severity": "major", "file": "tests/golden_queries/nvr_2026-08-01_1200-1400.md", "line": 65, "issue": "Claimed calibration run (16 passed + 8 xfailed = 24 cases, '24 NVR cases auto-skipping') does not match the 25 query cases shipped (17 non-xfail + 8 xfail) — one non-xfail assertion was never executed.", "scenario": "The next RUN_GOLDEN=1 GOLDEN_WORKDIR=.va-nvr run exercises the never-validated case and fails (or passes for an unmeasured reason), and the doc's calibration evidence cannot be reproduced from the shipped fixture set; re-run against HEAD and update the counts, or remove the unrun query until measured."}, {"severity": "minor", "file": "tests/golden_queries/README.md", "line": 69, "issue": "The canonical 'Provenance labels' section omits 'human-verified', the label used by all 25 new queries (it is defined only in the ask_questions paragraph).", "scenario": "A future fixture author or generator workflow consults the label enum, sees only vision-verified/model-regression, and either mislabels owner-verified truth or treats the NVR fixtures' label as invalid; add human-verified to the list with its meaning for queries: blocks."}]}
```
