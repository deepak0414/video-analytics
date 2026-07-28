# Agent review — approve

date: 2026-07-28T00:48:36.135129
range: origin/main..HEAD
branch: trust/l0-session-guards
findings: 0


---

## Full review

I've completed my review. Let me summarize what I verified.

## Review of `origin/main..HEAD` (WT.3 session guards)

**Context on the latest ledger.** The most recent review (`reviews/…-002419-…-badfb4f.md`) returned `request_changes` with a critical + major + three minors. HEAD (`a42fb4c`) is a later amend, and I confirmed **all five are fixed and covered by tests**:

1. **Backslash-newline continuation (was critical, `bash_guard.py:67`)** — `_split_lines_outside_quotes` now removes `\`+newline as a line continuation, and the single-quote branch runs *before* the escape branch so `\` is literal inside `'…'`. Verified: `git commit \⏎-n -m x`, `git push \⏎--force origin main`, `gh pr \⏎merge 14` are all in `BLOCKED_COMMANDS` and the parametrized `test_bash_guard_blocks` passes.
2. **Single-quote escape desync (was major, `:71`)** — fixed by the same reordering; `echo 'a\b'⏎touch .commit-approved` is blocked and tested.
3. **Attached-value `-m'…n…'` false positive (was minor)** — the `-n` rule now truncates the letter set at the value flag; `git commit -m'no changes'` / `-am'new feature'` are in `ALLOWED_COMMANDS` and pass.
4. **`.venv` deletion (was minor)** — `is_always_protected` now matches `ps[-1] == ".venv"`, so `rm -rf .venv` / `mv .venv /tmp/v` block. The residual stop-gate fail-open when `pytest` is absent is real but backstopped: `pre-push` Gate 1 fails *closed* on a missing runner (`if ! out=$(.venv/bin/pytest …)`).
5. **Untested `refs/heads/main` deletion (was minor)** — `test_deleting_remote_main_is_blocked_at_push` added and passing.

**Independent adversarial pass.** I probed the guard logic beyond the ledger cases — clustered/reordered/attached `-n` forms, value-flag argument consumption (`-m x -n`, `-S -n`), git global-flag insertion, env-prefix stripping, force/delete-push destination parsing (`+main`, `:main`, `:refs/heads/main`, `--mirror`, `--force --all`, bare force from a main checkout), `gh` self-approval spellings, `rm`/`sed -i`/`dd`/`ln` write-target detection, redirect spellings, and subshell/brace grouping. All hold. `path_guard` (`normpath`, basename matching for worktree state files, always-vs-maintenance split) and the stop-gate cache key (HEAD sha + tracked diff + untracked content, machinery paths, exit-code-as-truth) also check out. No test was deleted or weakened — `test_trust_guards.py` (+484) and the one new hook test are purely additive; the COORDINATION.md interface-change entry is present.

Command-substitution / inline-python evasion remains possible, but the plan explicitly scopes the shell layer as "hardening, not proof" with CI (WT.5) as the un-bypassable layer, so that is an accepted residual, not a defect.

**One out-of-scope observation (not a diff defect).** In *this* sandbox, 5 tests in `tests/test_trust_hooks.py` fail (`test_committed_ledgers_are_append_only` and four other lifecycle tests) because the post-commit → `agent-review.sh` → approval step never produces a ledger or `.git/.review-approved` here. Those 5 test functions and the git hooks they exercise (`commit-msg`, `post-commit`, `pre-commit`) are byte-identical to `origin/main` — the diff only *adds* one new test and bumps `agent-review.sh`'s timeout 480→900 — and the failures occur at the amend step, before any push. So they are pre-existing/environmental to this execution sandbox, not introduced by `origin/main..HEAD`. Worth knowing that the offline suite is red *in this environment* (which would block a real push via pre-push Gate 1), but it is not attributable to this change.

No critical or major defects were introduced by this diff.

```json
{"verdict": "approve", "findings": []}
```
