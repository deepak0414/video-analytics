"""Trust-layer self-tests (workflow-trust-plan.md WT.10, L1 slice).

Drives dummy commits/pushes through the REAL hook scripts inside a sandbox git
repo (bare origin + working clone, hooks active via core.hooksPath). The offline
suite the pre-push gate runs is a stub `.venv/bin/pytest` the tests control, so
these run with no GPU, no network, and no recursion into the real suite.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ZERO_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


class TrustRepo:
    def __init__(self, root: Path):
        self.root = root

    def run(self, *cmd, env=None, text_input=None):
        merged = os.environ.copy()
        merged.update(ZERO_ENV)
        merged.update(env or {})
        return subprocess.run(
            cmd, cwd=self.root, env=merged, text=True, input=text_input,
            capture_output=True,
        )

    def git(self, *args, env=None):
        return self.run("git", *args, env=env)

    def write(self, rel, content):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    def add_all(self):
        assert self.git("add", "-A").returncode == 0

    def commit(self, msg, env=None):
        return self.git("commit", "-m", msg, env=env)

    def push(self, *args, env=None):
        return self.git("push", *args, env=env)

    def set_suite(self, passing: bool):
        (self.root / ".venv/bin/.pytest_result").write_text(
            "pass" if passing else "fail"
        )

    def last_message(self):
        return self.git("log", "-1", "--pretty=%B").stdout


@pytest.fixture
def trust_repo(tmp_path):
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "clone", str(origin), str(work)], check=True, capture_output=True
    )
    repo = TrustRepo(work)
    repo.git("config", "user.email", "trust-test@example.com")
    repo.git("config", "user.name", "Trust Test")
    repo.git("checkout", "-b", "main")
    # Seed main and push it BEFORE activating hooks, so the sandbox has an
    # origin/main baseline the same way the real repo does.
    repo.write("README.md", "seed\n")
    repo.add_all()
    assert repo.commit("seed").returncode == 0
    assert repo.push("-u", "origin", "main").returncode == 0

    # Install the real hooks + scripts from this repo.
    shutil.copytree(REPO_ROOT / ".githooks", work / ".githooks")
    (work / "scripts").mkdir(exist_ok=True)
    for script in (REPO_ROOT / "scripts").glob("*.sh"):
        shutil.copy(script, work / "scripts" / script.name)
    for hook in (work / ".githooks").iterdir():
        hook.chmod(0o755)
    for script in (work / "scripts").iterdir():
        script.chmod(0o755)
    repo.git("config", "core.hooksPath", ".githooks")

    # Stub venv: pytest result toggled per-test; python is the real interpreter
    # (the pre-commit syntax gate genuinely compiles staged files).
    venv_bin = work / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    stub = venv_bin / "pytest"
    stub.write_text(
        '#!/bin/sh\nd="$(dirname "$0")"\n'
        'if [ "$(cat "$d/.pytest_result" 2>/dev/null)" = "fail" ]; then\n'
        '  echo "1 failed, 4 passed in 0.01s"; exit 1\nfi\n'
        'echo "5 passed in 0.01s"; exit 0\n'
    )
    stub.chmod(0o755)
    repo.set_suite(True)
    os.symlink(sys.executable, venv_bin / "python")

    # Day-to-day work happens on a branch (matches the repo's PR flow).
    repo.git("checkout", "-b", "feature/x")
    return repo


# --- pre-commit (matrix rows 1-4 + syntax gate) ---


def test_commit_on_main_blocked_and_human_overridable(trust_repo):
    r = trust_repo
    r.git("checkout", "main")
    r.write("f.txt", "x\n")
    r.add_all()
    res = r.commit("change on main")
    assert res.returncode != 0
    assert "direct commit to main" in res.stderr
    res = r.commit("change on main", env={"ALLOW_MAIN_COMMIT": "1"})
    assert res.returncode == 0


def test_secret_scan_blocks_staged_token(trust_repo):
    r = trust_repo
    # Constructed at runtime so this source file never contains a scannable literal.
    fake_token = "hf_" + "a" * 24
    r.write("config_snippet.py", f'TOKEN = "{fake_token}"\n')
    r.add_all()
    res = r.commit("add config")
    assert res.returncode != 0
    assert "possible secret" in res.stderr


def test_artifact_guard_blocks_workdir_files(trust_repo):
    r = trust_repo
    r.write(".va/catalog.db", "not a real db")
    r.add_all()
    res = r.commit("oops workdir")
    assert res.returncode != 0
    assert "artifacts staged" in res.stderr


def test_net_test_deletion_blocked_and_human_overridable(trust_repo):
    r = trust_repo
    r.write(
        "tests/test_sample.py",
        "def test_a():\n    assert True\n\n\ndef test_b():\n    assert True\n",
    )
    r.add_all()
    assert r.commit("add tests").returncode == 0
    r.write("tests/test_sample.py", "def test_a():\n    assert True\n")
    r.add_all()
    res = r.commit("trim tests")
    assert res.returncode != 0
    assert "net test deletion" in res.stderr
    res = r.commit("trim tests", env={"ALLOW_TEST_REMOVAL": "1"})
    assert res.returncode == 0


def test_syntax_gate_blocks_uncompilable_python(trust_repo):
    r = trust_repo
    r.write("src/broken.py", "def f(:\n")
    r.add_all()
    res = r.commit("add broken module")
    assert res.returncode != 0
    assert "does not compile" in res.stderr


# --- commit-msg (matrix row 5) ---


def test_commit_msg_rewrites_trailers(trust_repo):
    r = trust_repo
    r.write("g.txt", "y\n")
    r.add_all()
    msg = "feat: thing\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
    res = r.git("commit", "-m", msg)
    assert res.returncode == 0
    body = r.last_message()
    assert "Co-Authored-By" not in body
    assert "Signed-off-by: Deepak Gupta (deepak0414) using Claude assistance" in body


# --- pre-push (matrix row 6) ---


def test_pre_push_blocks_red_suite_and_allows_green(trust_repo):
    r = trust_repo
    r.write("h.txt", "z\n")
    r.add_all()
    assert r.commit("work").returncode == 0
    r.set_suite(False)
    res = r.push("-u", "origin", "feature/x")
    assert res.returncode != 0
    assert "offline suite not green" in res.stderr
    r.set_suite(True)
    res = r.push("-u", "origin", "feature/x")
    assert res.returncode == 0


def test_branch_deletion_push_skips_gates(trust_repo):
    r = trust_repo
    r.write("i.txt", "w\n")
    r.add_all()
    assert r.commit("work").returncode == 0
    assert r.push("-u", "origin", "feature/x").returncode == 0
    r.set_suite(False)  # gates would fail if they ran
    res = r.push("origin", ":feature/x")
    assert res.returncode == 0
