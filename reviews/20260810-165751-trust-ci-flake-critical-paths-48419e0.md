# Agent review — approve

date: 2026-08-10T17:07:00.877978
range: origin/main..HEAD
branch: trust/ci-flake-critical-paths
findings: 1

- **minor** `tests/test_trust_guards.py:832` — The success-path replay test asserts on git's literal word "warning:" in stderr, which is redundant with the adjacent `git: ` replay-prefix assertion and couples the offline gate to git's own diagnostic wording.
  - scenario: A CI runner image ships a git release that rewords or suppresses "exhaustive rename detection was skipped"; the assertion fails and the required offline-tests check goes red on every PR — a new flake introduced by a de-flaking branch. Fix: keep `res.stderr.count("git: ") >= 1` and `"warning:" not in res.stdout` (which together already prove git wrote to stderr and the replay stayed out of the scanned output) and drop the literal-word assertion, or log the raw stderr instead of asserting on it.

---

## Full review

I've verified every branch of the changed script by executing it directly against sandbox repos (pytest itself was denied approval in this non-interactive session, so I reproduced the fixtures in Python instead).

## What I verified rather than assumed

**Gate semantics are unchanged.** I re-ran the pre-existing behavioral contracts against the new script in fresh sandbox repos: `schema.py` without a label → 1, with → 0; `adapters/` with the wrong label → 1, `golden-verified` → 0; `web/scripts/app.js` → 0 (prefix still anchored); missing table → 1; non-ASCII + spaced path → 1; `run-qwen3vl/config/` → 1 without `golden-verified`. Adding `2>"$_cp_err"` cannot reach `$changed` — command substitution never captured stderr.

**All four new failure branches behave as the tests assert** (`scripts/check_critical_paths.sh:42-75`), driven live:
- bad base → `git exited 128` + `fatal: Invalid symmetric difference…` + the "exist in this checkout" hint;
- silent 137 → `KILLED by signal 9`, no shallow-clone hypothesis;
- noisy 128 → git's `error: unable to write file` echoed, hint suppressed;
- silent 128 → `printed nothing and exited 128`, no invented signal;
- `TMPDIR` pointing nowhere → `cannot create a temp file…`, exit **1** (fails closed), and *not* misreported as a base-sha failure.

Every one exits non-zero. `rc=$?` is the first statement in the `||` group, so it is git's status; the EXIT trap does not clobber it (all runs returned the intended code).

**The plan's central technical claim is true, and I tested it rather than reading it.** With `diff.renameLimit=1` and 40 decoy renames, `git mv src/va/cli.py src/va/cli_main.py` (plus edits) reports `D src/va/cli.py` + `A src/va/cli_main.py` — and the gate still demanded `human-reviewed`. A skipped rename detection is not a gate hole, and the warning reached stderr as `git: warning: exhaustive rename detection was skipped…` while stdout carried only the report line. That is exactly what `test_git_warnings_reach_the_log_without_entering_the_path_list` asserts, so the test constructs its scenario for real.

**Both WT.7 mirrors are byte-identical.** I re-implemented the new test's extraction independently: each marker occurs exactly once (plan lines 1805 and 1862), and both blocks match their files exactly. The recovery direction documented in the headers is file→plan, so restoring from the block can't inject the MIRROR header above the shebang. The table listing genuinely was stale — the file already had `run-qwen3vl/config/`, the plan didn't.

**Coverage of the new code is complete.** Last round's open minor (the `rc <= 128` arm and the hint's false arm with non-empty unrelated stderr) is closed by `test_a_non_signal_death_invents_neither_a_signal_nor_a_cause`, and its second case is deliberately built so the `if true` mutation fails — I confirmed silent-128 is the only path that reaches the signal wording.

**The strict `git()` fixture helper doesn't break the other cp_repo tests.** In the warning test the 40 content edits stay unstaged until the trailing `git add -A`, so no "nothing to commit" exit 1; I ran that exact sequence end to end. `test_missing_table_fails_closed` still exits at line 17, before `mktemp`.

**Documentation parity holds.** No new env vars, CLI flags, or config keys. The new mirror invariant is documented where an editor will actually see it — headers in both source files, plus the plan. `COORDINATION.md:250` and CLAUDE.md already carry the D9 "attestation, not proof" wording the script and plan now match; no contradiction introduced. Nothing role/backend/profile-shaped is touched, so no golden attestation is implied.

## Findings

**minor — `tests/test_trust_guards.py:832`** — `assert "warning:" in res.stderr` pins the suite to git's own stderr wording, and it's redundant. The line above it (`res.stderr.count("git: ") >= 1`) already proves git wrote to stderr and the replay fired; the literal-word assertion adds nothing but a dependency on `exhaustive rename detection was skipped…` continuing to be worded as a `warning:`. Failure scenario: a CI runner image ships a git that renames or drops that diagnostic, and the offline gate goes red for every PR on a branch whose entire purpose was removing a CI flake. Safe path: drop the literal-word assertion and keep the `git: ` replay-prefix count plus `"warning:" not in res.stdout` — or, if the literal is wanted as documentation, assert on the prefix and log the raw stderr instead of asserting it.

## Notes, not findings

- `.guard-override` is still present in the worktree. The previous round called this a finding; on the merits it is not one *yet* — `workflow-trust-plan.md:1194` says the human removes it **at finalize**, and this branch is still provisional (`need_agent_review:` subject, no `.commit-approved`). It remains yours to remove at finalize; leaving it relaxes `MAINTENANCE_PROTECTED` for every later session on this machine.
- "git printed nothing and was KILLED by signal N" is an inference (a program can `exit(137)` itself), but 128+N is the universal convention, the message hedges with the two common values, and the arm is tested. Not worth acting on.
- The plan states plainly that the root cause is unidentified and frames `gc.auto` as plausible rather than proven — the right side of the determinism-is-not-correctness rule. Occurrence #4 will now print git's actual words in stdout, which is what this change buys.
- `mktemp` with no template is GNU-only; consistent with the repo's existing `sed -i` in `.githooks/commit-msg`, so not a new portability constraint.
- This PR touches `scripts/`, so `critical-paths` will demand `human-reviewed` — yours to apply, and per D9 worth only the reading behind it.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "tests/test_trust_guards.py", "line": 832, "issue": "The success-path replay test asserts on git's literal word \"warning:\" in stderr, which is redundant with the adjacent `git: ` replay-prefix assertion and couples the offline gate to git's own diagnostic wording.", "scenario": "A CI runner image ships a git release that rewords or suppresses \"exhaustive rename detection was skipped\"; the assertion fails and the required offline-tests check goes red on every PR — a new flake introduced by a de-flaking branch. Fix: keep `res.stderr.count(\"git: \") >= 1` and `\"warning:\" not in res.stdout` (which together already prove git wrote to stderr and the replay stayed out of the scanned output) and drop the literal-word assertion, or log the raw stderr instead of asserting on it."}]}
```
