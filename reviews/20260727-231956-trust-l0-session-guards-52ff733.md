# Agent review — request_changes

date: 2026-07-27T23:29:27.115061
range: origin/main..HEAD
branch: trust/l0-session-guards
findings: 4

- **critical** `.claude/hooks/bash_guard.py:39` — Every protected-artifact rule joins the write verb to the target with [^|;&\n]*, so any ';', '&' or '|' character between them — including inside a quoted argument — makes the rule fail to match, leaving the self-bless and machinery paths writable from Bash.
  - scenario: Verified against the committed guard: `cp "/tmp/a&b.txt" .git/.review-approved`, `touch "x&y" .commit-approved`, `ln -sf "/bin/a&b" .venv/bin/pytest`, `cp "/tmp/x&y" .githooks/pre-commit` and `sed -i 's/request_changes/approve &/' reviews/x.md` all exit 0. Chained: `scripts/review_scope_hash.sh > "/tmp/a&b"` then `cp "/tmp/a&b" .git/.review-approved` then `touch "x&y" .commit-approved` satisfies commit-msg:42-46 and makes pre-push:51 skip the review backstop, so an agent finalizes and pushes its own unreviewed work; no CI layer exists yet (.github/workflows absent).
- **major** `.claude/hooks/bash_guard.py:119` — Both tokenizer loops pre-split the command with re.split(r"[|;&]", cmd), which cuts inside quoted arguments, so a commit message or config value containing '&', ';' or '|' hands the commit -n/--no-verify check and the force-push destination matcher a broken fragment that no longer contains 'git'.
  - scenario: `git commit -m "feat: ingest & query" -n`, `git commit -am "wip: a & b" --no-verify` and `git -c user.email="a&b" push --force origin main` all exit 0 from the committed guard, and I confirmed it live in-session (`git commit --dry-run -m "chore: probe A & B" -n` reached git). pre-commit (main-branch guard, secret scan, test-deletion guard, ledger append-only) is skipped, and the same trick on `git -c x="a&b" push --no-verify` skips pre-push's suite and review backstop; an ampersand in a commit message disables the guard with no intent to evade.
- **minor** `tests/test_trust_guards.py:38` — No case in BLOCKED_COMMANDS places a shell separator character inside a quoted argument, so the suite stays green over the entire quoted-separator bypass class.
  - scenario: A green 100+ case guard suite certifies a guard that allows `git commit -m "a & b" -n` and `cp "/tmp/a&b" .git/.review-approved`; the tables again encode the regexes' shape rather than the invariant, which the plan claims was corrected in an earlier round.
- **minor** `workflow-trust-plan.md:1534` — Validation-matrix rows 48 and 57 record these behaviors as blocked ('one WRITE_VERBS list guards every protected artifact', 'hook-skip flags in every argv position'), but neither holds once a quoted separator is present, so the WT.3 'Done when' rows are asserted rather than met.
  - scenario: A reader of the matrix concludes ledger forging, approval-hash writes and no-verify commits are covered and does not re-test; the guards silently permit all three whenever an argument contains '&', ';' or '|'.

---

## Full review

## Review: `origin/main..HEAD` — WT.3 session guards

Two commits (`52ff733` guards, `b546f05` docs). The previous ledger's major is genuinely fixed: `git commit -m x -n` and `-F /tmp/m -n` now exit 2 against the committed guard. But the same invariant has a broader hole underneath it, and this one also breaks every *artifact* rule, not just the commit rules.

**Root cause: bash_guard treats `;`, `&`, and `|` as command separators without any shell awareness.** Both the rule table (`[^|;&\n]*` between verb and target, `bash_guard.py:39` + rules at 59–76, 83) and the two tokenizer loops (`re.split(r"[|;&]", cmd)` at lines 119 and 160) assume those characters can only appear as syntax. Inside a quoted argument they are ordinary text — and their presence silently disables the rule. Verified against the committed guard with `CLAUDE_PROJECT_DIR` pointed at a clean temp dir (exit 0 = allowed):

```
0  git commit -m "feat: ingest & query" -n         # --no-verify, hooks skipped
0  git commit -am "wip: a & b" --no-verify
0  git -c user.email="a&b" push --force origin main
0  cp "/tmp/a&b.txt" .git/.review-approved         # self-bless the approval hash
0  touch "x&y" .commit-approved                    # forge the human sentinel
0  ln -sf "/bin/a&b" .venv/bin/pytest              # neuter the test runner
0  cp "/tmp/x&y" .githooks/pre-commit              # rewrite gate machinery
0  sed -i 's/request_changes/approve &/' reviews/x.md   # & is normal sed syntax
```

I also confirmed this live in this session, not just against the script: `git commit --dry-run -m "chore: probe A & B" -n` sailed past the PreToolUse hook and reached git. The same command without the ampersand is blocked.

The artifact-rule half chains into complete self-approval with no remaining layer. `scripts/review_scope_hash.sh > "/tmp/a&b"` (unguarded, ordinary temp write), then `cp "/tmp/a&b" .git/.review-approved`, then `touch "x&y" .commit-approved`, then a plain-subject commit: `commit-msg:42-46` is satisfied and `pre-push:51` sees a matching hash and prints "content already approved — skipping re-review." Unreviewed code reaches the remote. `.github/workflows/` does not exist yet, so WT.5/CI is not standing behind this today.

I considered whether this falls under the plan's recorded residual ("bash_guard is hardening, not proof — arbitrary shell, e.g. inline python writes, can evade"). It doesn't. These are the exact verbs the rules enumerate (`cp`, `touch`, `ln`, `sed -i`) against the exact paths they enumerate, defeated by an incidental character in an unrelated argument. It is also reachable *by accident*: an ampersand in a commit message is not an attack, and it turns the `--no-verify` guard off.

The fix that matches the round-9 lesson already in the plan ("when a rule needs to understand a command's grammar, parse it") is to split into segments with a quote-aware scan (or `shlex` first, then reassemble by operator token) and run the artifact rules over tokens rather than over a raw-string character class.

**Test integrity.** No existing test was deleted or weakened; the new module is substantial. But `BLOCKED_COMMANDS` (line 38) contains no case with a separator character inside a quoted argument — the only `&` in the whole file is `2>&1` in an *allowed* command. So the tables again mirror the regexes rather than the invariant, which is the root cause the plan says was fixed by making them variant-based.

**Plan gap.** Validation-matrix rows 48 and 57 (`workflow-trust-plan.md:1534,1543`) assert "one WRITE_VERBS list guards every protected artifact" and "hook-skip flags in every argv position — decided by shell tokenization." Both claims are false for any command containing a quoted separator, so those "Done when" rows are not met.

Everything else I attacked held: the `-n`/`--no-verify` tokenizer for separator-free commands, `git -c/-C` prefix insertion, quoted/reordered/`+refspec`/`HEAD`/`@`/`refs/heads` force-push forms, `--mirror`/`--all`/`:main`/`--delete`, the pre-push `refs/heads/main` deletion guard, path_guard's override-scoping split and `.git/config` + `.git/hooks/` coverage, and the stop-gate cache key. Out of committed scope but worth repeating a third time: `.guard-override` is still present in the working tree, so MAINTENANCE_RULES are dropped in every session on this machine until the human removes it.

```json
{"verdict": "request_changes", "findings": [{"severity": "critical", "file": ".claude/hooks/bash_guard.py", "line": 39, "issue": "Every protected-artifact rule joins the write verb to the target with [^|;&\\n]*, so any ';', '&' or '|' character between them — including inside a quoted argument — makes the rule fail to match, leaving the self-bless and machinery paths writable from Bash.", "scenario": "Verified against the committed guard: `cp \"/tmp/a&b.txt\" .git/.review-approved`, `touch \"x&y\" .commit-approved`, `ln -sf \"/bin/a&b\" .venv/bin/pytest`, `cp \"/tmp/x&y\" .githooks/pre-commit` and `sed -i 's/request_changes/approve &/' reviews/x.md` all exit 0. Chained: `scripts/review_scope_hash.sh > \"/tmp/a&b\"` then `cp \"/tmp/a&b\" .git/.review-approved` then `touch \"x&y\" .commit-approved` satisfies commit-msg:42-46 and makes pre-push:51 skip the review backstop, so an agent finalizes and pushes its own unreviewed work; no CI layer exists yet (.github/workflows absent)."}, {"severity": "major", "file": ".claude/hooks/bash_guard.py", "line": 119, "issue": "Both tokenizer loops pre-split the command with re.split(r\"[|;&]\", cmd), which cuts inside quoted arguments, so a commit message or config value containing '&', ';' or '|' hands the commit -n/--no-verify check and the force-push destination matcher a broken fragment that no longer contains 'git'.", "scenario": "`git commit -m \"feat: ingest & query\" -n`, `git commit -am \"wip: a & b\" --no-verify` and `git -c user.email=\"a&b\" push --force origin main` all exit 0 from the committed guard, and I confirmed it live in-session (`git commit --dry-run -m \"chore: probe A & B\" -n` reached git). pre-commit (main-branch guard, secret scan, test-deletion guard, ledger append-only) is skipped, and the same trick on `git -c x=\"a&b\" push --no-verify` skips pre-push's suite and review backstop; an ampersand in a commit message disables the guard with no intent to evade."}, {"severity": "minor", "file": "tests/test_trust_guards.py", "line": 38, "issue": "No case in BLOCKED_COMMANDS places a shell separator character inside a quoted argument, so the suite stays green over the entire quoted-separator bypass class.", "scenario": "A green 100+ case guard suite certifies a guard that allows `git commit -m \"a & b\" -n` and `cp \"/tmp/a&b\" .git/.review-approved`; the tables again encode the regexes' shape rather than the invariant, which the plan claims was corrected in an earlier round."}, {"severity": "minor", "file": "workflow-trust-plan.md", "line": 1534, "issue": "Validation-matrix rows 48 and 57 record these behaviors as blocked ('one WRITE_VERBS list guards every protected artifact', 'hook-skip flags in every argv position'), but neither holds once a quoted separator is present, so the WT.3 'Done when' rows are asserted rather than met.", "scenario": "A reader of the matrix concludes ledger forging, approval-hash writes and no-verify commits are covered and does not re-test; the guards silently permit all three whenever an argument contains '&', ';' or '|'."}]}
```
