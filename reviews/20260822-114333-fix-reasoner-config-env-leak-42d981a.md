# Agent review — approve

date: 2026-08-22T11:45:43.271887
range: origin/main..HEAD
branch: fix/reasoner-config-env-leak
findings: 1

- **minor** `tests/test_reasoner_env_leak.py:110` — The conftest's os.environ.pop is never exercised under a constructed leak: the predicate test covers only _should_strip, and the offline-run strip test passes on pre-fix code whenever the runner's env is already clean, so the module's 'these tests fail on the pre-fix code' claim does not hold for the strip itself.
  - scenario: Someone later changes conftest to `if _should_strip(...): pass` (or pops the wrong key); the predicate test stays green, the strip test stays green in clean CI/session envs, and the leak returns silently. Fix: in the by-path test, monkeypatch.setenv VA_CONFIG_DIR, call _load_conftest(), assert it is gone; repeat with RUN_GOLDEN=1 set and assert it is preserved.

---

## Full review

Pytest is permission-blocked here (same as the prior reviewers), so this stays a static review. I've now read everything I need: the diff, hooks, review script, golden harnesses, prior reviews, and the covering memory note. Writing up the findings.

**What I verified holds**

- `_call` passes an explicit env copy minus `VA_CONFIG_DIR`/`RUN_GOLDEN`/`GOLDEN_WORKDIR` plus `VA_AGENT_REVIEW=1`; `.claude/hooks/stop_gate.sh:11` and `.githooks/post-commit:9` both exit 0 on that var, and the bash/path guards don't key on it — so reasoner children are hook-less with no guard weakening. Child tools are none / `Read` only, so a child can't run pytest itself.
- `va.configuration._config_dir` (`configuration.py:23`) reads the env at call time, so the collection-time pop in `tests/conftest.py` is effective; every existing test that sets `VA_CONFIG_DIR` does so via `monkeypatch.setenv` and is unaffected. Both golden modules gate on `RUN_GOLDEN` at module level, so the conftest carve-out is the only way the golden harness can still run (the memory note's suggestion to pop `RUN_GOLDEN` in conftest would have killed it — the implementer's choice is the right one). CI runs `-m "not golden"` in a clean env, unaffected.
- All three findings from the two prior reviews of e52a8ec (`_STORM_ENV_VARS`, path-loaded conftest, trust-plan "second producer" note) are now in the committed range. CLAUDE.md documents the strip + `RUN_GOLDEN` escape. No COORDINATION.md interface touched. Commit subject is a provisional `need_agent_review:` (exempt).

**Findings (minor only; verdict approve)**

1. **minor** `tests/test_reasoner_env_leak.py:110` — the conftest's actual effect (the `os.environ.pop`) is never constructed under test: `test_conftest_should_strip_predicate_preserves_golden` checks only the pure predicate, and `test_conftest_strips_config_dir_during_offline_run` (line 106) passes on the pre-fix code whenever the runner's env is already clean (which is every CI and clean-session run), so the module docstring's "these tests fail on the pre-fix code" is only true under a leak. Per the 2026-08-08 lesson this is decoration for the strip itself. Safe path: in the by-path test, `monkeypatch.setenv("VA_CONFIG_DIR", "x")`, `_load_conftest()`, assert the var is gone; then set `RUN_GOLDEN=1` too, reload, assert it is preserved — this pins both branches of the line that matters with `monkeypatch` restoring state afterward.

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "tests/test_reasoner_env_leak.py", "line": 110, "issue": "The conftest's os.environ.pop is never exercised under a constructed leak: the predicate test covers only _should_strip, and the offline-run strip test passes on pre-fix code whenever the runner's env is already clean, so the module's 'these tests fail on the pre-fix code' claim does not hold for the strip itself.", "scenario": "Someone later changes conftest to `if _should_strip(...): pass` (or pops the wrong key); the predicate test stays green, the strip test stays green in clean CI/session envs, and the leak returns silently. Fix: in the by-path test, monkeypatch.setenv VA_CONFIG_DIR, call _load_conftest(), assert it is gone; repeat with RUN_GOLDEN=1 set and assert it is preserved."}
]}
```
