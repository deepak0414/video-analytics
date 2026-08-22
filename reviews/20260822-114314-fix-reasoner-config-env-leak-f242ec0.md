# Agent review — approve

date: 2026-08-22T11:45:56.788158
range: origin/main..HEAD
branch: fix/reasoner-config-env-leak
findings: 1

- **minor** `CLAUDE.md:485` — Present-tense claim that the Role-11 reasoner child 'inherits' VA_CONFIG_DIR is stale after this change, and the claude-code backend entry (line 228) doesn't document that its children run with a sanitized env (VA_CONFIG_DIR/RUN_GOLDEN/GOLDEN_WORKDIR dropped, VA_AGENT_REVIEW=1 set) — only the adapter docstring and trust plan record it.
  - scenario: A later session widens the claude-code child's --allowedTools to run `va` commands under run-claude/config and cannot see from CLAUDE.md why the child loads the stub config; safe path: reword the conftest sentence and add one clause to the claude-code backend line.

---

## Full review

Review complete. I could not execute pytest (permission-blocked in this session, as for the prior reviewer), so everything below is traced statically.

**What I verified holds**

- `_call` (`src/va/adapters/reasoner/claude_cli_inproc.py:80-83`) now passes `env=_sanitized_child_env()`; the helper copies the mapping (pure), drops `VA_CONFIG_DIR`/`RUN_GOLDEN`/`GOLDEN_WORKDIR`, sets `VA_AGENT_REVIEW=1`. Both `.githooks/post-commit:9` and `.claude/hooks/stop_gate.sh:11` exit 0 on that var; no bash/path guard keys on it, so the child is hook-less without weakening any gate. `plan()` children get no tools, `reason()` children only `Read` — the child can't run pytest itself.
- `va.configuration._config_dir` (`configuration.py:23`) reads the env at call time, so the collection-time pop in `tests/conftest.py` is effective. Every existing test that sets `VA_CONFIG_DIR` does so via `monkeypatch.setenv` (per-test restore) — nothing breaks. Both golden modules gate on `RUN_GOLDEN` at module level, matching the conftest carve-out; `testpaths=["tests"]` and no other `conftest.py` exists.
- The fake `subprocess.run` double matches `_call`'s real parsing (`{"result":"ok"}` → `"ok"`), and pre-fix `captured["env"]` would KeyError — the test genuinely fails on old code. `_load_conftest` loads by path, so the predicate test can't silently skip under `__init__.py`/importlib mode (re-executing the module-level pop is a no-op offline, skipped under `RUN_GOLDEN` — benign).
- All three minor findings from the prior review (`reviews/20260822-113556-…`) — `_STORM_ENV_VARS`, by-path conftest load, trust-plan "second producer" paragraph — are now in the committed range.
- Combination scope: only the `claude-code` reasoner backend (`run-claude/config`) spawns a child; `rule`/`qwen*` backends and the `claude-api` placeholder are unaffected. No COORDINATION.md interface touched (test-env semantics changed, but every agent's tests already use `monkeypatch`, so no cross-agent contract shifts).

**Findings**

1. **minor** (doc parity) `CLAUDE.md:485` — the new sentence states the Role-11 reasoner subprocess "inherits the var" as present-tense fact, but after this very change it no longer does; and the Role-11 backend list (`CLAUDE.md:228`) doesn't mention that `claude-code` children run with a sanitized env (`VA_CONFIG_DIR`/`RUN_GOLDEN`/`GOLDEN_WORKDIR` dropped, `VA_AGENT_REVIEW=1` set). Scenario: a later session widens the child's `--allowedTools` to run `va …` commands under `run-claude/config` and can't see why the child loads the stub — the only record is an adapter docstring and a trust-plan paragraph. Safe path: reword the conftest sentence to "would inherit (the adapter now sanitizes it)" and add one clause to the `claude-code` backend line.

Verdict: approve (no critical/major findings).

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "CLAUDE.md", "line": 485, "issue": "Present-tense claim that the Role-11 reasoner child 'inherits' VA_CONFIG_DIR is stale after this change, and the claude-code backend entry (line 228) doesn't document that its children run with a sanitized env (VA_CONFIG_DIR/RUN_GOLDEN/GOLDEN_WORKDIR dropped, VA_AGENT_REVIEW=1 set) — only the adapter docstring and trust plan record it.", "scenario": "A later session widens the claude-code child's --allowedTools to run `va` commands under run-claude/config and cannot see from CLAUDE.md why the child loads the stub config; safe path: reword the conftest sentence and add one clause to the claude-code backend line."}]}
```
