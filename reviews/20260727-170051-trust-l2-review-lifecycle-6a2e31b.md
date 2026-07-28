# Agent review — request_changes

date: 2026-07-27T17:07:53.044168
range: origin/main..HEAD
branch: trust/l2-review-lifecycle
findings: 2

- **major** `.githooks/pre-push:17` — Gate 1's green check `grep -qE '^[0-9]+ passed'` matches pytest summaries like '33 passed, 1 error' (pytest orders 'error' after 'passed' in KNOWN_TYPES) and the pipeline exit status is never checked, so collection errors fail OPEN.
  - scenario: An agent commits a test module with an import-time error (compiles fine, so the pre-commit py_compile gate passes); pytest reports '33 passed, 1 error' with that file's tests never run, the summary line matches the regex, and the push ships with a red suite behind a green gate — no matrix row or sandbox test covers the passed-plus-error shape.
- **minor** `scripts/review_scope_hash.sh:13` — Committed reviews/*.md ledgers are both excluded from the approval hash and classified as inert docs in every docs-only exemption, so edits or deletions of the audit trail push with zero review.
  - scenario: After a lifecycle completes, an agent rewrites a shipped ledger (e.g. flips a recorded request_changes or WAIVED entry to approve) and commits it alone with a plain 'docs:' subject: commit-msg grants the docs exemption, post-commit skips, Gate 2's docs-only test skips, Gate 3 passes, and the forged audit record reaches origin detectable only by manually reading git history.

---

## Full review

Review complete. I verified the round-3 findings are all genuinely fixed at this HEAD (stdin `</dev/null` on loop children + a regression test whose fake reviewer now eats stdin; reviewer stderr moved to `.git/agent-review.err` with a test; instruction-bearing files excluded from all three docs-only exemptions with a test), so none are repeated. Several new suspicions dissolved on verification: the `set -e` + `&&` idiom in commit-msg is safe; the empty-hash constant from a missing `origin/main` can't realistically be blessed because pre-push hard-fails without the ref; the finalize amend's staged-content check correctly excludes only `reviews/*.md`; Gate 3's three-dot range can't contain foreign provisional subjects; the non-greedy JSON-block regex handles nested findings objects; matrix rows 29–35 all have matching sandbox tests. The WT.3-dependent Done-when items (in-session sentinel/waiver guards) are explicitly disclosed as pending in the plan and hook messages, so they are not silent gaps.

Two findings survived:

**1. major — `.githooks/pre-push:17`: Gate 1's green check passes when the suite has collection errors.** The check is `grep -qE '^[0-9]+ passed'` on the summary line, and pytest's summary orders parts by `KNOWN_TYPES = ("failed", "passed", "skipped", …, "error")` (verified in the installed `_pytest/terminal.py:63`), so errors are appended *after* the passed count. A run summarizing `33 passed, 1 error in 31s` therefore starts with `33 passed` and matches; the pipeline's exit status is never checked (`$(… | tail -8)` under no `set -e`). Real failures are caught (`failed` sorts first), but a test module with an import-time error — which `pre-commit`'s `py_compile` syntax gate does not catch, since the module compiles — silently drops that file's tests and pushes with a "green" gate. The regex on this line was rewritten in this range without closing the hole, and no matrix row/test covers the passed-plus-error shape. Fix: check pytest's exit status (drop the grep-on-text approach) or also reject on `[0-9]+ error`.

**2. minor — `scripts/review_scope_hash.sh:13` + `.githooks/pre-push:37`: committed review ledgers are freely rewritable after the fact with zero gates.** `reviews/*.md` is excluded from the approval hash *and* classifies as inert docs in all three docs-only exemptions. So a later commit that edits or deletes an existing ledger (e.g. flipping a recorded `request_changes` or `WAIVED` entry to `approve`) gets a plain subject at commit-msg, no post-commit review, a Gate 2 docs-only skip, and a clean push. The audit trail the design leans on ("every use is recorded so waived pushes stay visible") is only tamper-evident via raw git history, not protected by any of the new gates. A cheap tightening: treat modifications/deletions of *existing tracked* `reviews/*.md` files as non-exempt (only ledger additions are review artifacts).

```json
{"verdict": "request_changes", "findings": [
{"severity": "major", "file": ".githooks/pre-push", "line": 17, "issue": "Gate 1's green check `grep -qE '^[0-9]+ passed'` matches pytest summaries like '33 passed, 1 error' (pytest orders 'error' after 'passed' in KNOWN_TYPES) and the pipeline exit status is never checked, so collection errors fail OPEN.", "scenario": "An agent commits a test module with an import-time error (compiles fine, so the pre-commit py_compile gate passes); pytest reports '33 passed, 1 error' with that file's tests never run, the summary line matches the regex, and the push ships with a red suite behind a green gate — no matrix row or sandbox test covers the passed-plus-error shape."},
{"severity": "minor", "file": "scripts/review_scope_hash.sh", "line": 13, "issue": "Committed reviews/*.md ledgers are both excluded from the approval hash and classified as inert docs in every docs-only exemption, so edits or deletions of the audit trail push with zero review.", "scenario": "After a lifecycle completes, an agent rewrites a shipped ledger (e.g. flips a recorded request_changes or WAIVED entry to approve) and commits it alone with a plain 'docs:' subject: commit-msg grants the docs exemption, post-commit skips, Gate 2's docs-only test skips, Gate 3 passes, and the forged audit record reaches origin detectable only by manually reading git history."}
]}
```
