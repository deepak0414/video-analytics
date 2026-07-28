# Agent review — request_changes

date: 2026-07-28T00:09:46.030104
range: origin/main..HEAD
branch: trust/l0-session-guards
findings: 4

- **major** `.claude/hooks/bash_guard.py:234` — Recursive-delete rule matches only lowercase 'r' in short flags, so the POSIX -R and GNU --recursive spellings bypass it for the paths only rule 3 protects (.git, /, ~).
  - scenario: Agent runs `rm -R .git` or `rm --recursive .git` (verified: guard exits 0, while `rm -rf .git` blocks) — hooks config, .review-approved, and ledger history are deleted; `rm -R /` is likewise allowed.
- **major** `.claude/hooks/bash_guard.py:215` — ALLOW_* override rule blocks only the exact '=1' spelling, but pre-commit/pre-push honor any non-empty value ([ -z ... ]), so every other value is an unguarded waiver.
  - scenario: Agent runs `ALLOW_MAIN_COMMIT=yes git commit -m x` (verified: guard exits 0) and the hook accepts the waiver; `ALLOW_LEDGER_EDIT=y git push` similarly waives the ledger-forgery gate.
- **major** `.claude/hooks/bash_guard.py:245` — gh self-approval rule reads lowered[:3] positionally instead of command_name() and matches only exact tokens, missing env prefixes, gh's short -a approve flag, and attached --add-label=value forms.
  - scenario: Verified all exit 0: `FOO=1 gh pr merge 14` self-merges a PR, `gh pr review 14 -a` self-approves, `gh pr edit 14 --add-label=human-reviewed` self-labels — each a one-command breach of the human-only P3 invariant.
- **minor** `.claude/hooks/path_guard.py:30` — Stop-gate cache protection is the literal '.git/.stop-gate-green', but in a linked worktree stop_gate.sh resolves the cache to .git/worktrees/<name>/.stop-gate-green, which the Write tool may freely create.
  - scenario: Agent in a worktree session computes the cache key from read-only git commands and Writes the worktree-local .stop-gate-green — the Stop gate reads it and lets the turn end with a red offline suite (fails open; pre-push still catches at push time).

---

## Full review

Review complete. I verified every finding empirically by piping PreToolUse payloads into the guards; the three round-13 ledger findings (env-prefixed `rm`, positional subcommand selection, `git stash push -f`) are all genuinely fixed in HEAD. But the same "alternate spelling / same class, different rule" pattern that drove rounds 9–13 has three more instances, each confirmed live.

## Findings

**1. (major) `bash_guard.py:234` — recursive-delete detection misses `-R` and `--recursive`.**
Rule 3 requires a short flag containing lowercase `"r"`. `-R` is the POSIX-guaranteed recursive spelling and `--recursive` the GNU long form; both sail through. Verified: `rm -R .git`, `rm --recursive .git`, and `rm -R /` all exit 0, while `rm -rf .git` blocks. The blast radius is exactly the paths only rule 3 protects — `.git` itself, `/`, `~` (machinery dirs like `.claude/` are still caught by rule 2's write-target check). One character (`"r" in f.lower()`, plus handling `--recursive`) deletes the entire git-side gate stack.

**2. (major) `bash_guard.py:215` — ALLOW_* rule blocks only the `=1` spelling, but the hooks honor any non-empty value.**
`.githooks/pre-commit:10,28,44` and `pre-push:63` all test `[ -z "${ALLOW_*:-}" ]` — any non-empty value is a valid waiver. The guard's `t.endswith("=1")` therefore blocks only one spelling of an infinite family. Verified: `ALLOW_MAIN_COMMIT=yes git commit -m x` exits 0, and the hook would honor it; same for `ALLOW_LEDGER_EDIT=y git push` (waives the ledger-forgery gate) and `ALLOW_TEST_REMOVAL=true`. Fix on either side: guard blocks any non-empty assignment, or hooks require exactly `1` (belt-and-suspenders: both).

**3. (major) `bash_guard.py:245` — the gh self-approval rule (P3) never got the round-13 treatment; three confirmed bypasses.**
Rule 4 reads `lowered[:3]` positionally instead of using `command_name()` — the file's own docstring at line 165 says "ONE definition, used by every rule" because a rule reading `seg[0]` directly was disabled by an env prefix, yet rule 4 still does exactly that. Verified all three exit 0: `FOO=1 gh pr merge 14` (env prefix shifts the window), `gh pr review 14 -a` (`-a` is gh's short form of `--approve`), and `gh pr edit 14 --add-label=human-reviewed` (attached `=value` form defeats exact-token matching). Each is a one-command self-approval/self-merge, the invariant CLAUDE.md marks human-only.

**4. (minor) `path_guard.py:30` — the stop-gate cache is unprotected in linked worktrees, and forging it fails *open*.**
`stop_gate.sh:15` resolves the cache to `$(git rev-parse --git-dir)/.stop-gate-green`, which in a linked worktree is `.git/worktrees/<name>/.stop-gate-green`, but path_guard protects only the literal `.git/.stop-gate-green`. An agent in a worktree session can Write the cache with the current key (derivable from read-only git commands) and end turns with a red suite unchecked. The prior ledger dissolved the analogous `.review-approved` worktree gap because those hooks fail closed into a re-review; this one fails open (the gate is skipped). Bounded — pre-push still runs the suite — hence minor. A basename match (like bash_guard's `SENTINELS` check) closes it.

Things I checked that dissolved: quoted/clustered/reordered `-n` and force-push spellings, `--force-with-lease=<ref>`, `+refspec`, `--mirror`/`--all`, redirect variants including `>|`/`>&`, grouping tokens, subshell-via-`$(...)` tokenization, the override-scope split (machinery relaxes, sentinels/waivers/ledgers don't), the pre-push main-deletion addition, the 480→900s reviewer timeout (stays fail-closed), COORDINATION logging, and test integrity (446 lines of new tests, none removed or weakened; each prior review round's bypass became a regression case). No src/ contract, schema, or ingest surface is touched by this range.

Two process notes for the human: **`.guard-override` is currently present in the repo** (created Jul 27 21:30) — the maintenance window is still open and should be removed per CLAUDE.md; and my sandbox denied running pytest, so I did not re-execute the suite myself (my verification was direct guard invocation).

```json
{"verdict": "request_changes", "findings": [
{"severity": "major", "file": ".claude/hooks/bash_guard.py", "line": 234, "issue": "Recursive-delete rule matches only lowercase 'r' in short flags, so the POSIX -R and GNU --recursive spellings bypass it for the paths only rule 3 protects (.git, /, ~).", "scenario": "Agent runs `rm -R .git` or `rm --recursive .git` (verified: guard exits 0, while `rm -rf .git` blocks) — hooks config, .review-approved, and ledger history are deleted; `rm -R /` is likewise allowed."},
{"severity": "major", "file": ".claude/hooks/bash_guard.py", "line": 215, "issue": "ALLOW_* override rule blocks only the exact '=1' spelling, but pre-commit/pre-push honor any non-empty value ([ -z ... ]), so every other value is an unguarded waiver.", "scenario": "Agent runs `ALLOW_MAIN_COMMIT=yes git commit -m x` (verified: guard exits 0) and the hook accepts the waiver; `ALLOW_LEDGER_EDIT=y git push` similarly waives the ledger-forgery gate."},
{"severity": "major", "file": ".claude/hooks/bash_guard.py", "line": 245, "issue": "gh self-approval rule reads lowered[:3] positionally instead of command_name() and matches only exact tokens, missing env prefixes, gh's short -a approve flag, and attached --add-label=value forms.", "scenario": "Verified all exit 0: `FOO=1 gh pr merge 14` self-merges a PR, `gh pr review 14 -a` self-approves, `gh pr edit 14 --add-label=human-reviewed` self-labels — each a one-command breach of the human-only P3 invariant."},
{"severity": "minor", "file": ".claude/hooks/path_guard.py", "line": 30, "issue": "Stop-gate cache protection is the literal '.git/.stop-gate-green', but in a linked worktree stop_gate.sh resolves the cache to .git/worktrees/<name>/.stop-gate-green, which the Write tool may freely create.", "scenario": "Agent in a worktree session computes the cache key from read-only git commands and Writes the worktree-local .stop-gate-green — the Stop gate reads it and lets the turn end with a red offline suite (fails open; pre-push still catches at push time)."}
]}
```
