# Agent review — request_changes

date: 2026-07-27T23:54:39.889179
range: origin/main..HEAD
branch: trust/l0-session-guards
findings: 2

- **major** `.claude/hooks/bash_guard.py:159` — VALUE_FLAGS includes -S/--gpg-sign and -o/--output, which for `git commit` take an optional attached-only argument (-S) or no separate-token argument (-o=--only), so rule 5 wrongly swallows the following token; when that token is -n/--no-verify the hook-skip check never sees it.
  - scenario: `git commit -S -n -m x`, `git commit --gpg-sign -n -m x`, `git commit -S -nm x`, and `git commit -o -n -m x` all exit 0 (allowed) from the committed guard, while `git commit -n -m x` is correctly blocked — the standalone -S/-o form lets an agent run `--no-verify`, skipping the pre-commit and commit-msg trust gates (P6); settings.json's deny only matches the literal `--no-verify`, not `-n`, so bash_guard is the sole barrier and it is bypassed.
- **minor** `tests/test_trust_guards.py:38` — BLOCKED_COMMANDS tests -n only after genuine arg-taking value flags (-m/-F), never after optional-arg/no-arg flags (-S/--gpg-sign/-o), so the suite stays green across the entire VALUE_FLAGS bypass class.
  - scenario: The 148-passing guard suite certifies a guard that allows `git commit -S -n -m x`, so a reader concludes the -n invariant is fully covered and does not re-test the signing/only combinations.

---

## Full review

Exit code 0, no block — the bypass is confirmed. I have enough to finalize the review.

## Review: `origin/main..HEAD` — WT.3 session guards

I verified the two round-12 findings are genuinely fixed: subshell/brace-wrapped writes (`( touch .commit-approved )`), and the `>|` / `>&` / `&>>` write-redirect operators are all now blocked. Everything the prior ledger re-checked (quoted-separator segmentation, force-push spellings, `--mirror`/`--all`/`:main`/`--delete`, pre-push `refs/heads/main` deletion guard, path_guard override scoping, stop-gate cache key) held under my probing too.

But I found one new bypass of the guard's own stated `-n`/`--no-verify` invariant.

### Finding: `VALUE_FLAGS` over-broadly consumes the token after optional-arg / no-arg git flags, letting a following `-n` escape the hook-skip check

`.claude/hooks/bash_guard.py:159` (`VALUE_FLAGS`) includes `-S`/`--gpg-sign` and `-o`/`--output`. In rule 5 (`bash_guard.py:236-242`), any token in `VALUE_FLAGS` sets `skip=True`, so the guard swallows the *next* token as that flag's argument. But for `git commit`:

- `-S` / `--gpg-sign` takes an **optional** key that git only accepts *attached* (`-Skeyid`); a standalone `-S` does **not** consume the following argument.
- `-o` is `--only` (and `--output` is not a `git commit` flag at all); neither takes a separate-token argument.

So when `-n`/`--no-verify` follows one of these standalone flags, it lands in the swallowed slot and the "hooks must run" check never sees it. Verified against the committed guard (exit 0 = allowed):

| command | guard result | reality |
|---|---|---|
| `git commit -n -m x` | **blocked** ✓ | baseline works |
| `git commit -S -n -m x` | **allowed** ✗ | signs + `--no-verify` |
| `git commit --gpg-sign -n -m x` | **allowed** ✗ | signs + `--no-verify` |
| `git commit -S -nm x` | **allowed** ✗ | signs + `--no-verify` |
| `git commit -o -n -m x` | **allowed** ✗ | `--only` + `--no-verify` |

`git commit -Skey -n` (attached key) *is* still blocked, which confirms the mechanism: it's specifically the standalone spelling of these flags that opens the hole.

**Failure scenario:** an agent runs `git commit -S -n -m 'need_agent_review: x'` (or `-o -n`). `--no-verify` skips the pre-commit and commit-msg trust hooks — the exact P6 invariant the guard exists to enforce ("gates have no agent override"). `.claude/settings.json`'s deny list only matches the literal `git commit --no-verify*`, not `-n`, so bash_guard is the sole barrier for the `-n` spelling, and it is bypassed. This is the same bug class rounds 7–9 repeatedly closed for `-n`, reintroduced by the VALUE_FLAGS remedy — and the latest ledger affirmatively claimed "value-flag consumption … held," having only tested real arg-taking flags (`-m x -n`, `-F /tmp/m -n`), never the optional-arg/no-arg cases. Severity **major**: reproducible defeat of the hook-skip guard via ordinary, documented flags (commit-signing is routine), though pre-push remains a downstream backstop and CI (WT.5) is not yet built.

The fix is on the guard side: `-S`/`--gpg-sign`/`-o`/`--output` should not be treated as separate-token value flags (or the skip should not fire when the consumed token itself is `-n`/`--no-verify`), and `BLOCKED_COMMANDS` in `tests/test_trust_guards.py:38` should gain `git commit -S -n …` / `-o -n …` cases — the suite is green over this entire bypass, repeating the "tables encode the rules' shape, not the invariant" pattern earlier rounds flagged.

```json
{"verdict": "request_changes", "findings": [{"severity": "major", "file": ".claude/hooks/bash_guard.py", "line": 159, "issue": "VALUE_FLAGS includes -S/--gpg-sign and -o/--output, which for `git commit` take an optional attached-only argument (-S) or no separate-token argument (-o=--only), so rule 5 wrongly swallows the following token; when that token is -n/--no-verify the hook-skip check never sees it.", "scenario": "`git commit -S -n -m x`, `git commit --gpg-sign -n -m x`, `git commit -S -nm x`, and `git commit -o -n -m x` all exit 0 (allowed) from the committed guard, while `git commit -n -m x` is correctly blocked — the standalone -S/-o form lets an agent run `--no-verify`, skipping the pre-commit and commit-msg trust gates (P6); settings.json's deny only matches the literal `--no-verify`, not `-n`, so bash_guard is the sole barrier and it is bypassed."}, {"severity": "minor", "file": "tests/test_trust_guards.py", "line": 38, "issue": "BLOCKED_COMMANDS tests -n only after genuine arg-taking value flags (-m/-F), never after optional-arg/no-arg flags (-S/--gpg-sign/-o), so the suite stays green across the entire VALUE_FLAGS bypass class.", "scenario": "The 148-passing guard suite certifies a guard that allows `git commit -S -n -m x`, so a reader concludes the -n invariant is fully covered and does not re-test the signing/only combinations."}]}
```
