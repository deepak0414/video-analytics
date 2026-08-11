# Agent review — approve

date: 2026-08-10T14:57:13.886130
range: origin/main..HEAD
branch: trust/ci-flake-critical-paths
findings: 1

- **minor** `workflow-trust-plan.md:2003` — The as-built note (and the commit body) rules out the shallow-checkout hypothesis on the grounds that the job checks out with fetch-depth: 0, but that is the critical-paths job; the job that actually flakes is offline-tests, whose checkout step has no fetch-depth and is therefore a depth-1 shallow clone.
  - scenario: Occurrence #4 happens; the next session reads 'Do not re-test these: shallow checkout (already fetch-depth: 0)', opens .github/workflows/offline-tests.yml, finds a bare actions/checkout@v4 with no fetch-depth, and either re-opens the eliminated hypothesis or discounts the whole ruled-out list — the exact wasted round the note was written to prevent. Fix: state the reason that actually applies — test_large_changeset_does_not_fail_open builds its own `git init` sandbox under tmp_path, so the outer checkout's depth cannot reach the base sha the checker is given.

---

## Full review

I reviewed the single commit in `origin/main..HEAD` (`d1dc5ce`), read the shipped checker, both CI workflows, the `cp_repo` fixture and every test that uses it, the WT.7 plan section, and the five prior branch reviews.

## Verification I ran

- **The plan's two WT.7 mirrors are byte-identical to their source files.** I extracted both blocks with the same header-offset/closing-fence logic the new test uses and compared: `scripts/check_critical_paths.sh` ✓, `scripts/critical_paths.txt` ✓. The prior review's minor (stale table mirror, missing `run-qwen3vl/config/`, pre-D9 header) is genuinely fixed, and the test now covers both blocks.
- **The new warning test's premise holds empirically.** The prior reviewer flagged that they could not confirm git emits a rename-limit warning for `--name-status`. I built the exact scenario in a scratch repo (40 rename+edit pairs, `diff.renameLimit=1`, `LC_ALL=C`) on git 2.43.0: stderr carries `warning: exhaustive rename detection was skipped due to too many files.` and stdout is `D f<i>.txt` / `A g<i>.txt` pairs. So both stderr assertions have something to bite on, the wording assertion (`"warning:"`) is version-robust across git's `inexact`→`exhaustive` rename of that message, and the "not a gate hole" claim in the comments is directly confirmed by the `D`/`A` output.
- **The fixture's new `assert returncode == 0` breaks no existing call site.** I traced every `change()`/`git_mv()`; the one that looks risky — the trailing `change("g0.txt", <content g0 already has>)` — still commits, because the 39 other `g<i>.txt` edits are unstaged at that point and `git add -A` sweeps them in. My repro exercised that exact sequence.
- **No weakening of the gate.** `$raw` is unchanged in both the old and new form (command substitution never captured stderr), `mktemp` failure degrades to a bad redirect → non-zero → the FAIL branch, and the `trap … EXIT` does not perturb `exit "$missing"`.
- **Could not run pytest** — sandbox denied it, and a suite was already live (PID 2046189). So the tests are verified by construction and by the git-level repro, not by execution.

## Finding — one minor

The eliminated-hypothesis reasoning is attached to the wrong job. `workflow-trust-plan.md:1990` says the fetch-depth hypothesis "could never have applied to the only automated caller" because "the `critical-paths` job already checks out with `fetch-depth: 0`", and `workflow-trust-plan.md:2003` lists "shallow checkout (already fetch-depth: 0)" under **Do not re-test these**. But the same note (correctly) establishes that all three failures were in **offline-tests** — and `.github/workflows/offline-tests.yml:29` is a bare `- uses: actions/checkout@v4` with no `fetch-depth`, i.e. a depth-1 shallow clone. The commit body carries the same inconsistency.

The conclusion is still right, for a different reason: `test_large_changeset_does_not_fail_open` builds its own repo with `git init` under `tmp_path`, so the outer checkout's depth cannot reach the sha the checker is handed. That is the reason worth recording — a next session that reads "already fetch-depth: 0", opens `offline-tests.yml`, and finds no such setting will either re-open the ruled-out question or stop trusting the rest of the ledger, which is precisely the failure mode this note exists to prevent.

## Not scored

The `header_lines = 3` / `2` offsets in `test_wt7_embedded_mirror_matches_the_shipped_checker` are hardcoded, so growing a mirror header produces a failure whose message ("re-copy the file into the block") won't fix it. Anchoring on the `#!/usr/bin/env bash` line would be self-maintaining. Fail-closed either way; the prior review already raised it as a note and I agree it isn't a finding.

For the human: this PR touches `scripts/`, so `critical-paths` will demand `human-reviewed` — yours to apply. `.guard-override` is still present in the worktree.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "workflow-trust-plan.md", "line": 2003, "issue": "The as-built note (and the commit body) rules out the shallow-checkout hypothesis on the grounds that the job checks out with fetch-depth: 0, but that is the critical-paths job; the job that actually flakes is offline-tests, whose checkout step has no fetch-depth and is therefore a depth-1 shallow clone.", "scenario": "Occurrence #4 happens; the next session reads 'Do not re-test these: shallow checkout (already fetch-depth: 0)', opens .github/workflows/offline-tests.yml, finds a bare actions/checkout@v4 with no fetch-depth, and either re-opens the eliminated hypothesis or discounts the whole ruled-out list — the exact wasted round the note was written to prevent. Fix: state the reason that actually applies — test_large_changeset_does_not_fail_open builds its own `git init` sandbox under tmp_path, so the outer checkout's depth cannot reach the base sha the checker is given."}]}
```
