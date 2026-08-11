# Agent review — approve

date: 2026-08-10T16:36:51.877326
range: origin/main..HEAD
branch: trust/ci-flake-critical-paths
findings: 2

- **minor** `scripts/check_critical_paths.sh:55` — The empty-stderr failure branch prints "KILLED by signal $((rc - 128))" for any failure with no git output, without checking that rc is actually >128, so a non-signal failure gets a fabricated (and impossible) signal number.
  - scenario: The runner's disk fills during the diff: git cannot write its fatal: line, exits 128, and the operator reads 'git printed nothing. Status >128 means it was KILLED by signal 0 - 137 is the OOM killer, 141 is SIGPIPE.' - a presumed cause under an empty message, which is exactly the misattribution this change exists to remove. Fix: guard the signal wording with `if [ "$rc" -gt 128 ]` and otherwise print a plain 'git printed nothing and exited $rc'.
- **minor** `tests/test_trust_guards.py:672` — The new conditional base-sha hint is asserted only in the negative (absent for a silent death, 'fetch-depth' absent for an unreachable base); no test asserts it is PRINTED in the case where git's words do implicate the revision, so its true arm has zero coverage.
  - scenario: A later edit narrows or typos one alternative in the `unknown revision|bad object|Invalid symmetric difference|ambiguous argument|no merge base` pattern list, or drops the block while trimming the comment-heavy branch; occurrence #4 fires on a force-pushed base branch, the operator sees git's raw fatal: with no pointer to what the checker actually needs, and the whole suite stays green. Fix: add `assert "exist in this checkout" in res.stdout` to test_unreachable_base_reports_gits_own_error, which already reproduces that scenario.

---

## Full review

# Agent review — approve

**Range:** `origin/main..HEAD` (1 commit, `4c9c96e`) — `scripts/check_critical_paths.sh`, `scripts/critical_paths.txt`, `tests/test_trust_guards.py`, `tests/test_trust_hooks.py`, `workflow-trust-plan.md`.

## What I verified rather than assumed

- **Both WT.7 mirrors really are byte-identical.** I re-implemented the extraction the new test performs (marker line + header offset → closing fence) and ran it against the committed files: both blocks match, each marker occurs exactly once, and the two markers can't cross-match (`# MIRROR of scripts/check_critical_paths.sh` vs `…/critical_paths.txt` — neither is a prefix of the other). Working tree is clean for all three paths and there is no `.gitattributes`, so CI sees what I compared.
- **The mirror test actually runs in CI.** `tests/test_trust_hooks.py` has no `pytestmark`, no module-level `skipif`, and no `golden` marker, so `-m "not golden"` collects it.
- **The stale-table claim is true and load-bearing.** `scripts/critical_paths.txt` already carried `run-qwen3vl/config/`; only the plan's listing lacked it. Copying the plan block over the file — the documented failure mode — would have dropped a whole config dir out of `golden-verified` enforcement.
- **No gate semantics changed.** `raw=$(…)` captured stdout only before and after; `$changed`, the prefix scan and both exit statuses are unchanged. The two new `#` lines in the table hit the `""|\#*) continue` arm.
- **The mktemp guard closes a latent fail-open.** The pattern loop feeds itself through a here-document, which bash materializes under `$TMPDIR`; a bad `TMPDIR` would have made that redirect fail, skipping the loop body and exiting 0 on every critical path. The new `mktemp || exit 1` runs first, and `test_a_temp_file_failure_is_named_and_fails_closed` pins it.
- **`rc=$?` is correct.** It is the first statement in the `||` group and the left side is a plain assignment, so `$?` is git's status, not a later command's.
- **The warning test constructs its scenario.** 40 rename+edit pairs with `diff.renameLimit=1` forces inexact detection to be skipped (`40×40 > 1×1`), so git warns; the `git: ` prefix assertion is what would catch a regression to `2>&1`, and `src/va/cli.py` is committed specifically so the "stays out of stdout" assertion isn't vacuous.
- **The newly-strict fixture `git()` doesn't break the other tests.** I walked all eleven `cp_repo` tests: the 40 content edits stay unstaged until a later `git add -A`, `git_mv` commits a staged rename each time, and no test re-commits identical content — so no "nothing to commit" exit 1.
- **Previous round's finding is closed.** `rc` is now printed, and the base-sha hint is gated on git's stderr actually naming a revision.
- **Honest scope, no repo-rule violations.** The as-built states the root cause is unidentified and frames `gc.auto` as plausible, not proven; no role code, no hardcoded content, no best-effort role touched. The commit body matches the diff, including the self-correction about which job lost the evidence.

I could not run pytest or the checker directly — both invocations were denied in this session — so the suite's green state is unverified by me; everything above is static or reproduced by the extraction script above.

## Findings

**1. minor — `scripts/check_critical_paths.sh:55` — the empty-stderr branch asserts "killed by signal" for every empty-stderr failure, including ones that aren't signals.** The arm is entered on `[ -s "$_cp_err" ]` being false regardless of `rc`, and prints `$((rc - 128))` unguarded. Concrete case: a full disk during the diff — git can't write its `fatal:` line, exits 128, and the operator reads *"git printed nothing. Status >128 means it was KILLED by signal 0 — 137 is the OOM killer…"*, a cause that is both wrong and impossible (there is no signal 0). That is the same presume-a-cause defect the rest of this change removes, relocated one branch over. Safe path: wrap the signal wording in `if [ "$rc" -gt 128 ]` and fall through to a plain `"git printed nothing and exited $rc"` otherwise.

**2. minor — `tests/test_trust_guards.py:672` — the base-sha hint's *true* arm has zero coverage.** The new `grep -qE "unknown revision|bad object|…"` conditional is asserted only in the negative (`"shallow" not in res.stdout` for the 137 case, `"fetch-depth" not in res.stdout` for the unreachable base). `test_unreachable_base_reports_gits_own_error` is exactly the scenario where the hint must fire — git answers `fatal: Invalid symmetric difference expression …`, which matches — but nothing checks it appears. Failure scenario: a later edit narrows or typos one alternative in that pattern list (or drops the block while "simplifying" the comment-heavy branch); occurrence #4 fires on a force-pushed base, the operator gets git's raw `fatal:` with no pointer to what the checker actually needs, and the whole suite stays green. Safe path: add one assertion to that test — `assert "exist in this checkout" in res.stdout`.

## Notes, not findings

- `COORDINATION.md:252` still says agents are "guard-blocked from applying it" — but it already carries the D9 qualifier ("this is defense-in-depth, NOT a guarantee: agent sessions share the human's credential"), so it agrees with the corrected wording in the script, the table and the plan. No parity gap.
- No new env vars, CLI flags or config keys; the mirror invariant is documented in the owning plan doc *and* as a header in both source files, which is where a maintainer hits it. CLAUDE.md's gate description remains accurate.
- The `diff.renameLimit=1` test asserts on git's `warning:` prefix. It's locale-pinned via `LC_ALL=C` and the prefix has been stable for many releases, so I don't consider it a new flake source — but it is the one assertion in this change that depends on git's own output text.
- This PR touches `scripts/`, so `critical-paths` will demand `human-reviewed` — yours to apply, and per D9 it is worth only the reading behind it.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "scripts/check_critical_paths.sh", "line": 55, "issue": "The empty-stderr failure branch prints \"KILLED by signal $((rc - 128))\" for any failure with no git output, without checking that rc is actually >128, so a non-signal failure gets a fabricated (and impossible) signal number.", "scenario": "The runner's disk fills during the diff: git cannot write its fatal: line, exits 128, and the operator reads 'git printed nothing. Status >128 means it was KILLED by signal 0 - 137 is the OOM killer, 141 is SIGPIPE.' - a presumed cause under an empty message, which is exactly the misattribution this change exists to remove. Fix: guard the signal wording with `if [ \"$rc\" -gt 128 ]` and otherwise print a plain 'git printed nothing and exited $rc'."}, {"severity": "minor", "file": "tests/test_trust_guards.py", "line": 672, "issue": "The new conditional base-sha hint is asserted only in the negative (absent for a silent death, 'fetch-depth' absent for an unreachable base); no test asserts it is PRINTED in the case where git's words do implicate the revision, so its true arm has zero coverage.", "scenario": "A later edit narrows or typos one alternative in the `unknown revision|bad object|Invalid symmetric difference|ambiguous argument|no merge base` pattern list, or drops the block while trimming the comment-heavy branch; occurrence #4 fires on a force-pushed base branch, the operator sees git's raw fatal: with no pointer to what the checker actually needs, and the whole suite stays green. Fix: add `assert \"exist in this checkout\" in res.stdout` to test_unreachable_base_reports_gits_own_error, which already reproduces that scenario."}]}
```
