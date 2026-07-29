# Agent review — approve

date: 2026-07-29T14:44:14.710542
range: origin/main..HEAD
branch: trust/role-instructions
findings: 1

- **minor** `workflow-trust-plan.md:2406` — RI.3's 'Done when' (CLAUDE.md shrinks per the <200-line doctrine) is unmet in this range and nothing records it as deliberately deferred — a repeat of round 1's third finding, still unaddressed.
  - scenario: The branch merges as WT.11's closing PR, the plan gets marked delivered, and RI.3 is silently dropped; CLAUDE.md stays at 322 lines and the pruning doctrine's warning about a skimmed-then-ignored advisory layer comes true.

---

## Full review

Review complete. This is round 2 on the WT.11 branch: the range delivers the single-sourced reviewer rubric (RI.1), the `/task-commit` committer procedure (RI.2), the rubric upgrades items 6–8 (RI.4), the RI.5 deferral rows, and the plan/matrix updates — plus fixes for round 1's three minor findings.

**Round-1 fixes verified:**

- **Matrix row collision (round-1 #1): fixed.** The new rows are 106–108, appended after the pre-existing 105, and all four cross-references were updated (`scripts/agent-review.sh:7`, the test docstring at `tests/test_trust_hooks.py:468`, and the RI.0/RI.1/RI.2/RI.6 cards). The "Matrix row 50" at `tests/test_trust_hooks.py:620` is the genuine pre-existing row 50, not a stale reference.
- **Waiver-before-print ordering (round-1 #2): fixed.** `scripts/agent-review.sh:34` now gates the `AGENT_REVIEW=skip` waiver on `print_only=0`, so `--print-prompt` with a lingering env var can no longer write a spurious WAIVED ledger — and the drift test explicitly exercises this (`env={"AGENT_REVIEW": "skip"}` plus a ledger-count assertion), so the regression is pinned, not just patched.
- **RI.3 growth (round-1 #3): half-addressed.** CLAUDE.md is back to net-zero (322 lines on both origin/main and HEAD; round 1 saw 325), but RI.3's "Done when" (shrink per the <200-line doctrine) remains unmet and nothing in the range states it is deferred to a later commit on this branch. Kept as a minor below.

**What I verified fresh:**

- The awk frontmatter strip at `scripts/agent-review.sh:51` is correct: nothing prints until after the second `---`, delimiter lines are excluded, later `---` lines in the body are unaffected, and both a missing file and the degenerate single-`---` file yield an empty rubric that hits the fail-closed exit. The fail-closed test's `"fail-closed"` stderr assertion matches the script's message.
- The drift test would fail against the old code (hardcoded prompt → no marker in output), so it genuinely reproduces the failure it guards against.
- Both hook call sites (`​.githooks/post-commit:31`, `.githooks/pre-push:54`) pass the range as the first positional argument; the optional `--print-prompt` flag only shifts when present, so the enforced paths are untouched.
- Moving the rubric into `.claude/agents/` loses no protection: `path_guard.py` covers `.claude/`, and `scripts/critical_paths.txt` lists `.claude/` under `human-reviewed`, so the CI critical-paths gate still fires on rubric edits — that suspicion dissolved.
- The sandbox fixture copies the agent file before the seed commit and before hooks activate, so all pre-existing lifecycle tests that route through `agent-review.sh` keep working against the fail-closed check.
- The prompt I am running under is itself the assembled output of the new mechanism (rubric + scope override + JSON appendix), which is live confirmation of the git-range path.
- Commit hygiene: `2780ec2` is a self-sufficient docs-only subject; HEAD's `need_agent_review:` subject is exempt under item 8. Combination coverage: trust machinery only — no roles×backends×profiles cell is affected, and the offline suite is the right (and only applicable) harness.
- Caveat for the digest: pytest execution was denied in this reviewer session (same as round 1), so my verification of the two new tests is static. The digest's evidence section must carry a real run's counts.

One finding, minor, so the verdict is approve.

**Findings:**

1. **minor** — `workflow-trust-plan.md:2406` — RI.3's "Done when" (CLAUDE.md shrinks per the <200-line doctrine) is unmet in this range (322 → 322 lines) and neither the plan card nor anything in the range records that RI.3 is deliberately a later commit; the WT.11 status line's "in progress" predates round 1 and doesn't name which cards remain. Scenario: the branch merges as "the arc's closing PR", WT.11 gets marked delivered, and RI.3 silently joins the bloat the pruning doctrine warns disables the advisory layer. Safe path: either land RI.3 in this branch or add a one-line as-built note on the RI.3 card ("deferred to follow-up commit/PR") and say so in the digest.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "workflow-trust-plan.md", "line": 2406, "issue": "RI.3's 'Done when' (CLAUDE.md shrinks per the <200-line doctrine) is unmet in this range and nothing records it as deliberately deferred — a repeat of round 1's third finding, still unaddressed.", "scenario": "The branch merges as WT.11's closing PR, the plan gets marked delivered, and RI.3 is silently dropped; CLAUDE.md stays at 322 lines and the pruning doctrine's warning about a skimmed-then-ignored advisory layer comes true."}]}
```
