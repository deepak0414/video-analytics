# Agent review — approve

date: 2026-07-30T22:15:58.122428
range: origin/main..HEAD
branch: feature/batch-reprocess
findings: 1

- **minor** `tests/test_reprocess.py:63` — The --role + --video combined scope of plan_reprocess has no test — role and video filters are each tested only in isolation, though the plan's X-2 'dry-run lists exactly the stale set' implies selection coverage.
  - scenario: A future refactor reorders the role filter and the video filter (e.g. filters by video before role-scoping the report) so `va reprocess --video X --role ocr` returns X's full stale_roles instead of the ocr-only subset; every existing test stays green and the executor built on this plan later re-runs roles that are current. Safe path: add a test asserting a video stale on role A but current on role B yields an empty plan for role=B and a single-role plan for role=A.

---

## Full review

Review complete. The range contains one commit (`6cecead`, provisional `need_agent_review:` subject — exempt from the finalized-message rule), adding the read-only RPRC-3a dry-run selection front-end: `pipeline/reprocess.py::plan_reprocess`, the `va reprocess` CLI, tests, and doc entries in CLAUDE.md, COORDINATION.md, and the plan.

What I verified:

- **Scope-XOR logic** (`all_stale == bool(video)` at `reprocess.py:38`): truth-tabled all four cases — correct, including the `video=""` edge (raises).
- **API contracts**: `stale_report` really returns `recorded_fps` and raises `ValueError` on unknown roles (`stale.py:28-33`); `ProvenanceStore.record(..., fps=)`, `Catalog.set_status`, `lookup_video`, and `write_color_video` signatures all match how the new code and tests call them. `reasoner` is genuinely absent from `PROVENANCE_ROLES`, so `test_unknown_role_raises` tests what it claims.
- **The non-done guard** (`reprocess.py:55-62`) correctly prevents an incomplete ingest from reading as "already current" — a real failure mode of the underlying done-filtered report, and it has a dedicated test.
- **The refusal path**: no `--dry-run` prints the plan then returns rc=1 with a "NO changes made" message — never a silent no-op — and the pointer to `va reingest <video> --fps <N>` references a flag that exists (`cli.py:492`).
- **Docs parity**: the new command is in CLAUDE.md, the read-only contract (plus a heads-up about the future `observations` purge) is logged in COORDINATION.md, and the plan status entry accurately narrows scope to RPRC-3a with RPRC-1/2 named as next. No schema or shared-contract change. The `_active_config_line` extraction is a pure refactor of the existing `va stale` header (behavior identical).
- Test execution was blocked by session permissions, so verification of the 10 new tests is static only; they exercise every branch of `plan_reprocess` except one (below).

One minor gap: the `--role` + `--video` flag combination (e.g. `va reprocess --video X --role ocr`) is the only selection combination with no test — role-scoping and video-scoping are each tested alone, but their intersection (a video stale on other roles but current on the requested one must yield an empty plan) is exactly the kind of selection correctness RPRC-3 exists for and the plan's X-2 line ("`--dry-run` lists exactly the stale set") implies coverage. One small test closes it. Nothing rises to major; the suspicious spots I chased (unstamped-role staleness in the fixtures, argparse mutual exclusion, fps propagation) all dissolved on reading the supporting code.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "tests/test_reprocess.py", "line": 63, "issue": "The --role + --video combined scope of plan_reprocess has no test — role and video filters are each tested only in isolation, though the plan's X-2 'dry-run lists exactly the stale set' implies selection coverage.", "scenario": "A future refactor reorders the role filter and the video filter (e.g. filters by video before role-scoping the report) so `va reprocess --video X --role ocr` returns X's full stale_roles instead of the ocr-only subset; every existing test stays green and the executor built on this plan later re-runs roles that are current. Safe path: add a test asserting a video stale on role A but current on role B yields an empty plan for role=B and a single-role plan for role=A."}]}
```
