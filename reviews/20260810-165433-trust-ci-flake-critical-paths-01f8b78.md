# Agent review — approve

date: 2026-08-10T17:03:14.623854
range: origin/main..HEAD
branch: trust/ci-flake-critical-paths
findings: 0


---

## Full review

I reviewed the single commit in `origin/main..HEAD` (`01f8b78`) — the critical-paths gate error-reporting change, its tests, and the WT.7 as-built. Unlike the earlier rounds recorded in `reviews/`, I was able to **execute the checker** in sandbox repos, so the behavioral claims below are run, not read.

## What I verified by running it, not by reading

- **All five new failure/success arms behave exactly as the tests assert.** Sandbox runs of `scripts/check_critical_paths.sh`:
  - unreachable base → rc 1, `git said: fatal: Invalid symmetric difference expression …`, plus the `exist in this checkout` hint; no `fetch-depth` wording.
  - `TMPDIR=<nonexistent>` → rc 1, `FAIL: cannot create a temp file …`, and **no** `cannot diff against` — fails closed with the right cause.
  - fake git exiting 137 silently → `git exited 137` / `KILLED by signal 9`, no `shallow` hint.
  - fake git exiting 128 with unrelated stderr → git's words replayed, **no** `exist in this checkout`.
  - fake git exiting 128 silently → `printed nothing and exited 128`, **no** `KILLED by signal`.
  This closes the previous round's minor: the `rc > 128` guard and the hint's false arm now both have tests that actually construct their scenario (the shim intercepts on the literal `diff` arg, and `git rev-parse --show-toplevel` still passes through, so setup can't silently miss).
- **The success-path replay works and does not enter the scanned list.** With 40 rename+edit pairs and `diff.renameLimit=1`, the run produced rc 0, stdout `ok: 'src/va/cli.py' touched, label 'human-reviewed' present`, and stderr `git: warning: exhaustive rename detection was skipped …`. No warning text in stdout.
- **The "skipped rename detection is not a gate hole" claim in the plan is true.** I reproduced it: with detection skipped, `--name-status` emits 80 records, statuses `{A, D}` — the old path is present, so `-M` only merges the pair when detection runs. The gate is not weakened by the warning path.
- **Both WT.7 mirrors are byte-identical right now.** I re-implemented the test's extraction (marker → header offset → closing fence) against the committed files: both compare equal, each marker occurs exactly once, neither marker is a prefix of the other. `run-qwen3vl/config/` was indeed present in the *file* and missing from the *plan* — the stale-mirror claim is accurate and the correction direction (file → plan) is the safe one.
- **`rc=$?` is git's status** (first statement in the `||` group; left side a plain assignment), and `$changed` / the prefix scan / both exit statuses are byte-for-byte unchanged from `origin/main` — adding `2>"$_cp_err"` cannot alter the verdict. The two new `#` lines in `critical_paths.txt` hit the `""|\#*) continue` arm.
- **Nothing else consumes the checker's output.** `pr-gates.yml:52` uses only the exit status; `.githooks/` doesn't reference the table at all; `path_guard.py`/`bash_guard.py` reference the paths, not their contents. No contract break, and no `COORDINATION.md` interface entry is owed.
- **Test integrity:** no deletions in the range (`--diff-filter=D` is empty), no assertion weakened. `test_large_changeset_does_not_fail_open` was *strengthened* (stderr now in the failure detail). The newly-strict fixture `git()` can't turn healthy runs red — I walked every `cp_repo` call site; each `change()`/`git_mv()` stages genuinely new content, and in the warning test the 40 edits stay unstaged until the closing `git add -A`, which also has 39 other files to commit.
- **Documentation parity:** no new env vars, CLI flags, config keys, or harness modes. The one new invariant (the WT.7 mirror must stay byte-identical) is documented in the owning plan *and* as a header in both source files, which is where a maintainer hits it. `CLAUDE.md` and `COORDINATION.md:252` already carry the D9 "attestation, not proof" framing the script/table/plan now match — the change removes a contradiction rather than creating one.
- **Combination coverage:** nothing role/backend/profile-shaped is touched, so no golden attestation is implied. This PR touches `scripts/`, so `critical-paths` will demand `human-reviewed` — yours to apply, and per D9 it is worth only the reading behind it.
- **Repo-rule conformance:** the as-built states plainly that the root cause is *not* identified and frames `gc.auto` as plausible rather than proven — the right side of the determinism-is-not-correctness rule. No hardcoded content, no best-effort role made able to abort.

I could **not** run the pytest suite — every `pytest` invocation required approval in this non-interactive session — so the suite's green state is unverified by me. The checker's runtime behavior, which is where the risk lives, I verified directly.

## Findings

None at critical or major severity, and nothing minor I can support after verification. The two minors from the previous round are resolved or out of range: the `rc <= 128` / hint-false-arm coverage gap is closed by `test_a_non_signal_death_invents_neither_a_signal_nor_a_cause` (I confirmed both of its cases produce the asserted output), and `.guard-override` is worktree state outside `origin/main..HEAD`.

## Notes, not findings

- **`.guard-override` is still present in the worktree** (created 13:58). It's outside this review's declared range and the branch is still mid-loop, so it may legitimately still be in use — but `path_guard.py` drops `MAINTENANCE_PROTECTED` while it exists, so it needs your `rm` once the script edits are final.
- **Residual, pre-existing, not introduced here:** the pattern loop feeds itself through a here-document, which bash materializes under `$TMPDIR`. The new `mktemp` guard closes the case where `TMPDIR` is bad *at startup*, but if `/tmp` fills between line 42 and line 96 the here-doc redirect fails, the loop body is skipped, `hit` stays 0, and the gate exits 0 — fail-open. Narrow enough that I don't think it's worth acting on in this change, but it's the one remaining fail-open in the file.
- `test_git_warnings_reach_the_log_without_entering_the_path_list` is the only assertion here that depends on git's own output text (`warning:`). It's locale-pinned with `LC_ALL=C` and the wording is stable, so I don't count it as a new flake source, but in a de-flaking branch it's the assertion most exposed to a git upgrade.
- The plan mirror places three comment lines *above* `#!/usr/bin/env bash`, so the block isn't directly copy-pasteable back into an executable file. The plan says the file wins in a disagreement, so the documented recovery direction is the safe one; noting it only because the block reads like a restore source.

```json
{"verdict": "approve", "findings": []}
```
