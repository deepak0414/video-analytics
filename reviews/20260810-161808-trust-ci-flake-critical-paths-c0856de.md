# Agent review — approve

date: 2026-08-10T16:25:28.166215
range: origin/main..HEAD
branch: trust/ci-flake-critical-paths
findings: 1

- **minor** `scripts/check_critical_paths.sh:46` — The diff-failure branch replays git's stderr but never reports git's exit status, and prints the shallow-clone/force-push explanation unconditionally, so a silent git death still yields a message whose only content is a presumed cause.
  - scenario: Occurrence #4 of the flake is git being OOM-killed (status 137, empty stderr) or reporting object-store corruption on the ~900-commit sandbox; the operator sees 'git said:' followed by nothing (or by an unrelated 'error: object file is empty') and then 'the checker needs <sha> to exist ... a shallow clone or a force-pushed base branch removes it' — reintroducing exactly the misattribution this change removed. Fix: take rc=$? as the first statement inside the || block and print it, and emit the shallow-clone hint only when git's stderr is empty or actually names a revision.

---

## Full review

I reviewed the single commit in `origin/main..HEAD`, read the full diff, both mirrored blocks, the CI workflows, the guards, and the fixture — and verified the script's three new paths by actually running them in throwaway git sandboxes.

## What I verified rather than assumed

- **Both WT.7 mirror blocks are byte-identical to their files.** I re-implemented the extraction the test performs (marker line + header offset → closing fence) against the committed files: both report `MATCH`, exactly one marker line each, and neither file contains a line stripping to a bare fence. Working tree is clean for all three paths and there is no `.gitattributes`, so no EOL transform can desync CI from local.
- **The unreachable-base branch really surfaces git's words.** Sandbox run with `deadbeef*5`: rc 1, stdout carries `fatal: Invalid symmetric difference expression …`, and `fetch-depth` is absent — all four assertions in `test_unreachable_base_reports_gits_own_error` hold.
- **The mktemp branch fails closed with the right message.** `TMPDIR=<nonexistent>`: rc 1, `FAIL: cannot create a temp file for git's stderr`, no `cannot diff against`. This also incidentally closes a latent hole I went looking for — the pattern loop feeds itself via a here-document, which bash materializes in `$TMPDIR`; a bad `TMPDIR` would have made that redirect fail, skipping the loop body and exiting 0 (fail-open on every critical path). The new guard now exits before reaching it.
- **The warning test constructs its scenario.** Rebuilt it (40 rename+edit pairs, `diff.renameLimit=1`): git emits `warning: exhaustive rename detection was skipped…`, the checker exits 0, stdout carries the `ok: 'src/va/cli.py'` line, and stderr carries the `git: `-prefixed replay. Every assertion is non-vacuous, and the `git: ` prefix assertion is what would catch a regression to `2>&1`.
- **No gate semantics changed.** `raw=$(…)` captured stdout only before and after, so `$changed`, the prefix scan, and both exit statuses are identical to `origin/main`. The two new comment lines in `critical_paths.txt` parse as `#` and are skipped.
- **The eliminated hypothesis is genuinely eliminated.** `pr-gates.yml:43` is the only automated caller and uses `fetch-depth: 0`; `offline-tests.yml:28` has none, matching the note. `cp_repo` runs `git init` under `tmp_path`, so the outer clone depth cannot reach it.
- **No test was deleted or weakened, and the previous round's two findings are both closed** — the exit-status assertion at `tests/test_trust_guards.py:737` and the mktemp test at `:684` both exist. The fixture's newly-strict `git()` cannot trip on "nothing to commit": in the new test the 40 content edits stay unstaged until a later `git add -A`, and no existing test re-commits identical content (I walked all 11 `cp_repo` tests).
- **Honest scope.** The as-built note states the root cause is unidentified and frames `gc.auto` as a plausible race, not a proven one — no determinism-presented-as-correctness problem, and no repo-rule violations (no hardcoded content, no best-effort role touched).

I could not run pytest — the invocation was denied in this session — so the suite's green state is unverified by me; everything above is either static or reproduced by hand.

## Finding

**minor — `scripts/check_critical_paths.sh:46`.** The failure branch prints git's stderr but never git's *exit status*, and then asserts a cause unconditionally. If occurrence #4 is a silent death (OOM-kill of a `git diff` over a ~900-commit sandbox on a loaded runner → status 137, empty stderr), the operator gets `git said:` followed by nothing, immediately followed by "the checker needs '<sha>' to exist in this checkout: a shallow clone or a force-pushed base branch removes it" — a hypothesis that is now the only content of the message. The same applies if git reports object-store corruption (`error: object file … is empty`), which is the note's own `gc`-race hypothesis: git's message and the checker's hint would flatly contradict each other. Fix: capture `rc=$?` as the first statement in the `||` block and include it (`git exited $rc`), and gate the parenthetical hint on `[ -s "$_cp_err" ]` being false or on the message actually mentioning a revision.

## Notes, not findings

- The new mirror invariant is undocumented in CLAUDE.md but documented in the owning plan doc *and* as a header in both source files — i.e. at the exact point a maintainer would hit it. That is the right placement.
- Putting a copy of guarded machinery (`path_guard.py:45–46` covers both files) into the unguarded `workflow-trust-plan.md` is contained: any divergence turns `test_wt7_embedded_mirror_matches_the_shipped_checker` red, so it fails closed in both directions.
- This PR touches `scripts/`, so `critical-paths` will demand `human-reviewed` — yours to apply, and per D9 it is worth only the reading behind it.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "scripts/check_critical_paths.sh", "line": 46, "issue": "The diff-failure branch replays git's stderr but never reports git's exit status, and prints the shallow-clone/force-push explanation unconditionally, so a silent git death still yields a message whose only content is a presumed cause.", "scenario": "Occurrence #4 of the flake is git being OOM-killed (status 137, empty stderr) or reporting object-store corruption on the ~900-commit sandbox; the operator sees 'git said:' followed by nothing (or by an unrelated 'error: object file is empty') and then 'the checker needs <sha> to exist ... a shallow clone or a force-pushed base branch removes it' — reintroducing exactly the misattribution this change removed. Fix: take rc=$? as the first statement inside the || block and print it, and emit the shallow-clone hint only when git's stderr is empty or actually names a revision."}]}
```
