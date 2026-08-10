# Agent review — approve

date: 2026-08-10T14:45:53.572075
range: origin/main..HEAD
branch: trust/ci-flake-critical-paths
findings: 1

- **minor** `workflow-trust-plan.md:1803` — This commit establishes and test-enforces the byte-identical-mirror rule for the check_critical_paths.sh listing, but the critical_paths.txt listing in the same WT.7 section is left stale and unenforced: it omits `run-qwen3vl/config/  golden-verified` and keeps the pre-D9 header asserting that bash_guard.py blocks agents from applying labels.
  - scenario: A session consults WT.7 for the critical-path table (now reasonably trusting it, since the block beside it is declared a maintained mirror) and either copies the listing over scripts/critical_paths.txt — dropping run-qwen3vl/config/ so real-model config changes stop requiring golden-verified — or concludes from the stale header that an agent cannot apply its own review label, which D9 documents as false. Fix: sync the ```text block to scripts/critical_paths.txt and extend test_wt7_embedded_mirror_matches_the_shipped_checker to cover it, or label it a historical snapshot pointing at the real table.

---

## Full review

I've read the full range (1 commit), the shipped checker, both CI workflows, the real `critical_paths.txt`, the `cp_repo` fixture and every test that uses it, the WT.7 plan section, and the four prior branch reviews.

## What holds up

**The checker change fails closed and the reasoning in its comments checks out.** Routing git's stderr to a temp file rather than `2>&1` keeps warning lines out of `$raw`; the comment's claim that a folded-in line "can only ADD a spurious match, never hide a real one" is correct (no TAB → `cut -f2-` passes it whole → it becomes an extra candidate path → at worst an extra `FAIL`). The `trap … EXIT` is installed *after* the early `table missing` exit, and `rm -f` doesn't perturb `exit "$missing"`. A failed `mktemp` degrades to a bad redirect → non-zero → the FAIL branch, which is the safe direction. The `if [ -s … ]` form is right for the reason its comment gives.

**The prior review's major is genuinely fixed.** I verified the WT.7 mirror is now byte-identical to `scripts/check_critical_paths.sh` (extracted both, compared — equal), and `test_wt7_embedded_mirror_matches_the_shipped_checker` pins it.

**Test integrity is sound.** Nothing was deleted or weakened; `test_large_changeset_does_not_fail_open` keeps both original assertions and only gains a `detail` string. `test_unreachable_base_reports_gits_own_error` is a real regression test (against `origin/main`'s script, stdout carries neither `git said` nor `fatal:`). The warning test discriminates correctly in both directions: under the old script `res.stderr.count("git: ")` is 0, and under a `2>&1` regression `"warning:" in res.stderr` fails. Its docstring is more cautious than it needs to be — the two stderr assertions *do* imply the warning never entered `$raw` — but honest under-claiming isn't a defect. The fixture's new `assert res.returncode == 0` breaks no existing call site: I traced every `change()`/`git_mv()` and each stages distinct content, including the new test where the trailing `change("g0.txt", …)` still has the 39 other pending edits to commit.

**Verification limit, stated plainly:** a full suite was already running (`pgrep` shows PID 1773466), and per this repo's own lesson I did not start a second one; sandbox denials also blocked a standalone `git` repro. So I could not empirically confirm that git emits the rename-limit `warning:` for `--name-status`. That construction fails *loud* if it doesn't (empty `_cp_err` → no replay → both stderr assertions fail), so it is a red-CI risk, never a silent-green one.

## Finding — one minor

The sibling mirror in the same WT.7 section is stale. This commit introduces the doctrine ("a stale mirror is worse than no mirror") and enforces it for the script; the `critical_paths.txt` listing 15 lines above gets neither. Diffed against the real table, it is missing `run-qwen3vl/config/  golden-verified` and still carries the pre-D9 header claiming `bash_guard.py` *blocks* agents from applying labels — the exact overstatement `critical_paths.txt` and CLAUDE.md were corrected to call "a speed bump against accident, not a guarantee." The prior review declined to score this as predating the range; I'm scoring it minor because this commit is what makes the block's neighbor an enforced mirror, so a reader now reasonably trusts both.

**Note, not scored:** `test_wt7_embedded_mirror_matches_the_shipped_checker` hardcodes `body = starts[0] + 3` to skip the header comment. If that header ever grows a line, the test fails with "re-copy the file into the block" — advice that won't fix it. Anchoring on the `#!/usr/bin/env bash` line after the marker would be self-maintaining. Fail-closed either way, so low stakes.

**Process, for the human (not findings):** this PR touches `scripts/`, so `critical-paths` will demand `human-reviewed` — yours to apply, not mine. `.guard-override` is still present in the worktree.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "workflow-trust-plan.md", "line": 1803, "issue": "This commit establishes and test-enforces the byte-identical-mirror rule for the check_critical_paths.sh listing, but the critical_paths.txt listing in the same WT.7 section is left stale and unenforced: it omits `run-qwen3vl/config/  golden-verified` and keeps the pre-D9 header asserting that bash_guard.py blocks agents from applying labels.", "scenario": "A session consults WT.7 for the critical-path table (now reasonably trusting it, since the block beside it is declared a maintained mirror) and either copies the listing over scripts/critical_paths.txt — dropping run-qwen3vl/config/ so real-model config changes stop requiring golden-verified — or concludes from the stale header that an agent cannot apply its own review label, which D9 documents as false. Fix: sync the ```text block to scripts/critical_paths.txt and extend test_wt7_embedded_mirror_matches_the_shipped_checker to cover it, or label it a historical snapshot pointing at the real table."}]}
```
