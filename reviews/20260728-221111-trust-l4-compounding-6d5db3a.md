# Agent review — request_changes

date: 2026-07-28T22:17:19.234227
range: origin/main..HEAD
branch: trust/l4-compounding
findings: 4

- **major** `.claude/hooks/bash_guard.py:347` — The raw token stream is built without the newline separator tokens that segments() injects, so a loop opener at the start of any line after the first fails the SEPARATORS precedence check and the guard never inspects it — re-introducing the documented round-15 'newlines are command separators' critical.
  - scenario: `echo starting\nuntil ! pgrep -f portscan.py; do sleep 5; done` is verified ALLOWED (exit 0) by the hook and hangs forever, while the same command spelled with `;` is blocked; multi-line commands are an ordinary agent shape, the guard's own comment and plan row 108 claim any-position coverage, and no test exercises a newline-separated loop, so the suite stays green over the gap.
- **minor** `.claude/hooks/bash_guard.py:366` — Stage dispatch reads _st[0] directly instead of command_name(), so an env-assignment prefix hides pgrep/ps from the guard — the exact round-13 class command_name() exists to prevent, and which _matcher_pattern already handles.
  - scenario: `until ! LC_ALL=C pgrep -f portscan.py; do sleep 5; done` and `until ! LC_ALL=C ps aux | grep portscan.py; do sleep 5; done` are both verified ALLOWED and both hang; the un-prefixed spellings are blocked.
- **minor** `.claude/hooks/bash_guard.py:346` — SEPARATORS includes do/then but not else/elif, so a self-observing loop opening an else-branch is never checked.
  - scenario: `if true; then echo hi; else until ! pgrep -f portscan.py; do sleep 5; done; fi` is verified ALLOWED (the token before `until` is `else`) and hangs forever.
- **minor** `.claude/hooks/bash_guard.py:370` — The grep self-exclusion carve-out matches only short-form -v (bare or clustered), not --invert-match, so the long-form self-exclusion idiom is falsely blocked as a matcher.
  - scenario: `until ! ps aux | grep "[m]yjob" | grep --invert-match grep; do sleep 5; done` — bracket-tricked and self-excluding, terminates normally — is verified BLOCKED on pattern 'grep'; every false block of a correct loop pushes toward the workaround behavior guards exist to prevent.

---

## Full review

Review complete. All prior-round fixes held up under re-probing (nested loops, sequential loops, `--full`, `grep -e`, `$VAR`, clustered flags, per-pipeline `ps` scoping, and the joined-line lessons budget are all genuinely fixed), and the WT.8 deliverables (`/lesson`, Lessons section, `test_lessons_section.py`) meet the plan's "Done when". The offline suite is green (452 passed, 2 skipped). But probing the loop guard in the current HEAD found one new major and three minors — all verified empirically by feeding commands through the hook itself.

## Findings

**1. major — `.claude/hooks/bash_guard.py:347` — the raw token stream drops newline separators, so a loop opener at the start of any line after the first is never detected.**
`_stream` is built by tokenizing each line and concatenating the results with nothing in between — unlike `segments()` directly above it, which appends a `"\n"` token per line precisely because "NEWLINES ARE COMMAND SEPARATORS" (documented there as the round-15 *critical*). `"\n"` is even listed in `SEPARATORS`, but the stream never contains one. Verified: `echo starting\nuntil ! pgrep -f portscan.py; do sleep 5; done` exits 0 (allowed) and hangs forever, while the identical command spelled with `;` is blocked. Multi-line commands are one of the most common shapes an agent emits — arguably more common than the `cd /tmp &&` prefix that was round 1's major — and the guard's own comment ("ANY segment may open the loop") plus plan row 108 claim exactly this coverage. No test exercises a newline-separated loop (the only multi-line BLOCKED/ALLOWED entries put the `\n` inside a quoted message). This is the fifth instance of the project's documented "documentation asserts a guarantee the code does not provide" pattern, and the fix is the same one `segments()` already embodies: `+ ["\n"]` per line.

**2. minor — `.claude/hooks/bash_guard.py:366` — stage dispatch reads `_st[0]` directly instead of using `command_name()`, so an env-assignment prefix disables the guard.**
Verified allowed (both hang): `until ! LC_ALL=C pgrep -f portscan.py; do sleep 5; done` (`_name` becomes `LC_ALL=C`, not `pgrep`) and `until ! LC_ALL=C ps aux | grep portscan.py; do sleep 5; done` (`_has_ps` at line 362 misses the prefixed `ps`, so the grep is treated as a file-grep). This is the exact class `command_name()`'s own docstring records from round 13 ("a rule that read seg[0] directly was disabled by an env prefix"), and inconsistently, `_matcher_pattern` *does* use `command_name()` — dispatch just never reaches it.

**3. minor — `.claude/hooks/bash_guard.py:346` — `SEPARATORS` includes `do`/`then` but not `else`/`elif`, so a loop in an else-branch is never checked.**
Verified allowed: `if true; then echo hi; else until ! pgrep -f portscan.py; do sleep 5; done; fi` exits 0 and hangs. The token before `until` is `else`, which fails the opener-precedence test, and there is no other opener occurrence to catch it.

**4. minor — `.claude/hooks/bash_guard.py:370` — the self-exclusion carve-out recognizes only short-form `-v`, so the long-form `--invert-match` idiom is falsely blocked.**
Verified: `until ! ps aux | grep "[m]yjob" | grep --invert-match grep; do sleep 5; done` — bracket-tricked *and* self-excluding, terminates fine — is blocked on pattern `'grep'`. The suggested `[g]rep` rewrite does work, which keeps this at friction level rather than dead-end, but per this branch's own lesson an over-broad guard is a defect too.

Everything else I probed dissolved: subshell-wrapped and backgrounded loops are caught, bracket-tricked patterns containing spaces survive the join/re-split, full-path `pgrep` is caught, `grep -m 5 -e pat` extracts the right pattern, and there are no contract breaks, test deletions, or silently hardcoded content in the range (the modified `src/va` files in the worktree are uncommitted and outside `origin/main..HEAD`).

```json
{"verdict": "request_changes", "findings": [
  {"severity": "major", "file": ".claude/hooks/bash_guard.py", "line": 347, "issue": "The raw token stream is built without the newline separator tokens that segments() injects, so a loop opener at the start of any line after the first fails the SEPARATORS precedence check and the guard never inspects it — re-introducing the documented round-15 'newlines are command separators' critical.", "scenario": "`echo starting\\nuntil ! pgrep -f portscan.py; do sleep 5; done` is verified ALLOWED (exit 0) by the hook and hangs forever, while the same command spelled with `;` is blocked; multi-line commands are an ordinary agent shape, the guard's own comment and plan row 108 claim any-position coverage, and no test exercises a newline-separated loop, so the suite stays green over the gap."},
  {"severity": "minor", "file": ".claude/hooks/bash_guard.py", "line": 366, "issue": "Stage dispatch reads _st[0] directly instead of command_name(), so an env-assignment prefix hides pgrep/ps from the guard — the exact round-13 class command_name() exists to prevent, and which _matcher_pattern already handles.", "scenario": "`until ! LC_ALL=C pgrep -f portscan.py; do sleep 5; done` and `until ! LC_ALL=C ps aux | grep portscan.py; do sleep 5; done` are both verified ALLOWED and both hang; the un-prefixed spellings are blocked."},
  {"severity": "minor", "file": ".claude/hooks/bash_guard.py", "line": 346, "issue": "SEPARATORS includes do/then but not else/elif, so a self-observing loop opening an else-branch is never checked.", "scenario": "`if true; then echo hi; else until ! pgrep -f portscan.py; do sleep 5; done; fi` is verified ALLOWED (the token before `until` is `else`) and hangs forever."},
  {"severity": "minor", "file": ".claude/hooks/bash_guard.py", "line": 370, "issue": "The grep self-exclusion carve-out matches only short-form -v (bare or clustered), not --invert-match, so the long-form self-exclusion idiom is falsely blocked as a matcher.", "scenario": "`until ! ps aux | grep \"[m]yjob\" | grep --invert-match grep; do sleep 5; done` — bracket-tricked and self-excluding, terminates normally — is verified BLOCKED on pattern 'grep'; every false block of a correct loop pushes toward the workaround behavior guards exist to prevent."}
]}
```
