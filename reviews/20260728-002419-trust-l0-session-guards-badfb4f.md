# Agent review — request_changes

date: 2026-07-28T00:32:22.174926
range: origin/main..HEAD
branch: trust/l0-session-guards
findings: 5

- **critical** `.claude/hooks/bash_guard.py:67` — A backslash-newline line continuation is preserved into the tokenizer, so shlex prefixes a literal newline onto the next token and every rule keyed on a flag or subcommand token (which test t.startswith('-') or compare exactly) silently fails to match.
  - scenario: Verified against real bash with a fake git on PATH: `git commit \\<newline>-n -m x` executes as `commit -n -m x`, `git push \\<newline>--force origin main` executes as `push --force origin main`, and `git push \\<newline>--no-verify origin feature` executes as `push --no-verify origin feature` — all three exit 0 from bash_guard, letting the agent skip pre-commit hooks, force-push main (the settings.json deny entry for --force was removed in this same diff and the remaining --no-verify glob no longer matches), and skip pre-push entirely, which is both the offline suite and the review backstop. Same result for `git \\<newline>commit -nm x` and `gh pr \\<newline>merge 14`. No CI layer exists yet (.github/ is absent), so nothing stands behind this.
- **major** `.claude/hooks/bash_guard.py:71` — The escape branch runs before the quote branch, so a backslash is honored as an escape inside single quotes (where POSIX defines none), desyncing quote tracking so the closing quote and the following newline are absorbed and later command lines are merged into one unanalyzed segment.
  - scenario: The two-line command `echo 'a\\'` + newline + `touch .commit-approved` exits 0 from bash_guard; executing it under bash actually creates the human-only .commit-approved sentinel (verified in a scratch dir). Quote desync also forces the shlex ValueError fallback to the quote-naive command.split(), so the merged segment's command name is the harmless first word and no write-verb, git, gh, or rm rule fires.
- **minor** `.claude/hooks/bash_guard.py:324` — The commit -n rule inspects attached-value flag tokens, so any message supplied in the attached form -m'...' that contains the letter n is blocked as --no-verify.
  - scenario: `git commit -m'no changes'` and `git commit -am'new feature'` tokenize to `-mno changes` / `-amnew feature`; t[1:] contains 'n' and the guard blocks a legitimate commit. Fails closed, but contradicts plan matrix rows 54/58, and no test covers the attached-value spelling.
- **minor** `.claude/hooks/stop_gate.sh:8` — The Stop gate exits 0 when .venv/bin/pytest is missing, and bash_guard protects only that exact path, not the .venv directory that contains it.
  - scenario: `rm -rf .venv` and `mv .venv /tmp/v` both return exit 0 from bash_guard (verified); afterwards the `[ -x .venv/bin/pytest ] || exit 0` early-out makes the Stop gate no-op on a red suite, which is exactly the self-blessing that plan matrix row 44 claims is closed for the runner path.
- **minor** `.githooks/pre-push:10` — The new refs/heads/main deletion branch has no test, although the plan's validation matrix (declared to be the rollout's 'Done when') lists blocking it at pre-push as part of row 50.
  - scenario: tests/test_trust_hooks.py:566 exercises only the opposite branch (feature-ref deletion skips gates); a future edit that reorders the case statement or drops the fail() call would let `git push origin :main` through with a green suite.

---

## Full review

I found and empirically verified several guard bypasses. Here is the review.

## Findings

**1. (critical) `.claude/hooks/bash_guard.py:67` — a backslash-newline line continuation corrupts the next token, disabling every flag- and subcommand-keyed rule.**

`_split_lines_outside_quotes` treats `\` as an escape and appends both the backslash and the following newline into the current line. `_tokenize` then hands that to `shlex(posix=True)`, which turns `\<newline>` into a *literal newline character prefixed onto the next token* — whereas bash removes it entirely. So the guard sees `"\n-n"` instead of `-n`, and every rule that tests `t.startswith("-")` or compares a token exactly falls through. Paths escape this because `norm()` calls `.strip()`; flags and subcommands never get stripped.

Verified against a fake `git` on PATH — bash really does execute the dangerous form while the guard exits 0:

| command | bash executes | guard |
|---|---|---|
| `git commit \⏎-n -m x` | `commit -n -m x` | allow |
| `git push \⏎--force origin main` | `push --force origin main` | allow |
| `git push \⏎--no-verify origin feature` | `push --no-verify origin feature` | allow |
| `git \⏎commit -nm x` | `commit -nm x` | allow |
| `gh pr \⏎merge 14` | `pr merge 14` | allow |

This is the same class as the round-15 newline critical, and reachable with no evasive intent at all: multi-line git invocations formatted with trailing backslashes are ordinary agent output. The consequences are the full set the guard exists to prevent — `git push \⏎--no-verify` skips `pre-push` entirely (offline suite *and* the review backstop), and force-push to main has no other L0 layer since `Bash(git push --force*)` was deliberately dropped from the `settings.json` deny list in this same diff. Note that the deny list also can't catch `git commit \⏎--no-verify`, because the glob `git commit --no-verify*` no longer matches the string. There is no `.github/` directory yet, so WT.5's CI is not standing behind this.

**2. (major) `.claude/hooks/bash_guard.py:71` — backslash is honored as an escape inside single quotes, desyncing quote tracking and swallowing subsequent command lines.**

The `if ch == "\\"` branch runs *before* the `if quote:` branch, so a backslash inside a single-quoted string escapes the closing quote. POSIX single quotes have no escapes. When the closing `'` is consumed, `quote` never resets, the newline is absorbed as quoted text, all lines merge into one, `shlex` raises `ValueError` on the unbalanced quote, and the quote-naive `command.split()` fallback yields one segment whose command name is the harmless first word.

Verified end to end: `echo 'a\'` + newline + `touch .commit-approved` is allowed by the guard, and running it under bash **creates the human-only approval sentinel** in the target directory.

**3. (minor) `.claude/hooks/bash_guard.py:324` — the `-n` rule false-positives on attached-value short flags.**

`git commit -m'no changes'` tokenizes to the single token `-mno changes`; the rule then finds `n` in `t[1:]` and blocks it as `--no-verify`. Any commit message containing the letter "n" is unusable in the attached `-m'…'` / `-am'…'` form. Fails closed, so it is an ergonomic cost rather than a hole, but the plan's matrix rows 54/58 claim messages mentioning `-n` are allowed, and no test covers the attached form.

**4. (minor) `.claude/hooks/stop_gate.sh:8` — the Stop gate fails open when the test runner is absent, and `.venv` as a directory is unprotected.**

`[ -x .venv/bin/pytest ] || exit 0` means a missing runner silently allows the turn to end. `bash_guard` protects the exact path `.venv/bin/pytest` but not its parent: `rm -rf .venv` and `mv .venv /tmp/v` both return exit 0 (verified). Matrix row 44 claims tampering with the runner is blocked; deleting the directory achieves the same effect and leaves the gate no-opping. (`pre-push` Gate 1 still fails closed here, so this is defence-in-depth erosion, not a full bypass.)

**5. (minor) `.githooks/pre-push:10` — the new remote-main deletion branch has zero test coverage.**

`tests/test_trust_hooks.py:566` covers the other side of the same `if` (feature-branch deletion skips gates), but nothing exercises `git push origin :main`. The plan states the validation matrix *is* the rollout's "Done when", and row 50 claims deletion of `refs/heads/main` is blocked at pre-push; only the `bash_guard` half of that row is tested.

## What checked out

`path_guard.py` (basename matching for worktree state files, `normpath` closing `../` traversal, the `ALWAYS_PROTECTED` vs `MAINTENANCE_PROTECTED` split), the stop-gate cache key (HEAD sha + tracked diff + untracked content, machinery paths included, exit-code truth with `</dev/null`), the refspec-aware push destination matching, the `gh` and `rm` rules routing through the shared `command_name()` helper, and the `agent-review.sh` 480→900 s bump all hold up. No existing test was deleted or weakened; `tests/test_trust_guards.py` is purely additive. The `COORDINATION.md` entry logs the shared-interface change as required. I checked the `reviews/` ledgers — none of these five findings is disputed there; findings 1 and 2 are distinct variants of the round-10/15 shell-grammar class rather than repeats of it.

```json
{"verdict": "request_changes", "findings": [{"severity": "critical", "file": ".claude/hooks/bash_guard.py", "line": 67, "issue": "A backslash-newline line continuation is preserved into the tokenizer, so shlex prefixes a literal newline onto the next token and every rule keyed on a flag or subcommand token (which test t.startswith('-') or compare exactly) silently fails to match.", "scenario": "Verified against real bash with a fake git on PATH: `git commit \\\\<newline>-n -m x` executes as `commit -n -m x`, `git push \\\\<newline>--force origin main` executes as `push --force origin main`, and `git push \\\\<newline>--no-verify origin feature` executes as `push --no-verify origin feature` — all three exit 0 from bash_guard, letting the agent skip pre-commit hooks, force-push main (the settings.json deny entry for --force was removed in this same diff and the remaining --no-verify glob no longer matches), and skip pre-push entirely, which is both the offline suite and the review backstop. Same result for `git \\\\<newline>commit -nm x` and `gh pr \\\\<newline>merge 14`. No CI layer exists yet (.github/ is absent), so nothing stands behind this."}, {"severity": "major", "file": ".claude/hooks/bash_guard.py", "line": 71, "issue": "The escape branch runs before the quote branch, so a backslash is honored as an escape inside single quotes (where POSIX defines none), desyncing quote tracking so the closing quote and the following newline are absorbed and later command lines are merged into one unanalyzed segment.", "scenario": "The two-line command `echo 'a\\\\'` + newline + `touch .commit-approved` exits 0 from bash_guard; executing it under bash actually creates the human-only .commit-approved sentinel (verified in a scratch dir). Quote desync also forces the shlex ValueError fallback to the quote-naive command.split(), so the merged segment's command name is the harmless first word and no write-verb, git, gh, or rm rule fires."}, {"severity": "minor", "file": ".claude/hooks/bash_guard.py", "line": 324, "issue": "The commit -n rule inspects attached-value flag tokens, so any message supplied in the attached form -m'...' that contains the letter n is blocked as --no-verify.", "scenario": "`git commit -m'no changes'` and `git commit -am'new feature'` tokenize to `-mno changes` / `-amnew feature`; t[1:] contains 'n' and the guard blocks a legitimate commit. Fails closed, but contradicts plan matrix rows 54/58, and no test covers the attached-value spelling."}, {"severity": "minor", "file": ".claude/hooks/stop_gate.sh", "line": 8, "issue": "The Stop gate exits 0 when .venv/bin/pytest is missing, and bash_guard protects only that exact path, not the .venv directory that contains it.", "scenario": "`rm -rf .venv` and `mv .venv /tmp/v` both return exit 0 from bash_guard (verified); afterwards the `[ -x .venv/bin/pytest ] || exit 0` early-out makes the Stop gate no-op on a red suite, which is exactly the self-blessing that plan matrix row 44 claims is closed for the runner path."}, {"severity": "minor", "file": ".githooks/pre-push", "line": 10, "issue": "The new refs/heads/main deletion branch has no test, although the plan's validation matrix (declared to be the rollout's 'Done when') lists blocking it at pre-push as part of row 50.", "scenario": "tests/test_trust_hooks.py:566 exercises only the opposite branch (feature-ref deletion skips gates); a future edit that reorders the case statement or drops the fail() call would let `git push origin :main` through with a green suite."}]}
```
