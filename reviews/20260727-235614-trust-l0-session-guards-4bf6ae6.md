# Agent review — request_changes

date: 2026-07-28T00:01:17.032540
range: origin/main..HEAD
branch: trust/l0-session-guards
findings: 3

- **major** `.claude/hooks/bash_guard.py:209` — Destructive-delete rule reads the verb from seg[0] without skipping leading ENV=value assignments (unlike write_targets), so an env prefix disables it.
  - scenario: Agent runs `LC_ALL=C rm -rf .git` (verified: guard exits 0); the repo's entire git-side gate stack — hooks config, .review-approved, ledger history — is deleted; `FOO=1 rm -rf /` is likewise allowed.
- **major** `.claude/hooks/bash_guard.py:228` — Hook-skip rule selects the git subcommand by tuple order ('commit' before 'push'), not token position, so a later token equal to 'commit' redirects the scan and leaves push flags unexamined.
  - scenario: Agent creates a branch named `commit`, then runs `git push --no-verify origin commit` (verified: guard exits 0) — pre-push is skipped entirely, pushing unreviewed/red content past the suite and the backstop review.
- **minor** `.claude/hooks/bash_guard.py:251` — Rule 6 fires on any segment where 'push' follows 'git', so non-push subcommands like `git stash push -f` are treated as a bare force-push.
  - scenario: With main checked out, `git stash push -f` (or any `git <sub> push -f`) is falsely blocked as 'force-push of the checked-out main' — fails closed, but blocks a legitimate local operation.

---

## Full review

Both suspicions confirmed empirically. Review complete.

## Findings

**1. (major) `bash_guard.py` rule 3 — env-assignment prefix bypasses the destructive-delete rule.**
`.claude/hooks/bash_guard.py:209` checks `os.path.basename(seg[0]) == "rm"`, but `seg[0]` can be a leading `NAME=value` token — the very case `write_targets()` explicitly skips (line 165). The two code paths disagree, and rule 3 never got the env-skip. Verified live: `FOO=1 rm -rf .git` and `FOO=1 rm -rf /` both exit 0 (allowed), while `rm -rf .git` blocks. `.git` and `/`/`~` are only protected by rule 3 (rule 2's `is_always_protected` covers `.git/config` and `.git/hooks`, not `.git` itself), so a single innocuous-looking env prefix (`LC_ALL=C rm -rf .git`) deletes the entire git-side gate stack — hooks, the approval hash, the ledgers' history. This is exactly the "assumed a simpler shell grammar" class rounds 9–12 were fixing, and the test table (`tests/test_trust_guards.py:684`) covers only the bare spellings.

**2. (major) `bash_guard.py` rule 5 — subcommand selection is tuple-ordered, not position-ordered, so `git push --no-verify` can go unscanned.**
`.claude/hooks/bash_guard.py:228` picks `sub = next(s for s in ("commit", "push") ...)` — it prefers the token `commit` anywhere after `git`, regardless of position. Verified live: `git push --no-verify origin commit` exits 0 — `commit` (here a ref name) wins the tuple race, `args` becomes the empty slice after it, and the `--no-verify` at position 2 is never examined. An agent can trivially create a branch named `commit`, and `git push --no-verify` skips pre-push entirely — the offline suite, the backstop review, and the unapproved-content gate (the guard's own test at `tests/test_trust_guards.py:621` treats `push --no-verify` as a must-block). Fix: choose the commit/push token with the smallest index, not the first tuple entry that appears anywhere.

**3. (minor) `bash_guard.py` rule 6 — `git stash push -f` from a main checkout is falsely blocked.**
`.claude/hooks/bash_guard.py:251` triggers on any segment where `push` follows `git`, so `git stash push -f` is parsed as a bare force-push (flags `["-f"]`, no refspecs) and, when the checked-out branch is main/master, blocked at line 294 — though `stash push` touches no remote. Ergonomic false positive only (fails closed), same class for `git svn push -f` etc.

Things I checked that dissolved: the linked-worktree gap in `path_guard`'s literal `.git/.review-approved` entry is not exploitable because `.githooks/pre-push:51` and `commit-msg:42` also read the literal `.git/...` path — in a worktree they simply fail closed into a re-review; quoted/clustered/reordered `-n` and force-push spellings, redirect variants, grouping tokens, and the override-scope split all behave as the tests claim; the COORDINATION.md logging obligation is met; no schema, contract, or ingest-abort surface is touched; no tests were deleted or weakened (the 480→900s reviewer timeout stays fail-closed).

```json
{"verdict": "request_changes", "findings": [
  {"severity": "major", "file": ".claude/hooks/bash_guard.py", "line": 209, "issue": "Destructive-delete rule reads the verb from seg[0] without skipping leading ENV=value assignments (unlike write_targets), so an env prefix disables it.", "scenario": "Agent runs `LC_ALL=C rm -rf .git` (verified: guard exits 0); the repo's entire git-side gate stack — hooks config, .review-approved, ledger history — is deleted; `FOO=1 rm -rf /` is likewise allowed."},
  {"severity": "major", "file": ".claude/hooks/bash_guard.py", "line": 228, "issue": "Hook-skip rule selects the git subcommand by tuple order ('commit' before 'push'), not token position, so a later token equal to 'commit' redirects the scan and leaves push flags unexamined.", "scenario": "Agent creates a branch named `commit`, then runs `git push --no-verify origin commit` (verified: guard exits 0) — pre-push is skipped entirely, pushing unreviewed/red content past the suite and the backstop review."},
  {"severity": "minor", "file": ".claude/hooks/bash_guard.py", "line": 251, "issue": "Rule 6 fires on any segment where 'push' follows 'git', so non-push subcommands like `git stash push -f` are treated as a bare force-push.", "scenario": "With main checked out, `git stash push -f` (or any `git <sub> push -f`) is falsely blocked as 'force-push of the checked-out main' — fails closed, but blocks a legitimate local operation."}
]}
```
