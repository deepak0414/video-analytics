# Agent review — approve

date: 2026-08-22T11:53:35.159960
range: origin/main..HEAD
branch: fix/reasoner-config-env-leak
findings: 0


---

## Full review

I couldn't execute the test file (pytest runs need approval this session), so this review is static — but the change is small and fully readable.

**What I checked**

- `_sanitized_child_env` is pure, copies the env, drops exactly the three storm selectors, sets `VA_AGENT_REVIEW=1`, and `_call` now passes it as `env=`; PATH/HOME and auth-relevant vars survive, so `claude` still resolves in the child (`src/va/adapters/reasoner/claude_cli_inproc.py:43-83`).
- The only hooks that read `VA_AGENT_REVIEW` are `.githooks/post-commit:9` and `.claude/hooks/stop_gate.sh:11` (both plain recursion guards, exit 0) — no guard *relaxes* on that var, so a reasoner child carrying it gains nothing beyond "no suite, no review". The semantic overload is recorded in `workflow-trust-plan.md`.
- `tests/conftest.py` is the only conftest; `testpaths = ["tests"]` so the Stop gate's `pytest -q` and CI both load it before any test module imports `va`. `_config_dir()` reads the env at call time and nothing in `src/` caches `load_config()` at import, so the collection-time pop is sufficient.
- Golden gating is consistent: `test_golden_queries.py:35` / `test_golden_ask.py:25` already key on `RUN_GOLDEN`, which is exactly what `_should_strip` preserves; empty `RUN_GOLDEN=` is falsy in both places.
- Every existing non-golden test that needs a config dir sets it via `monkeypatch.setenv` (test_profile_gating, test_staged_models, test_nvr_source, …) after collection, so the strip doesn't break them.
- The adapter is the only `claude -p` spawn site in `src/` (the other `subprocess.run` calls are ffmpeg/NVR). `scripts/agent-review.sh` is not sanitized at source, but its child's Bash is limited to `git *`, its Stop gate is disabled by `VA_AGENT_REVIEW`, and the conftest covers any suite anyway — not a storm path.
- New tests: `test_call_spawns_child_with_sanitized_env` KeyErrors pre-fix (no `env=`), `test_conftest_module_load_strips_a_constructed_leak` constructs the leak and would fail pre-fix (no conftest), the pure helpers are covered, and `_load_conftest` is path-based so it's independent of pytest import mode. `test_conftest_strips_config_dir_during_offline_run` is only meaningful under a leaked env (it passes trivially in a clean CI env), but it's complemented by the constructed-leak test, so not a gap.
- Docs: CLAUDE.md Testing section documents the strip + `RUN_GOLDEN` exception; the plan doc documents the second `VA_AGENT_REVIEW` producer. No new CLI flags/config keys. Commit subject is a provisional `need_agent_review:` (exempt).

No correctness, contract, test-integrity, combination, or documentation findings survived verification.

```json
{"verdict": "approve", "findings": []}
```
