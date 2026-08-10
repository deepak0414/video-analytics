# Agent review — request_changes

date: 2026-08-10T14:33:20.447891
range: origin/main..HEAD
branch: trust/ci-flake-critical-paths
findings: 1

- **major** `workflow-trust-plan.md:1879` — The WT.7 section's embedded listing of check_critical_paths.sh — verified to be a maintained mirror, byte-identical to origin/main's script — was not updated, so it still shows the old `FAIL: cannot diff against '$base' (fetch-depth: 0 required in CI)` line, the exact hypothesis this commit's as-built note 70 lines below declares eliminated.
  - scenario: Occurrence #4 of the flake fires; the next session opens WT.7 as the commit body directs, reads the deliverable listing before the as-built note, and either re-diagnoses toward fetch-depth (the cost this change was written to prevent) or restores the listed version over the shipped script — silently reverting the inlined git error and the success-path stderr replay while the tests pinning them go red for an unexplained reason. Fix: sync the embedded block to the shipped script (the _cp_err + trap + replay lines), or mark it explicitly as a historical snapshot pointing at scripts/check_critical_paths.sh for current behavior.

---

## Full review

I've read the full range, the shipped checker, both CI workflows, the critical-paths table, the fixture and every call site, the WT.7 plan section, and the three prior branch reviews (whose two minors this HEAD has folded in correctly — the rename-detection premise is now stated the right way in all four copies, and the new test's docstring scopes its own assertions honestly).

**Verification limits, stated plainly:** the sandbox denied `pytest`, `mktemp -d`, and `git -c …`, so nothing below rests on a recorded run. In particular I could not confirm empirically that git 2.43 emits the rename-limit `warning:` for `--name-status` output — but that construction fails LOUD if it doesn't (an empty `_cp_err` means no replay, so both stderr assertions fail), so it is not a silent-green risk. `git show origin/main:…` and file reads were available, which is what the one finding below rests on.

## Review — `origin/main..HEAD` (1 commit, `23d39a0`)

**What holds up.** The mechanism is right and fails closed. Routing git's stderr to a file rather than `2>&1` keeps it out of `$raw` (a folded-in line has no TAB, survives `cut -f2-` whole, and joins the scanned list); the `trap … EXIT` is installed after the early `table missing` exit and `rm -f` doesn't perturb `exit "$missing"`; a failed `mktemp` degrades to a bad redirect → non-zero → the FAIL branch. The `if [ -s … ]` form is correct for the reason its comment gives. `test_unreachable_base_reports_gits_own_error` is a genuine regression test — against `origin/main`'s script `res.stdout` carries neither `git said` nor `fatal:`. The fixture's new `assert res.returncode == 0` breaks no existing call site: every `change()`/`git_mv()` stages distinct content, including the new test, where the trailing `change("g0.txt", …)` commits the 39 other pending edits (`git commit` without `-a` never swept them into the per-rename commits). `gc.auto=0` is repo-local and the checker subprocess shares that cwd. No test was deleted or weakened. The checker-side fix also covers the tests that were *not* given a `detail` string — git's `fatal:` now lands on the checker's **stdout**, which pytest prints as the operand of `assert … in res.stdout`, so evidence survives whichever cp test the flake hits next. The "root cause is NOT identified" paragraph is exactly the honest scoping this repo's rules ask for, and the `need_agent_review:` subject is exempt from the clarity rule (its body is already fit for the finalize amend).

I chased and discarded one hypothesis worth recording so nobody re-spends it: the fixture inherits `os.environ`, and hook-spawned sessions here really do export `GIT_INDEX_FILE=.git/index` (I checked my own env). It is **relative**, so with `cwd=work` it resolves to the temp repo's own index — harmless — and GitHub Actions sets no `GIT_*` at all, so it cannot explain the CI flake either way.

**Finding — one major.**

1. **`workflow-trust-plan.md:1879` (major)** — WT.7 embeds a listing of `scripts/check_critical_paths.sh` under the heading "`scripts/check_critical_paths.sh`:", and it was not updated by this commit. I verified it is a *maintained mirror*, not an as-drafted snapshot: `git show origin/main:scripts/check_critical_paths.sh` is byte-identical to the block, meaning it has been re-synced through the PR-4 backstop fixes (`-M`, `core.quotepath=off`, the pure-bash matcher) since it was first written. It therefore still reads:

   ```bash
   raw=$(git -c core.quotepath=off diff -M --name-status "$base"...HEAD) || {
     echo "FAIL: cannot diff against '$base' (fetch-depth: 0 required in CI)"; exit 1; }
   ```

   — the exact hypothesis this commit exists to eliminate, sitting 70 lines above the new as-built note that says "**That hypothesis is eliminated**", in the one section the commit body directs the next diagnoser to. Failure scenario: occurrence #4 fires; a session opens WT.7 as instructed, reads the deliverable listing before the as-built note, and either spends the diagnosis on fetch-depth again (the precise cost this change was written to prevent) or — since the block is the canonical listing — restores it over the shipped script, silently reverting both the inlined git error and the success-path stderr replay while every test that pins the new behavior goes red for an unexplained reason. Safe path: update the embedded block to the shipped script (the `_cp_err` + trap + replay lines), or, if the intent is a historical snapshot, label it as one and point to `scripts/check_critical_paths.sh` for current behavior.

**Two notes, not findings.** (a) The embedded `critical_paths.txt` mirror in the same section is also stale — it lacks `run-qwen3vl/config/ golden-verified`, which the real table carries. That drift predates this range so I'm not scoring it, but it corroborates that these mirrors need a sync convention rather than one-off fixes. (b) `.guard-override` is still present in the worktree; per CLAUDE.md it is removed after gate maintenance, and that removal is the human's (it keeps the machinery-write guards relaxed until then). This PR touches `scripts/`, so `critical-paths` will demand `human-reviewed` — also the human's to apply.

```json
{"verdict": "request_changes", "findings": [{"severity": "major", "file": "workflow-trust-plan.md", "line": 1879, "issue": "The WT.7 section's embedded listing of check_critical_paths.sh — verified to be a maintained mirror, byte-identical to origin/main's script — was not updated, so it still shows the old `FAIL: cannot diff against '$base' (fetch-depth: 0 required in CI)` line, the exact hypothesis this commit's as-built note 70 lines below declares eliminated.", "scenario": "Occurrence #4 of the flake fires; the next session opens WT.7 as the commit body directs, reads the deliverable listing before the as-built note, and either re-diagnoses toward fetch-depth (the cost this change was written to prevent) or restores the listed version over the shipped script — silently reverting the inlined git error and the success-path stderr replay while the tests pinning them go red for an unexplained reason. Fix: sync the embedded block to the shipped script (the _cp_err + trap + replay lines), or mark it explicitly as a historical snapshot pointing at scripts/check_critical_paths.sh for current behavior."}]}
```
