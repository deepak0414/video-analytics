# Agent review — approve

date: 2026-08-10T16:50:54.643815
range: origin/main..HEAD
branch: trust/ci-flake-critical-paths
findings: 2

- **minor** `scripts/check_critical_paths.sh:60` — The newly-added `rc <= 128` arm (the fix for last round's fabricated-signal message) has zero test coverage, as does the base-sha hint's false arm when git's stderr is non-empty but unrelated to the revision.
  - scenario: A later refactor drops the `if [ "$rc" -gt 128 ]` guard while trimming the comment-heavy branch; occurrence #4 fires as a disk-full exit 128 and the operator reads 'git printed nothing and was KILLED by signal 0' - the exact fabricated cause this change exists to remove - while the suite stays green. Fix: add a second fake-git case exiting 128 with an unrelated stderr line, asserting 'exited 128' appears and neither 'KILLED by signal' nor 'exist in this checkout' does.
- **minor** `.guard-override:1` — The human-only guard-override sentinel is still present in the worktree after the gate-maintenance commit it enabled; CLAUDE.md requires removing it afterwards.
  - scenario: path_guard.py:53 drops MAINTENANCE_PROTECTED (.githooks/, .claude/, .github/workflows/, trust scripts) while the file exists, so the next agent session starts with the machinery-write guards already relaxed and can edit bash_guard.py or check_critical_paths.sh without anyone deciding to open the cage. Fix: `rm .guard-override` (human-only) once this branch's script edits are final.

---

## Full review

I reviewed the single commit in `origin/main..HEAD` (`950ab7e`) — the critical-paths gate error-reporting change plus its tests and plan as-built.

## What I verified rather than assumed

- **Gate semantics are unchanged.** `raw=$(…)` captured stdout only before and after; adding `2>"$_cp_err"` cannot alter `$changed`, the prefix scan, or either exit status. The two new `#` lines in `critical_paths.txt` hit the `""|\#*) continue` arm.
- **`rc=$?` is correct** — first statement in the `||` group, left side a plain assignment, so it is git's status.
- **Both WT.7 mirrors are byte-identical.** I re-did the extraction the new test performs by line arithmetic: table body `workflow-trust-plan.md:1808–1851` ↔ `critical_paths.txt:1–44`; script body `1866–1982` ↔ `check_critical_paths.sh:1–117`. Each marker occurs once and neither is a prefix of the other. The documented recovery direction is file→plan, so copying the plan block back cannot inject the 3-line MIRROR header above the shebang.
- **Every new test constructs its scenario and fails loudly if setup doesn't take.** If the fake-git shim missed PATH, or `mktemp` unexpectedly succeeded under a bad `TMPDIR`, the diff would succeed and `returncode == 1` would fail first — none of these assertions can pass vacuously.
- **The strict `git()` helper doesn't break the other ten `cp_repo` tests.** I walked each: every `change()`/`git_mv()` stages new or genuinely-modified content, so no "nothing to commit" exit 1. In the warning test the 40 content edits stay unstaged until the later `git add -A`.
- **`test_missing_table_fails_closed` still exits before `mktemp`** (table check is line 17, mktemp line 42), so no trap/temp interaction.
- **Documentation parity holds.** No new env vars, CLI flags, or config keys. The mirror invariant is documented in both source-file headers and the plan; `COORDINATION.md:252` and `CLAUDE.md` already carry the D9 "attestation, not proof" wording the script now matches — no contradiction introduced.
- **Combination coverage:** nothing role/backend/profile-shaped is touched, so no golden attestation is implied — `scripts/` maps to `human-reviewed` only. GNU-only `mktemp` (no template) is consistent with the repo's existing `sed -i` in `.githooks/commit-msg`, so it's not a new portability constraint.

I could not run pytest or the checker — Bash was denied for anything beyond simple git reads, and a full suite was already live (`pgrep` showed pid 4082253), so I deliberately did not add load. Green state is unverified by me; everything above is static.

## Findings

**1. minor — `scripts/check_critical_paths.sh:60` — the `rc <= 128` arm added to fix the last round's fabricated-signal message is itself untested.** `test_a_silent_git_death_is_not_blamed_on_the_base_sha` only exercises `rc=137`, and no test drives a git failure with empty stderr and a status ≤ 128. The same conditional's sibling gap: the `grep -qE` hint's false arm is only reached through an *empty* stderr file, so "git spoke, but not about the revision" is never exercised either. Failure scenario: a later refactor drops the `if [ "$rc" -gt 128 ]` guard while trimming the comment-heavy branch; occurrence #4 fires as a disk-full exit 128, the operator reads *"git printed nothing and was KILLED by signal 0"* — the exact fabricated cause this change exists to remove — and the whole suite stays green. Safe path: extend the fake-git shim test with a second case exiting `128` and writing an unrelated line to stderr, asserting `"exited 128"` appears, `"KILLED by signal"` does not, and `"exist in this checkout"` does not.

**2. minor — repo root — `.guard-override` is still present in the worktree after the gate-maintenance commit.** CLAUDE.md's rule is `touch .guard-override` for gate maintenance, "remove it after"; `path_guard.py:53` drops `MAINTENANCE_PROTECTED` (`.githooks/`, `.claude/`, `.github/workflows/`, the trust scripts) from the protected set while it exists. This change is precisely the gate maintenance that required it, and it was not cleaned up. Failure scenario: the next agent session in this repo starts with the machinery-write guards already relaxed and edits `.claude/hooks/bash_guard.py` or `scripts/check_critical_paths.sh` without anyone deciding to open the cage — the block that would have surfaced the change never fires. Safe path: `rm .guard-override` (human-only) once this branch's script edits are final.

## Notes, not findings

- `test_git_warnings_reach_the_log_without_entering_the_path_list` is the one assertion in this change that depends on git's own output text (`warning:` from a skipped inexact-rename pass, forced by `diff.renameLimit=1` with 40×40 > 1×1). It's locale-pinned via `LC_ALL=C` and the wording has been stable for many releases, so I don't count it as a new flake source — but in a branch whose purpose is de-flaking, it's the assertion most exposed to a git upgrade. The two tests that build their own `env` omit `LANGUAGE=""`; harmless, since both assert only on the *checker's* wording, and `LANGUAGE` is ignored under a C locale anyway.
- The as-built's "What changed" paragraph doesn't mention the `mktemp` fail-closed guard, but the script's own comment block does — and that block is mirrored verbatim into the plan, so the plan does carry it. No parity gap.
- The plan states plainly that the root cause is unidentified and frames `gc.auto` as plausible rather than proven, which is the right side of the determinism-is-not-correctness rule.
- Pre-existing, not introduced here: if the `2>"$_cp_err"` redirect itself fails after a successful `mktemp` (tmp-reaper plus a full disk), bash skips git and returns 1, which this branch would report as "git exited 1". Bash's own message still reaches the log, so the misattribution is bounded — too narrow to act on.
- This PR touches `scripts/`, so `critical-paths` will demand `human-reviewed` — yours to apply, and per D9 it is worth only the reading behind it.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "scripts/check_critical_paths.sh", "line": 60, "issue": "The newly-added `rc <= 128` arm (the fix for last round's fabricated-signal message) has zero test coverage, as does the base-sha hint's false arm when git's stderr is non-empty but unrelated to the revision.", "scenario": "A later refactor drops the `if [ \"$rc\" -gt 128 ]` guard while trimming the comment-heavy branch; occurrence #4 fires as a disk-full exit 128 and the operator reads 'git printed nothing and was KILLED by signal 0' - the exact fabricated cause this change exists to remove - while the suite stays green. Fix: add a second fake-git case exiting 128 with an unrelated stderr line, asserting 'exited 128' appears and neither 'KILLED by signal' nor 'exist in this checkout' does."}, {"severity": "minor", "file": ".guard-override", "line": 1, "issue": "The human-only guard-override sentinel is still present in the worktree after the gate-maintenance commit it enabled; CLAUDE.md requires removing it afterwards.", "scenario": "path_guard.py:53 drops MAINTENANCE_PROTECTED (.githooks/, .claude/, .github/workflows/, trust scripts) while the file exists, so the next agent session starts with the machinery-write guards already relaxed and can edit bash_guard.py or check_critical_paths.sh without anyone deciding to open the cage. Fix: `rm .guard-override` (human-only) once this branch's script edits are final."}]}
```
