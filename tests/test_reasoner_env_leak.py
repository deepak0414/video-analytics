"""The VA_CONFIG_DIR env-leak fix (memory: claude-cli-reasoner-env-leak).

`claude -p` children — the Role-11 reasoner subprocess and the post-commit
reviewer — must not inherit the parent's real-model `VA_CONFIG_DIR`, or the
pytest suites those children spawn load the real models instead of the stub and
pile into glacial, flaky "storms". Two layers guard this:

  1. `_sanitized_child_env` / `_call`  — the adapter strips the var (and sets the
     post-commit recursion guard) on the child it spawns.
  2. `tests/conftest.py`               — the offline suite strips it at collection
     time, so a suite spawned with an already-leaked env still runs the stub.

These tests fail on the pre-fix code: `_sanitized_child_env` did not exist and
`_call` spawned the child with no `env=` (full inheritance).
"""
import importlib.util
import os
from pathlib import Path

import pytest

import va.adapters.reasoner.claude_cli_inproc as mod
from va.adapters.reasoner.claude_cli_inproc import _sanitized_child_env


def _load_conftest():
    """Load tests/conftest.py by path — independent of pytest's import mode — so
    the golden-preservation predicate is always covered, never silently skipped."""
    path = Path(__file__).with_name("conftest.py")
    spec = importlib.util.spec_from_file_location("_va_conftest_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sanitized_child_env_drops_storm_vars_sets_guard_and_is_pure():
    base = {
        "VA_CONFIG_DIR": "run-claude/config",   # real-model selectors + golden
        "RUN_GOLDEN": "1",                       #   harness un-gate + workdir: none
        "GOLDEN_WORKDIR": ".va-shots",           #   may steer a child suite to the
        "PATH": "/usr/bin",                      #   real/golden path (the storm).
        "HOME": "/home/x",
    }
    env = _sanitized_child_env(base)

    assert "VA_CONFIG_DIR" not in env          # real-model config never reaches the child
    assert "RUN_GOLDEN" not in env             # child never un-gates the golden harnesses
    assert "GOLDEN_WORKDIR" not in env         #   nor points them at a real-model workdir
    assert env["VA_AGENT_REVIEW"] == "1"        # child won't re-trigger post-commit review
    assert env["PATH"] == "/usr/bin"            # the rest of the parent env is preserved
    assert env["HOME"] == "/home/x"
    assert base == {                            # pure: caller's mapping is untouched
        "VA_CONFIG_DIR": "run-claude/config",
        "RUN_GOLDEN": "1",
        "GOLDEN_WORKDIR": ".va-shots",
        "PATH": "/usr/bin",
        "HOME": "/home/x",
    }


def test_sanitized_child_env_defaults_to_os_environ(monkeypatch):
    monkeypatch.setenv("VA_CONFIG_DIR", "run-claude/config")
    monkeypatch.setenv("RUN_GOLDEN", "1")
    monkeypatch.setenv("VA_SENTINEL_KEEPME", "yes")

    env = _sanitized_child_env()

    assert "VA_CONFIG_DIR" not in env
    assert "RUN_GOLDEN" not in env
    assert env["VA_AGENT_REVIEW"] == "1"
    assert env["VA_SENTINEL_KEEPME"] == "yes"
    # the live environment itself must not be mutated by the helper
    assert os.environ.get("VA_CONFIG_DIR") == "run-claude/config"


def test_call_spawns_child_with_sanitized_env(monkeypatch):
    """`_call` must hand subprocess.run an explicit sanitized env. Pre-fix it
    passed no `env=`, so the child inherited VA_CONFIG_DIR — this KeyErrors then."""
    monkeypatch.setenv("VA_CONFIG_DIR", "run-claude/config")
    monkeypatch.setattr(mod.shutil, "which", lambda _b: "/usr/bin/claude")

    captured: dict = {}

    class FakeProc:
        returncode = 0
        stdout = '{"result": "ok"}'
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    out = mod.ClaudeCliReasoner()._call("hello")

    assert out == "ok"
    child_env = captured["env"]
    assert child_env is not None
    assert "VA_CONFIG_DIR" not in child_env
    assert child_env["VA_AGENT_REVIEW"] == "1"


@pytest.mark.skipif(
    bool(os.environ.get("RUN_GOLDEN")),
    reason="golden runs keep VA_CONFIG_DIR; the conftest strip is intentionally skipped",
)
def test_conftest_strips_config_dir_during_offline_run():
    # tests/conftest.py removed VA_CONFIG_DIR at collection time for offline runs,
    # so even a suite spawned with a leaked env sees the stub config here.
    assert os.environ.get("VA_CONFIG_DIR") is None


def test_conftest_should_strip_predicate_preserves_golden():
    conftest = _load_conftest()
    assert conftest._should_strip({"VA_CONFIG_DIR": "x"}) is True
    assert conftest._should_strip({"RUN_GOLDEN": "1", "VA_CONFIG_DIR": "x"}) is False


def test_conftest_module_load_strips_a_constructed_leak(monkeypatch):
    # Reproduce the leak the strip exists for: set VA_CONFIG_DIR, re-run the
    # conftest's module-level strip, and confirm it is popped. This FAILS on
    # pre-fix code (no strip). Under RUN_GOLDEN the strip is skipped so the real
    # config survives for the golden harnesses.
    monkeypatch.delenv("RUN_GOLDEN", raising=False)
    monkeypatch.setenv("VA_CONFIG_DIR", "run-claude/config")
    _load_conftest()  # re-executes the module-level strip
    assert os.environ.get("VA_CONFIG_DIR") is None

    monkeypatch.setenv("RUN_GOLDEN", "1")
    monkeypatch.setenv("VA_CONFIG_DIR", "run-claude/config")
    _load_conftest()
    assert os.environ.get("VA_CONFIG_DIR") == "run-claude/config"
