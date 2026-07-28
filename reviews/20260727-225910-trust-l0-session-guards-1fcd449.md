# Agent review — request_changes

date: 2026-07-27T23:06:41.906270
range: origin/main..HEAD
branch: trust/l0-session-guards
findings: 4

- **major** `.claude/hooks/bash_guard.py:48` — The commit -n rule only matches when the n-flag is the first token after `commit`, so any preceding flag bypasses the --no-verify block.
  - scenario: `git commit --amend -n -m x`, `git commit -a -n -m x` and `git commit -q -nm x` all exit 0 from the committed guard (verified; `git commit -nm x` still exits 2), skipping .githooks/pre-commit and with it the main-branch guard, secret scan, test-deletion guard and ledger append-only check — and `--amend` is this repo's documented finalize command. Regression from the round-7 narrowing: the prior rule caught these.
- **major** `.claude/hooks/bash_guard.py:40` — MACHINERY and the reviews/ rule require a trailing slash, so bare-directory writes/removals (`rm -rf .githooks`, `rm -rf .claude`, `rm -rf reviews`, `mv .claude /tmp/x`) are allowed.
  - scenario: `rm -rf .githooks` exits 0 from the guard (verified; `rm -rf .githooks/` exits 2) and deletes the directory core.hooksPath points at, so git silently runs no pre-commit or pre-push hooks at all — removing the review backstop; `rm -rf reviews` destroys the untracked pending ledgers, which the pre-commit --diff-filter=MDR check cannot protect.
- **minor** `tests/test_trust_guards.py:441` — BLOCKED_COMMANDS again mirrors the regex shapes rather than the invariants: every n-flag case puts the flag first and every machinery case uses a trailing slash, so both bypasses above pass the suite.
  - scenario: A 126-green run certifies a guard that permits `git commit --amend -n` and `rm -rf .githooks`, contradicting the plan's as-built claim that the tables were made variant-based after the round-1 root-cause analysis.
- **minor** `.guard-override:1` — The human maintenance sentinel is still present in the working tree at finalize, relaxing the machinery-write and core.hooksPath rules for every future session on this machine.
  - scenario: While .guard-override exists both guards drop MAINTENANCE_RULES, so any agent session can rewrite .githooks/, .claude/ and the trust scripts or repoint core.hooksPath; flagged in the previous ledger and still unresolved (confirmed via git status --ignored).

---

## Full review

I probed the committed guards directly rather than reading the regexes alone (`CLAUDE_PROJECT_DIR` pointed at a clean dir so the maintenance rules were active), with known-blocked forms as sanity controls. Two real bypasses survived verification.

## Findings

**1. major — `.claude/hooks/bash_guard.py:48` — `git commit -n` is only caught when `-n` is the *first* token after `commit`.**

The round-7 fix anchored the rule to `commit\b(?:\s+-[a-zA-Z]*n[a-zA-Z]*)`, which consumes exactly one flag group immediately after the subcommand. Any flag in front of `-n` walks past it. Verified against the committed hook:

```
exit=0  git commit -a -n -m x
exit=0  git commit --amend -n -m x
exit=0  git commit -q -nm x
exit=0  git commit --amend --no-edit -n
exit=2  git commit -nm x          (control — still blocked)
```

`-n` is `--no-verify`, so this skips `.githooks/pre-commit` entirely: the main-branch guard, the secret scan, the net-test-deletion guard, and the ledger append-only check are all bypassable in-session. The `--amend` form is the worst case because this repo's own documented finalize step is `git commit --amend`. Note this is a *regression* introduced by this diff's last round — the previous rule (`commit\b[^|;&\n]*\s-n\b`, still visible in the plan's before-image) caught `git commit -a -n -m x`; narrowing it to fix the " -n in a commit message" false positive traded a minor for a hole in the guard's headline invariant. The push-side backstop still holds, so nothing unreviewed reaches origin, which is why this is major and not critical.

**2. major — `.claude/hooks/bash_guard.py:40` — machinery and ledger paths are matched with a mandatory trailing slash, so directory-level removal walks through.**

`MACHINERY` requires `\.githooks/`, `\.claude/`, `\.github/`, and the `reviews/` rule requires `\breviews/`. Naming the directory without the slash — the natural spelling for `rm`/`mv` — matches nothing:

```
exit=0  rm -rf .githooks
exit=0  rm -rf .claude
exit=0  rm -rf reviews
exit=0  mv .claude /tmp/xx
exit=0  cp -r /tmp/evil .claude
exit=2  rm -rf .githooks/         (control — trailing slash blocks)
exit=2  cp /tmp/evil.py .claude/hooks/bash_guard.py   (control)
```

`rm -rf .githooks` deletes the directory `core.hooksPath` points at; git then runs no hooks at all and silently succeeds, so pre-commit *and* pre-push — including the agent-review backstop that finding 1 relies on — are gone in one command. `rm -rf .claude` removes the session guards themselves. `rm -rf reviews` destroys the pending ledgers, which are untracked on this branch and therefore not protected by the pre-commit `--diff-filter=MDR` append-only check. The `.git`-deletion rule at line 73 doesn't cover these (`\.git(/\S*)?` requires a following space, so `.githooks` never matches — correctly, since that rule is about `.git` itself).

**3. minor — `tests/test_trust_guards.py:441` — the tables mirror the regex shapes again, which is why both bypasses above are green.**

Every `-n` case in `BLOCKED_COMMANDS` places the n-flag first (`-n -m x`, `-nm x`, `-anm x`, `git -c … commit -nm x`); every machinery case uses a trailing slash (`.githooks/pre-commit`, `.claude/hooks/bash_guard.py`, `reviews/fake.md`). The plan's as-built note claims the root cause was fixed — "the test tables mirrored the regexes, not the invariants — tables are now variant-based" — but the variant axes that matter here (flag *position* before `-n`, path spelled as a bare directory) are absent, so a 126-green suite certifies a guard that lets `git commit --amend -n` and `rm -rf .githooks` through.

**4. minor — `.guard-override` is still present in the working tree at finalize** (confirmed via `git status --ignored`). While it exists, both guards drop their MAINTENANCE rules on this machine for every future session, so `core.hooksPath` changes and machinery writes are permitted. The previous ledger (`…-6d3f35d.md`) flagged this and it is unresolved; it's gitignored and outside the committed diff, so it's the human's cleanup, not a code change.

Everything else I attacked held: `git -c`/`-C` prefix insertion, quoted refspecs, reordered `--force`, `+refspec`/`HEAD`/`@` forms, `--mirror`, `--all` force, `:main` and `--delete`/`-d` deletions, the `WRITE_VERBS` list against `sed -i`/`dd`/`ln`/`install`/`truncate`, path_guard's `.git/config` and `.git/hooks/` coverage and its `..`-normalization, the override-scoping split across both guards, and the pre-push `refs/heads/main` deletion gate. The stop-gate cache key (HEAD + tracked diff + untracked content over `src tests config .claude .githooks scripts`), its exit-code judgment, `</dev/null`, and the `rev-parse --git-dir` worktree fix all look correct.

```json
{"verdict": "request_changes", "findings": [{"severity": "major", "file": ".claude/hooks/bash_guard.py", "line": 48, "issue": "The commit -n rule only matches when the n-flag is the first token after `commit`, so any preceding flag bypasses the --no-verify block.", "scenario": "`git commit --amend -n -m x`, `git commit -a -n -m x` and `git commit -q -nm x` all exit 0 from the committed guard (verified; `git commit -nm x` still exits 2), skipping .githooks/pre-commit and with it the main-branch guard, secret scan, test-deletion guard and ledger append-only check — and `--amend` is this repo's documented finalize command. Regression from the round-7 narrowing: the prior rule caught these."}, {"severity": "major", "file": ".claude/hooks/bash_guard.py", "line": 40, "issue": "MACHINERY and the reviews/ rule require a trailing slash, so bare-directory writes/removals (`rm -rf .githooks`, `rm -rf .claude`, `rm -rf reviews`, `mv .claude /tmp/x`) are allowed.", "scenario": "`rm -rf .githooks` exits 0 from the guard (verified; `rm -rf .githooks/` exits 2) and deletes the directory core.hooksPath points at, so git silently runs no pre-commit or pre-push hooks at all — removing the review backstop; `rm -rf reviews` destroys the untracked pending ledgers, which the pre-commit --diff-filter=MDR check cannot protect."}, {"severity": "minor", "file": "tests/test_trust_guards.py", "line": 441, "issue": "BLOCKED_COMMANDS again mirrors the regex shapes rather than the invariants: every n-flag case puts the flag first and every machinery case uses a trailing slash, so both bypasses above pass the suite.", "scenario": "A 126-green run certifies a guard that permits `git commit --amend -n` and `rm -rf .githooks`, contradicting the plan's as-built claim that the tables were made variant-based after the round-1 root-cause analysis."}, {"severity": "minor", "file": ".guard-override", "line": 1, "issue": "The human maintenance sentinel is still present in the working tree at finalize, relaxing the machinery-write and core.hooksPath rules for every future session on this machine.", "scenario": "While .guard-override exists both guards drop MAINTENANCE_RULES, so any agent session can rewrite .githooks/, .claude/ and the trust scripts or repoint core.hooksPath; flagged in the previous ledger and still unresolved (confirmed via git status --ignored)."}]}
```
