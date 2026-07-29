# Agent review — approve

date: 2026-07-29T14:38:05.583602
range: origin/main..HEAD
branch: trust/role-instructions
findings: 3

- **minor** `workflow-trust-plan.md:2488` — New validation-matrix rows numbered 50-52 duplicate the pre-existing rows 50-52 (table already extends past 105), making every WT.11 row reference ambiguous.
  - scenario: A rollout audit or RI.6 sign-off follows 'rows 50-52' (also cited in agent-review.sh:7 and the test docstring) and ticks the force-push/git-config/roles.yaml rows instead of the WT.11 rows, so the drift-test validation is never actually confirmed.
- **minor** `scripts/agent-review.sh:33` — --print-prompt is evaluated after the AGENT_REVIEW=skip waiver check, so print mode with that env var set writes a spurious WAIVED ledger and emits no prompt.
  - scenario: A shell with AGENT_REVIEW=skip lingering runs the drift check: reviews/ gains a 'Review WAIVED by user' audit entry for a review that was never requested, and the drift test fails confusingly because stdout is empty.
- **minor** `CLAUDE.md:1` — RI.3's 'Done when' requires CLAUDE.md to shrink (<200-line doctrine), but this range grows it from 322 to 325 lines with no statement that RI.3 is deferred to a later commit.
  - scenario: The WT.11 'closing PR' merges with RI.3 silently unmet; CLAUDE.md keeps bloating and the plan's own pruning doctrine records the card as delivered when it was not.

---

## Full review

Review complete. This range delivers WT.11's RI.1/RI.2/RI.4/RI.5 pieces: the reviewer rubric moves into `.claude/agents/code-reviewer.md` as the single source, `agent-review.sh` assembles its headless prompt from that file with a fail-closed empty-rubric check and a `--print-prompt` drift-test mode, `/task-commit` codifies the committer procedure, and two sandbox tests cover the drift and fail-closed behaviors.

**What I verified:**

- The awk frontmatter-strip (`agent-review.sh:50`) is correct: nothing prints until after the second `---`, the delimiter lines themselves are excluded, and a body containing later `---` lines is unaffected. The fail-closed branch catches both a missing file (awk error → empty substitution) and the degenerate single-`---` file the new test writes.
- Both new tests trace correctly against the fixture: the sandbox now commits the agent file to its seeded main (parity comment is accurate), the marker lands after the second `---` so it must appear in `--print-prompt` output, and the fail-closed test's `"fail-closed"` stderr assertion matches the script's message. Note: I could not execute pytest in this reviewer session (command approval denied), so test verification is static, not a live run — the digest's evidence section should carry the real counts.
- The prompt I am running under is itself the assembled output of the new mechanism (agent-file rubric + scope override + JSON appendix), which is live confirmation the git-range path works.
- The dispute channel now correctly points at workflow-trust-plan.md instead of the unwritable `reviews/` dir, in both the rubric and `/task-commit` step 5 — the fix owed since dropped PR 6 is in.
- Moving the rubric into `.claude/agents/` does not weaken protection: `path_guard.py:38` covers all of `.claude/`, equivalent to the old home in the trust script. The pre-push hook's positional invocation still works since the optional flag only shifts when present.
- Commit hygiene: `2780ec2` is genuinely docs-only with a self-sufficient plain subject; HEAD's provisional subject is exempt under rubric item 8.

**Findings (all minor):**

1. **workflow-trust-plan.md:2488** — the three new validation-matrix rows were inserted as 50/51/52, but the table already had rows 50–52 (and runs past 105). Every WT.11 cross-reference — RI.0/RI.1/RI.6's "rows 50–52", the script comment "matrix row 50" at `agent-review.sh:7`, and the test docstring at `test_trust_hooks.py:305` — now ambiguously names two different rows. Safe path: renumber the new rows to the next free numbers (106+) and update the four references.
2. **scripts/agent-review.sh:33** — the `AGENT_REVIEW=skip` waiver check runs before the `--print-prompt` early-exit, so print mode with that env var lingering writes a spurious "Review WAIVED" ledger into `reviews/` (an audit-trail entry for a review that was never requested) and prints nothing. Safe path: hoist the `print_only` exit above the waiver/ledger block — print mode should be side-effect-free.
3. **Plan conformance, RI.3** — its "Done when" says CLAUDE.md shrinks toward the <200-line doctrine, but this range grows it 322 → 325 lines. Fine if RI.3 is deliberately a later commit on this branch, but the digest should state that explicitly so the human isn't left assuming WT.11's closing PR is complete.

No critical or major findings, so the verdict is approve.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "workflow-trust-plan.md", "line": 2488, "issue": "New validation-matrix rows numbered 50-52 duplicate the pre-existing rows 50-52 (table already extends past 105), making every WT.11 row reference ambiguous.", "scenario": "A rollout audit or RI.6 sign-off follows 'rows 50-52' (also cited in agent-review.sh:7 and the test docstring) and ticks the force-push/git-config/roles.yaml rows instead of the WT.11 rows, so the drift-test validation is never actually confirmed."}, {"severity": "minor", "file": "scripts/agent-review.sh", "line": 33, "issue": "--print-prompt is evaluated after the AGENT_REVIEW=skip waiver check, so print mode with that env var set writes a spurious WAIVED ledger and emits no prompt.", "scenario": "A shell with AGENT_REVIEW=skip lingering runs the drift check: reviews/ gains a 'Review WAIVED by user' audit entry for a review that was never requested, and the drift test fails confusingly because stdout is empty."}, {"severity": "minor", "file": "CLAUDE.md", "line": 1, "issue": "RI.3's 'Done when' requires CLAUDE.md to shrink (<200-line doctrine), but this range grows it from 322 to 325 lines with no statement that RI.3 is deferred to a later commit.", "scenario": "The WT.11 'closing PR' merges with RI.3 silently unmet; CLAUDE.md keeps bloating and the plan's own pruning doctrine records the card as delivered when it was not."}]}
```
