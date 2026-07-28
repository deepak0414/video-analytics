# Agent review — request_changes

date: 2026-07-27T23:44:39.090726
range: origin/main..HEAD
branch: trust/l0-session-guards
findings: 4

- **major** `.claude/hooks/bash_guard.py:146` — write_targets() takes the first non-ENV token as the command name, so a write verb wrapped in a subshell/brace group (`( … )` / `{ …; }`) is mis-identified as the grouping token and its operands are never checked, leaving protected artifacts writable from Bash.
  - scenario: `( touch .commit-approved )` and `{ cp /tmp/h .git/.review-approved; }` exit 0 from the committed guard (bare `touch .commit-approved` is blocked). Chained: `( scripts/review_scope_hash.sh <sha> ... )` self-writes .git/.review-approved and `( touch .commit-approved )` forges the human sentinel, so pre-push:51 skips the review backstop and an agent pushes unreviewed work; .github/workflows/ (CI) does not exist yet.
- **major** `.claude/hooks/bash_guard.py:133` — The redirect-operator set (">",">>","1>","2>","&>") omits other valid write-redirect operators that shlex emits as single tokens (`>|`, `>&`, `&>>`, `2>|`), so a redirection into a protected artifact is not detected.
  - scenario: `echo forged >| .commit-approved` and `scripts/review_scope_hash.sh <sha> >& .git/.review-approved` tokenize with `>|`/`>&` as one token not in the set; write_targets returns [] and the guard exits 0, forging the sentinel / approval hash and enabling the same self-approval-and-push chain as finding 1.
- **minor** `tests/test_trust_guards.py:38` — BLOCKED_COMMANDS contains no subshell-wrapped write and no alternate-redirect (>|/>&/&>>) case, so the suite stays green across the entire grouping/redirect bypass class.
  - scenario: A 148-passing guard suite certifies a guard that allows `( touch .commit-approved )` and `echo x >| .git/.review-approved`; the tables again encode the rules' shape rather than the invariant the plan claims was corrected.
- **minor** `workflow-trust-plan.md:1615` — Validation-matrix rows 48/60/61 record protected-artifact writes and redirection targets as blocked/gated, but neither holds when the write verb is inside `( … )`/`{ …; }` or uses a `>|`/`>&`/`&>>` redirect.
  - scenario: A reader of the matrix concludes sentinel forging and approval-hash writes are covered and does not re-test; the guard silently permits both whenever the write is grouped in a subshell or uses a non-enumerated write-redirect operator.

---

## Full review

I have enough to finalize. I traced two distinct bypasses in the committed `write_targets` (confirming the tokenization empirically and the self-approval chain by reading the hooks), verified the fix for the prior ledger's critical holds, and checked the plan's matrix claims.

The prior ledger's critical (regex separator splitting) is genuinely fixed — the guard now tokenizes with `shlex`. But the new tokenizing `write_targets` has two independent holes in the protected-artifact write guard (rule 2), both defeating the exact verb/path combinations the guard enumerates.

## Review: `origin/main..HEAD` — WT.3 session guards

**Root issue: `write_targets()` still fails to see a protected-file write in two ordinary shell forms**, so rule 2 (block writes to `.commit-approved`, `.git/.review-approved`, `reviews/`, machinery) never fires. Both chain to the top-severity outcome for this trust system: `pre-push` (`.githooks/pre-push:51`) skips the review backstop when `.git/.review-approved` matches `scripts/review_scope_hash.sh <sha>`, and `.github/workflows/` does not exist yet — so bash_guard is the sole barrier against an agent blessing and pushing its own unreviewed work.

1. **Subshell / brace grouping.** The command-name detector (`bash_guard.py:146-152`) takes the first non-`ENV=` token as the command name. When a segment is wrapped in `( … )` or `{ …; }`, shlex emits the grouping char as its own token, so the "name" is mis-read as `(` / `{`, and write-command operands are never checked. I confirmed the tokenization: `( touch .commit-approved )` → `['(', 'touch', '.commit-approved', ')']`; name resolves to `(`, `WRITE_CMDS` check misses, `write_targets` returns `[]`, guard exits 0. `( cp /tmp/hash .git/.review-approved )` and `{ touch .commit-approved; }` bypass identically, while the bare forms are correctly blocked.

2. **Non-enumerated write-redirect operators.** The redirect set (`bash_guard.py:133,141`) is `(">", ">>", "1>", "2>", "&>")`, but shlex (with `punctuation_chars`) produces other valid *write* operators as single tokens not in that set: `>|` (clobber), `>&`, `&>>`, `2>|`. Verified: `echo x >| .commit-approved` → `['echo','x','>|','.commit-approved']`; `>|` isn't recognized as a redirect, so `.commit-approved` is never collected and the guard exits 0. (Aside: the literal `"1>"`/`"2>"` entries are dead — shlex splits the fd digit off, e.g. `2>` → `['2','>']` — the bare `>` is what actually catches those.)

Both are the same shape as the prior ledger's critical (a rule enumerates exact verbs/paths, defeated by an incidental shell construct), differing only in that they require deliberate syntax rather than firing by accident — which is why I rate them major, not critical. The design docstring's concession ("hardening, not proof; CI is the un-bypassable layer") applies, but these defeat the operand/redirect paths the validation matrix affirmatively marks blocked, and no CI layer exists today.

**Test integrity.** No test was deleted or weakened; the suite is substantial (148 pass). But `BLOCKED_COMMANDS` (`tests/test_trust_guards.py:38`) has no subshell-wrapped write and no alternate-redirect (`>|`/`>&`/`&>>`) case — the only `&` in a blocked entry is a quoted separator, and every redirect case uses `>`/`>>`. So the suite stays green over exactly this bypass class, repeating the "tables mirror the rules, not the invariant" pattern the earlier rounds flagged.

**Plan gap.** Matrix rows 48 and 60 (`workflow-trust-plan.md:1615,1627`) assert "one WRITE_VERBS list guards every protected artifact" and "operand-form write targets … parsed explicitly," and row 61 says "redirection targets are gated." None hold for a write verb inside `( … )` or a `>|`/`>&` redirect, so those "Done when" rows are asserted rather than met.

Everything else I attacked held: the `-n`/`--no-verify` tokenizer (value-flag consumption, clustered/reordered forms), `git -c/-C` prefix insertion, quoted/`+refspec`/`HEAD`/`@`/`refs/heads` force-push forms, `--mirror`/`--all`/`:main`/`--delete`, the pre-push `refs/heads/main` deletion guard, the quoted-separator segmentation (prior critical — fixed), path_guard's override scoping and `.git/config`+`.git/hooks/` coverage, and the stop-gate cache key (HEAD + tracked diff + untracked content). Out of committed scope but still true on this machine: `.guard-override` is present in the working tree, so MAINTENANCE rules are dropped every session until the human removes it.

```json
{"verdict": "request_changes", "findings": [
{"severity": "major", "file": ".claude/hooks/bash_guard.py", "line": 146, "issue": "write_targets() takes the first non-ENV token as the command name, so a write verb wrapped in a subshell/brace group (`( … )` / `{ …; }`) is mis-identified as the grouping token and its operands are never checked, leaving protected artifacts writable from Bash.", "scenario": "`( touch .commit-approved )` and `{ cp /tmp/h .git/.review-approved; }` exit 0 from the committed guard (bare `touch .commit-approved` is blocked). Chained: `( scripts/review_scope_hash.sh <sha> ... )` self-writes .git/.review-approved and `( touch .commit-approved )` forges the human sentinel, so pre-push:51 skips the review backstop and an agent pushes unreviewed work; .github/workflows/ (CI) does not exist yet."},
{"severity": "major", "file": ".claude/hooks/bash_guard.py", "line": 133, "issue": "The redirect-operator set (\">\",\">>\",\"1>\",\"2>\",\"&>\") omits other valid write-redirect operators that shlex emits as single tokens (`>|`, `>&`, `&>>`, `2>|`), so a redirection into a protected artifact is not detected.", "scenario": "`echo forged >| .commit-approved` and `scripts/review_scope_hash.sh <sha> >& .git/.review-approved` tokenize with `>|`/`>&` as one token not in the set; write_targets returns [] and the guard exits 0, forging the sentinel / approval hash and enabling the same self-approval-and-push chain as finding 1."},
{"severity": "minor", "file": "tests/test_trust_guards.py", "line": 38, "issue": "BLOCKED_COMMANDS contains no subshell-wrapped write and no alternate-redirect (>|/>&/&>>) case, so the suite stays green across the entire grouping/redirect bypass class.", "scenario": "A 148-passing guard suite certifies a guard that allows `( touch .commit-approved )` and `echo x >| .git/.review-approved`; the tables again encode the rules' shape rather than the invariant the plan claims was corrected."},
{"severity": "minor", "file": "workflow-trust-plan.md", "line": 1615, "issue": "Validation-matrix rows 48/60/61 record protected-artifact writes and redirection targets as blocked/gated, but neither holds when the write verb is inside `( … )`/`{ …; }` or uses a `>|`/`>&`/`&>>` redirect.", "scenario": "A reader of the matrix concludes sentinel forging and approval-hash writes are covered and does not re-test; the guard silently permits both whenever the write is grouped in a subshell or uses a non-enumerated write-redirect operator."}
]}
```
