# Agent review — approve

date: 2026-08-10T15:40:21.627959
range: origin/main..HEAD
branch: trust/ci-flake-critical-paths
findings: 2

- **minor** `tests/test_trust_guards.py:707` — test_git_warnings_reach_the_log_without_entering_the_path_list never asserts the checker's exit status, so the gate's verdict in the presence of a git warning is unverified by the test named for that scenario.
  - scenario: If a future git version emits a warning line that survives `cut -f2-` and prefix-matches a table pattern, the gate fails closed spuriously (rc 1) on any PR with a rename-heavy diff; this test still passes because it only checks that 'src/va/cli.py' appears in stdout and that the replay landed on stderr. Fix: add `assert res.returncode == 0, detail` alongside the existing assertions.
- **minor** `scripts/check_critical_paths.sh:42` — The new mktemp failure branch (message + exit 1) has zero test coverage, in a change whose entire premise is that the gate's failure messages must be accurate.
  - scenario: A CI runner with a full or read-only /tmp makes mktemp fail; if that branch's message or exit status ever regresses (e.g. someone drops the `|| { … exit 1; }` while refactoring), the gate exits 0 and scans nothing — fail-open — and no test notices. Fix: add a cp_repo case that runs the checker with TMPDIR set to a nonexistent directory and asserts rc 1 plus the temp-file message.

---

## Full review

I reviewed the single commit in `origin/main..HEAD`, read the full diff, both mirrored files, the plan section, the CI workflows, the path guard, and the pre-push hook. I could not run pytest in this session (the invocation was denied), so I verified what I could statically and by direct byte comparison.

## What I verified rather than assumed

- **Both WT.7 mirror blocks are genuinely byte-identical.** I re-implemented the test's extraction independently and checksummed it: plan lines 1866–1965 vs `scripts/check_critical_paths.sh` → `b6e9efc0…` on both; plan lines 1808–1851 vs `scripts/critical_paths.txt` → `a6cb8790…` on both. Exactly one marker line matches each `startswith` prefix (1806 and 1863), the header offsets (2 and 3) land precisely on the first real line of each file, and neither file can contain a line stripping to a bare fence. The test is sound and currently green by construction.
- **No gate semantics changed.** `raw=$(…)` captured stdout only before and after, so `$changed`, the prefix scan, and both exit paths are identical to `origin/main`. Routing git's stderr to a file rather than `2>&1` is strictly the safer choice, and the comment's reasoning holds — a warning line has no TAB, `cut -f2-` would pass it through whole, and a spurious prefix match can only fail *closed*.
- **The eliminated hypothesis really is eliminated.** `pr-gates.yml:43` is the only automated caller and does check out with `fetch-depth: 0`; `offline-tests.yml` has no `fetch-depth`, matching the note. `cp_repo` runs `git init` under `tmp_path` and the checker's `cd "$(git rev-parse --show-toplevel)"` resolves inside that sandbox, so the outer clone depth cannot reach it.
- **No tests deleted or weakened.** The 8 removed lines are all replaced by stronger versions: the fixture's `git()` now asserts success, `check()` surfaces `res.stderr`, and `test_large_changeset_does_not_fail_open` carries stdout+stderr in its failure detail. `test_git_warnings_…` now touches `src/va/cli.py` with its label, so the previous round's "vacuous stdout assertion" finding is genuinely fixed — with the label present the run exits 0 and stdout carries a real `ok:` line.
- **Fixture sequencing is safe.** In the new test, `git mv` stages the rename with pre-edit content and each `git commit` sees a non-empty index; the 40 unstaged content edits are swept up by the later `git add -A`, so the newly-strict `git()` assertion cannot trip on "nothing to commit". `gc.auto=0` is set in local config, which does disable automatic housekeeping.
- **`run-qwen3vl/config/` was already in the shipped table** — the plan's listing was the stale side, exactly as the note claims, and `run-qwen3vl/config` exists.
- **Honest scope.** The As-built note states plainly that the root cause is unidentified and frames `gc.auto` as a plausible race rather than a proven cause — no determinism-presented-as-correctness problem.

Things I checked and dropped: `mktemp` without a template is BSD-incompatible, but nothing here targets macOS; the "skipped rename detection is a gate hole" line is correctly refuted (`--name-status` reports the undetected pair as `D <old>` + `A <new>`); and I chased whether `GIT_DIR` leaking from the pre-push hook could let `cp_repo` operate on the real repo — if that happened these tests would have been corrupting the working repo at every push since PR 4, which they demonstrably have not. The new mirror-sync invariant is undocumented in CLAUDE.md but is documented in the owning plan doc *and* as a header comment in both source files, i.e. at the exact point a maintainer would hit it — that's the right placement, not a finding.

## Findings (both minor)

**`tests/test_trust_guards.py:707`** — the test named for "does not enter the path list" never asserts the checker's verdict. Adding `assert res.returncode == 0, detail` is the one-line fix that makes the gate's decision, not just its log routing, observable in this scenario.

**`scripts/check_critical_paths.sh:42`** — the new `mktemp` failure branch is uncovered, in a change whose thesis is that failure messages must be accurate. `TMPDIR` pointed at a nonexistent directory makes GNU `mktemp` fail deterministically, so the test is cheap.

Process note (not a finding): this PR touches `scripts/`, so `critical-paths` will demand `human-reviewed` — yours to apply, and per D9 it is worth only the reading behind it. The suite's green state is unverified by me.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "tests/test_trust_guards.py", "line": 707, "issue": "test_git_warnings_reach_the_log_without_entering_the_path_list never asserts the checker's exit status, so the gate's verdict in the presence of a git warning is unverified by the test named for that scenario.", "scenario": "If a future git version emits a warning line that survives `cut -f2-` and prefix-matches a table pattern, the gate fails closed spuriously (rc 1) on any PR with a rename-heavy diff; this test still passes because it only checks that 'src/va/cli.py' appears in stdout and that the replay landed on stderr. Fix: add `assert res.returncode == 0, detail` alongside the existing assertions."}, {"severity": "minor", "file": "scripts/check_critical_paths.sh", "line": 42, "issue": "The new mktemp failure branch (message + exit 1) has zero test coverage, in a change whose entire premise is that the gate's failure messages must be accurate.", "scenario": "A CI runner with a full or read-only /tmp makes mktemp fail; if that branch's message or exit status ever regresses (e.g. someone drops the `|| { … exit 1; }` while refactoring), the gate exits 0 and scans nothing — fail-open — and no test notices. Fix: add a cp_repo case that runs the checker with TMPDIR set to a nonexistent directory and asserts rc 1 plus the temp-file message."}]}
```
