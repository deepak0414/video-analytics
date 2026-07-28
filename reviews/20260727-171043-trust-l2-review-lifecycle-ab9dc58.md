# Agent review — approve

date: 2026-07-27T17:16:30.737727
range: origin/main..HEAD
branch: trust/l2-review-lifecycle
findings: 1

- **minor** `workflow-trust-plan.md:414` — WT.3's planned bash_guard RULES (extended in this diff) guard every override token except the new .git/.review-approved approval file, so agent self-blessing of the pre-push backstop stays mechanically unblocked even after WT.3 lands; relatedly, ADDING a fabricated reviews/*.md 'approve' ledger passes all gates since only M/D/R are blocked.
  - scenario: An agent commits unreviewed code as 'wip:', runs 'scripts/review_scope_hash.sh > .git/.review-approved', and pushes: Gate 2 sees a matching hash, prints 'skipping re-review', and the content ships with no review; once WT.3's session guards land, no RULES pattern flags the command, unlike the equivalent .commit-approved and AGENT_REVIEW=skip paths.

---

## Full review

Review complete. This range (three commits: review scripts + reviewer subagent, docs/lifecycle conventions, lifecycle enforcement + sandbox tests) is the fifth review round for this branch, and I verified both round-4 findings are genuinely fixed at this HEAD: Gate 1 now judges the suite by pytest's exit code (with the `set_suite_raw("error")` regression test simulating "33 passed, 1 error"), and shipped ledgers are append-only at both pre-commit (`--diff-filter=MDR`) and pre-push Gate 2b, with the `ALLOW_LEDGER_EDIT=1` human override tested on both the honest and `--no-verify` paths.

Several new suspicions dissolved on verification:

- `.commit-approved`/`.guard-override` are already gitignored on the committed baseline (WT.1 PR), so the sentinel can't be accidentally committed by `git add -A`.
- The commit-msg finalize check runs while HEAD is still the provisional commit, so `review_scope_hash.sh` (default HEAD) and `git diff --cached` both measure exactly the approved scope during an amend; `git commit -a` is also safe because git points hooks at the temporary index via `GIT_INDEX_FILE`.
- The `set -e` + `cmd && exit 0` idiom in commit-msg, the empty-`$files` case (fails closed), the three-dot ranges in Gate 2/3, rename handling in the append-only gates (undetected renames surface as D), and the oversized-`RAW` env-var edge in `agent-review.sh` (exec failure → nonzero → fail-closed) all check out.
- The pre-push comment "every command in this loop gets `</dev/null`" is literally inaccurate (only pytest and `agent-review.sh` get it), but no other command in the loop reads stdin, so there is no behavioral fail-open — comment-only, not reported as a finding.
- WT.4's "Done when" items (a)–(e) each map to a sandbox test; matrix rows 36–37 have matching tests; the test edits in this diff only add the now-required `wip:` subjects and strengthen the old summary-grep test — nothing weakened or deleted.

One minor finding survived, in the same audit-trail class the last round worked in: this diff introduces `.git/.review-approved` as the token that makes pre-push skip the backstop review, and it extends WT.3's planned `RULES` guard list (adding `ALLOW_LEDGER_EDIT`), yet no rule covers writing that file — so even after WT.3's session guards land, `scripts/review_scope_hash.sh > .git/.review-approved` remains a mechanical self-blessing path (and, relatedly, *adding* a fabricated `reviews/*.md` "approve" ledger passes every gate, since only modification/deletion is blocked). Today this is admittedly convention-guarded like all overrides (disclosed as pending WT.3), which is why it's minor and not blocking — but the RULES list is the mechanism meant to close exactly this class, and it should name the approval file.

Verdict: **approve** — no critical or major findings.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "workflow-trust-plan.md", "line": 414, "issue": "WT.3's planned bash_guard RULES (extended in this diff) guard every override token except the new .git/.review-approved approval file, so agent self-blessing of the pre-push backstop stays mechanically unblocked even after WT.3 lands; relatedly, ADDING a fabricated reviews/*.md 'approve' ledger passes all gates since only M/D/R are blocked.", "scenario": "An agent commits unreviewed code as 'wip:', runs 'scripts/review_scope_hash.sh > .git/.review-approved', and pushes: Gate 2 sees a matching hash, prints 'skipping re-review', and the content ships with no review; once WT.3's session guards land, no RULES pattern flags the command, unlike the equivalent .commit-approved and AGENT_REVIEW=skip paths."}]}
```
