# Agent review — approve

date: 2026-08-22T11:39:59.440420
range: origin/main..HEAD
branch: fix/reasoner-config-env-leak
findings: 3

- **minor** `src/va/adapters/reasoner/claude_cli_inproc.py:49` — Committed _sanitized_child_env drops only VA_CONFIG_DIR; the covering spec requires dropping RUN_GOLDEN and GOLDEN_WORKDIR too, and the new conftest preserves VA_CONFIG_DIR whenever RUN_GOLDEN leaks, so the second defensive layer is bypassed for golden-spawned children (the uncommitted working-tree _STORM_ENV_VARS edit fixes this — commit it).
  - scenario: RUN_GOLDEN=1 VA_CONFIG_DIR=run-claude/config GOLDEN_WORKDIR=.va-24h pytest -m golden → va ask → claude -p child inherits RUN_GOLDEN/GOLDEN_WORKDIR; if any future hook/path runs a suite in that child without honouring VA_AGENT_REVIEW, conftest keeps the real config and the self-breeding storm returns.
- **minor** `tests/test_reasoner_env_leak.py:95` — pytest.importorskip('conftest') converts an import failure into a silent skip, so the only test of the golden-preservation predicate can disappear while the suite stays green (working tree already replaces it with a by-path load — commit it).
  - scenario: Someone adds tests/__init__.py or switches to --import-mode=importlib; 'conftest' is no longer a top-level module; the test skips, _should_strip is uncovered, gates stay green.
- **minor** `workflow-trust-plan.md:1111` — The reviewed commit makes the claude-code reasoner a second producer of VA_AGENT_REVIEW=1 (every va ask / va serve planner child is hook-less), but the committed trust plan still states only agent-review.sh exports it (the working-tree paragraph fixes this — commit it alongside the code).
  - scenario: A later session auditing the recursion guard sees headless children carrying VA_AGENT_REVIEW that are not reviewer sessions and misreads them as a guard bypass, or removes the var from the adapter as 'unnecessary' and reopens the stop-gate storm.

---

## Full review

Review complete (static — `pytest` is permission-blocked in this session, same as for the earlier reviewer, so I could not execute the new tests; everything below is traced by reading).

**What I verified holds**

- `ClaudeCliReasoner._call` (`src/va/adapters/reasoner/claude_cli_inproc.py:70-73`) now passes an explicit env copy minus `VA_CONFIG_DIR` plus `VA_AGENT_REVIEW=1`. Both `.claude/hooks/stop_gate.sh:11` and `.githooks/post-commit:9` exit 0 on that var, and the bash/path guards don't key on it, so the child is hook-less without any guard weakening. `plan()` children get no tools and `reason()` children get only `Read`, so a child can't run pytest itself either — the storm source is closed at the adapter.
- `va.configuration._config_dir` (`configuration.py:21-26`) reads the env at call time, so the collection-time pop in `tests/conftest.py` is effective. Every existing test that sets `VA_CONFIG_DIR` does so via `monkeypatch.setenv` (restored per-test), so the strip breaks nothing. Both golden modules gate on `RUN_GOLDEN` at module level, matching the conftest carve-out.
- `tests/` has no `__init__.py` and `pyproject.toml` sets no `--import-mode`, so `import conftest` resolves under the default prepend mode today.
- No `subprocess.run` doubles for this adapter exist in tests (2026-08-03 lesson); the fake in the new test matches `_call`'s real parsing (`{"result": "ok"}` → `"ok"`). No COORDINATION.md interface touched; CLAUDE.md documents the strip and the `RUN_GOLDEN` escape.

**Findings (all minor; verdict approve)** — note: the working tree already carries uncommitted edits (`git diff HEAD`: `_STORM_ENV_VARS`, path-loaded conftest, trust-plan "second producer" note) that address all three. They are NOT in the reviewed range `origin/main..HEAD`; they need to land in the next `need_agent_review:` commit to count.

1. **minor** `src/va/adapters/reasoner/claude_cli_inproc.py:49` — the covering spec (memory note `claude-cli-reasoner-env-leak`, "storm multiplier") requires dropping `RUN_GOLDEN` and `GOLDEN_WORKDIR` too; the committed helper drops only `VA_CONFIG_DIR`, and the new conftest *preserves* `VA_CONFIG_DIR` whenever `RUN_GOLDEN` is set, so the second defensive layer is bypassed in exactly the golden-spawned-child case. Safe today only because `VA_AGENT_REVIEW=1` short-circuits the hooks. Safe path: pop both vars in `_sanitized_child_env` (the working-tree `_STORM_ENV_VARS` edit does this) and commit it.
2. **minor** `tests/test_reasoner_env_leak.py:95` — `pytest.importorskip("conftest")` turns an import failure into a silent skip, so the only coverage of `_should_strip` can vanish green (adding `tests/__init__.py` or `--import-mode=importlib` would do it). Safe path: load `tests/conftest.py` by path (the working-tree `_load_conftest` does this — note it re-executes the module-level pop, which is a no-op under offline runs and skipped under `RUN_GOLDEN`, so benign).
3. **minor** (doc parity) `workflow-trust-plan.md:1111` — the committed range makes the reasoner adapter a second producer of `VA_AGENT_REVIEW=1`, but the committed trust plan still says only `agent-review.sh` exports it. The working-tree edit adds the "second producer" paragraph; commit it with the code so a later auditor doesn't misread hook-less planner children as a guard bypass.

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "src/va/adapters/reasoner/claude_cli_inproc.py", "line": 49, "issue": "Committed _sanitized_child_env drops only VA_CONFIG_DIR; the covering spec requires dropping RUN_GOLDEN and GOLDEN_WORKDIR too, and the new conftest preserves VA_CONFIG_DIR whenever RUN_GOLDEN leaks, so the second defensive layer is bypassed for golden-spawned children (the uncommitted working-tree _STORM_ENV_VARS edit fixes this — commit it).", "scenario": "RUN_GOLDEN=1 VA_CONFIG_DIR=run-claude/config GOLDEN_WORKDIR=.va-24h pytest -m golden → va ask → claude -p child inherits RUN_GOLDEN/GOLDEN_WORKDIR; if any future hook/path runs a suite in that child without honouring VA_AGENT_REVIEW, conftest keeps the real config and the self-breeding storm returns."},
{"severity": "minor", "file": "tests/test_reasoner_env_leak.py", "line": 95, "issue": "pytest.importorskip('conftest') converts an import failure into a silent skip, so the only test of the golden-preservation predicate can disappear while the suite stays green (working tree already replaces it with a by-path load — commit it).", "scenario": "Someone adds tests/__init__.py or switches to --import-mode=importlib; 'conftest' is no longer a top-level module; the test skips, _should_strip is uncovered, gates stay green."},
{"severity": "minor", "file": "workflow-trust-plan.md", "line": 1111, "issue": "The reviewed commit makes the claude-code reasoner a second producer of VA_AGENT_REVIEW=1 (every va ask / va serve planner child is hook-less), but the committed trust plan still states only agent-review.sh exports it (the working-tree paragraph fixes this — commit it alongside the code).", "scenario": "A later session auditing the recursion guard sees headless children carrying VA_AGENT_REVIEW that are not reviewer sessions and misreads them as a guard bypass, or removes the var from the adapter as 'unnecessary' and reopens the stop-gate storm."}
]}
```
