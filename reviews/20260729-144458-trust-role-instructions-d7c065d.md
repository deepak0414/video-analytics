# Agent review — approve

date: 2026-07-29T14:49:27.527870
range: origin/main..HEAD
branch: trust/role-instructions
findings: 2

- **minor** `COORDINATION.md:257` — WT.11 changes both agents' commit procedure (mandatory /task-commit, four-section digest, rubric relocation) but adds no coordination-log entry, breaking the convention every prior trust landing followed.
  - scenario: The other agent's session merges this and keeps committing by the old habit — digests arrive without the mandatory sections and the designated cross-session broadcast channel never announced the change.
- **minor** `workflow-trust-plan.md:2553` — Validation-matrix rows 107 and 108 say '(observed in first live /task-commit use)' although that observation has not yet occurred, in a table whose rows otherwise record executed results.
  - scenario: A future reader auditing the rollout's 'Done when' takes rows 107/108 as completed validation and skips ever confirming the digest-completeness and shorthand-flagging behaviors live.

---

## Full review

## Review: origin/main..HEAD (2 commits — WT.11 role instructions)

The core mechanism is sound and I verified it the strongest way possible: **this review session is itself running on the assembled prompt** — rubric body extracted from `.claude/agents/code-reviewer.md`, scope-override line, JSON appendix — so the single-sourcing demonstrably works end-to-end on the enforced headless path.

**Suspicions I chased and dropped:**

- *Rubric moved out of critical-path protection?* No — `scripts/critical_paths.txt` lists `.claude/` under `human-reviewed`, so the new rubric home is still gated. Dissolved.
- *awk frontmatter extraction fragile?* The agent file has exactly two `^---$` lines (1 and 5); `f{print}` runs before the counter increments, so the second delimiter is correctly excluded and the full body ships. The empty-rubric fail-closed guard covers the file-moved/malformed cases, and `test_missing_rubric_fails_closed` proves it. Dissolved.
- *Print mode writing a spurious WAIVED ledger with a lingering `AGENT_REVIEW=skip`?* Fixed (the `print_only = 0` conjunct at `scripts/agent-review.sh:34`) and pinned by the drift-canary test, which runs with the waiver var deliberately set. Dissolved.
- *RI.3 "net CLAUDE.md size does not grow"?* Verified: 322 lines on origin/main, 322 lines here. Dissolved.
- *Sandbox parity:* the fixture now copies and commits the agent file, so all pre-existing lifecycle tests exercise the new assembly path too — a broken extraction would turn the whole trust suite red, not pass silently. Good property.
- *Test env handling:* `TrustRepo.run()` merges onto `os.environ`, so the `env={"AGENT_REVIEW": "skip"}` override composes with PATH/fakes correctly. Dissolved.

**Two minor findings:**

1. **COORDINATION.md is silent on a change that alters both agents' commit procedure.** Every prior trust landing (WT.4 lifecycle, WT.3 guards, WT.5–7 CI gates) shipped an append-only log entry flagged "affects BOTH agents"; this branch makes `/task-commit` the committer's mandatory procedure with a four-section digest contract and relocates the rubric, but adds no entry. A concurrent session that merges this and keeps working from stale habits produces digests missing the mandatory sections. Safe path: add a `2026-07-29 (trust)` entry in this same landing PR.

2. **Matrix rows 107/108 record "(observed in first live `/task-commit` use)" before the observation exists** (`workflow-trust-plan.md:2553-2554`). The §8 matrix is explicitly the rollout's "Done when", and its other rows record executed results; these two read as completed validation for events that haven't happened (and row 108 only ever fires if a committer actually writes a shorthand-dependent message). Safe path: phrase them as "pending first live use" and flip them to observed-fact wording when RI.6 actually completes.

Neither rises to major: the CLAUDE.md update reaches every session at start, and the matrix phrasing is a plan-doc accuracy issue, not a gate weakness.

**Verdict: approve.**

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "COORDINATION.md", "line": 257, "issue": "WT.11 changes both agents' commit procedure (mandatory /task-commit, four-section digest, rubric relocation) but adds no coordination-log entry, breaking the convention every prior trust landing followed.", "scenario": "The other agent's session merges this and keeps committing by the old habit — digests arrive without the mandatory sections and the designated cross-session broadcast channel never announced the change."}, {"severity": "minor", "file": "workflow-trust-plan.md", "line": 2553, "issue": "Validation-matrix rows 107 and 108 say '(observed in first live /task-commit use)' although that observation has not yet occurred, in a table whose rows otherwise record executed results.", "scenario": "A future reader auditing the rollout's 'Done when' takes rows 107/108 as completed validation and skips ever confirming the digest-completeness and shorthand-flagging behaviors live."}]}
```
