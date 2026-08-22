# Agent review — approve

date: 2026-08-22T11:36:47.259831
range: origin/main..HEAD
branch: fix/reasoner-config-env-leak
findings: 3

- **minor** `tests/test_reasoner_env_leak.py:95` — pytest.importorskip('conftest') converts an import failure into a silent skip, so the only test of the golden-preservation predicate can disappear green.
  - scenario: Someone adds tests/__init__.py or runs with --import-mode=importlib; 'conftest' is no longer importable as a top-level module; the test skips and _should_strip is no longer covered while the suite stays green. Import loudly (from conftest import _should_strip, or load tests/conftest.py by path).
- **minor** `src/va/adapters/reasoner/claude_cli_inproc.py:49` — Sanitized child env drops only VA_CONFIG_DIR; RUN_GOLDEN/GOLDEN_WORKDIR (the 'storm multiplier' the covering note says must also be dropped) still reach the claude -p child.
  - scenario: A golden run (RUN_GOLDEN=1 VA_CONFIG_DIR=run-claude/config) spawns planner children carrying RUN_GOLDEN=1; the new conftest now PRESERVES VA_CONFIG_DIR whenever RUN_GOLDEN is set, so if any child ever runs the suite without the VA_AGENT_REVIEW short-circuit (new hook not honouring it, guard dropped) the self-breeding storm returns. Pop RUN_GOLDEN and GOLDEN_WORKDIR too and extend the pure test.
- **minor** `workflow-trust-plan.md:1111` — Trust docs state VA_AGENT_REVIEW is exported only by agent-review.sh; the reasoner adapter is now a second producer, so every va ask / va serve planner child runs with all repo hooks disabled, undocumented in the trust plan.
  - scenario: A later session auditing the recursion guard (or test_trust_guards.py:445's comment) assumes only reviewer sessions carry the var and misreads hook-less reasoner children as a guard bypass; add a one-line note to the recursion-guard bullet / CLAUDE.md paragraph.

---

## Full review

Review complete. Context gathered: the adapter, the new conftest, hooks (`stop_gate.sh`, `post-commit`), `scripts/agent-review.sh`, the trust-plan recursion-guard text, and the memory note that serves as the covering spec. Note: `pytest` invocations are permission-blocked in this headless session, so I could not execute the new tests; findings below are from reading.

**Assessment**

The change is small and correct on its main line: `_call` now passes an explicit copy of `os.environ` minus `VA_CONFIG_DIR` plus `VA_AGENT_REVIEW=1`; `stop_gate.sh:11` and `.githooks/post-commit:9` both exit 0 on that var, so a reasoner child can no longer spawn the suite or a review. `va.configuration._config_dir` reads the env at call time (`configuration.py:23`), so the collection-time pop in `tests/conftest.py` is effective, and the `RUN_GOLDEN` carve-out matches how both golden modules gate themselves (`test_golden_*.py: skip unless RUN_GOLDEN`). No existing test doubles `subprocess.run` for this adapter, so widening the call with `env=` breaks nothing (checked against the 2026-08-03 lesson). `tests/` has no `__init__.py`, so under pytest's default prepend import mode `import conftest` resolves. CLAUDE.md documents the strip and the `RUN_GOLDEN` escape. Nothing here touches a shared interface in COORDINATION.md.

**Findings (all minor; verdict approve)**

1. minor — `tests/test_reasoner_env_leak.py:95` — `pytest.importorskip("conftest")` turns an import failure into a silent SKIP, so the only test of the golden-preservation predicate can vanish green (e.g. if someone adds `tests/__init__.py` or switches to `--import-mode=importlib`, `conftest` is no longer a top-level module). Safe path: import loudly (`from conftest import _should_strip`, or load `tests/conftest.py` by path with `importlib.util`). Related: `test_conftest_strips_config_dir_during_offline_run` (line 88) passes on pre-fix code whenever the runner's env is clean, so the module docstring's "these tests fail on the pre-fix code" is only true for it under a leak — fine as a canary, but the predicate test is the real coverage, which is why it must not be skippable.

2. minor — `src/va/adapters/reasoner/claude_cli_inproc.py:49` — the covering spec (memory `claude-cli-reasoner-env-leak`, "storm multiplier") asks that `RUN_GOLDEN` and `GOLDEN_WORKDIR` be dropped too; the child still inherits them (plus `PYTEST_CURRENT_TEST`). Today this is safe only because `VA_AGENT_REVIEW=1` short-circuits every hook that runs pytest; but the new conftest now *preserves* `VA_CONFIG_DIR` whenever `RUN_GOLDEN` is set, so any future path where a child with `RUN_GOLDEN=1` runs the suite without the guard (a new hook that doesn't honour it, or the guard removed) re-creates the self-breeding storm. Safe path: pop `RUN_GOLDEN` and `GOLDEN_WORKDIR` in `_sanitized_child_env` as well (two lines, extend the pure test).

3. minor (doc parity) — `workflow-trust-plan.md:1111` and `tests/test_trust_guards.py:445` describe `VA_AGENT_REVIEW` as set by `agent-review.sh` (reviewer sessions) only; this change adds a second producer — every `va ask`/`va serve` planner child now runs with all repo hooks disabled. Add a one-line note to the trust plan's recursion-guard bullet (and/or the new CLAUDE.md paragraph) so the next reader of the trust docs knows reasoner children are hook-less by design.

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "tests/test_reasoner_env_leak.py", "line": 95, "issue": "pytest.importorskip('conftest') converts an import failure into a silent skip, so the only test of the golden-preservation predicate can disappear green.", "scenario": "Someone adds tests/__init__.py or runs with --import-mode=importlib; 'conftest' is no longer importable as a top-level module; the test skips and _should_strip is no longer covered while the suite stays green. Import loudly (from conftest import _should_strip, or load tests/conftest.py by path)."},
{"severity": "minor", "file": "src/va/adapters/reasoner/claude_cli_inproc.py", "line": 49, "issue": "Sanitized child env drops only VA_CONFIG_DIR; RUN_GOLDEN/GOLDEN_WORKDIR (the 'storm multiplier' the covering note says must also be dropped) still reach the claude -p child.", "scenario": "A golden run (RUN_GOLDEN=1 VA_CONFIG_DIR=run-claude/config) spawns planner children carrying RUN_GOLDEN=1; the new conftest now PRESERVES VA_CONFIG_DIR whenever RUN_GOLDEN is set, so if any child ever runs the suite without the VA_AGENT_REVIEW short-circuit (new hook not honouring it, guard dropped) the self-breeding storm returns. Pop RUN_GOLDEN and GOLDEN_WORKDIR too and extend the pure test."},
{"severity": "minor", "file": "workflow-trust-plan.md", "line": 1111, "issue": "Trust docs state VA_AGENT_REVIEW is exported only by agent-review.sh; the reasoner adapter is now a second producer, so every va ask / va serve planner child runs with all repo hooks disabled, undocumented in the trust plan.", "scenario": "A later session auditing the recursion guard (or test_trust_guards.py:445's comment) assumes only reviewer sessions carry the var and misreads hook-less reasoner children as a guard bypass; add a one-line note to the recursion-guard bullet / CLAUDE.md paragraph."}
]}
```
