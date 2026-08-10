"""Unit tests for the WT.3 session guards (workflow-trust-plan.md).

The guards are plain scripts fed a PreToolUse-style JSON payload on stdin; exit
code 2 + a stderr reason = blocked, 0 = allowed. These are script-level
equivalents of validation-matrix rows 9-13/25/38-39 — live-session behavior is
verified once at rollout (hooks snapshot at session start).
"""

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BASH_GUARD = REPO_ROOT / ".claude" / "hooks" / "bash_guard.py"
PATH_GUARD = REPO_ROOT / ".claude" / "hooks" / "path_guard.py"
STOP_GATE = REPO_ROOT / ".claude" / "hooks" / "stop_gate.sh"


def run_guard(script, tool_input, project_dir):
    """project_dir is REQUIRED: bash_guard honors a .guard-override there, so
    tests must control it explicitly (the dev repo may have one during gate
    maintenance)."""
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    payload = {"tool_name": "Bash", "tool_input": tool_input}
    return subprocess.run(
        [sys.executable, str(script)], input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )


BLOCKED_COMMANDS = [
    # --no-verify in every observed spelling (invariant: hooks must run)
    "git commit --no-verify -m x",
    "git commit -n -m x",
    "git commit -nm x",           # clustered form — real bypass found by review
    "git commit -anm x",
    "git -c foo=bar commit --no-verify",
    # force-push to main, order-independent (issue #40117 reordering evasion)
    "git push --force origin main",
    "git push -f origin main",
    "git push origin main --force",
    "git push origin main -f",
    "git push origin main --force-with-lease",
    "git push origin +main",              # git-native +refspec force
    "git push origin +HEAD:main",
    "git push --force origin HEAD:refs/heads/main",  # fully-qualified dst
    'git push --force origin "main"',     # quoted refspec (round-5 major)
    "git push -f origin 'main'",
    # full write-verb coverage on protected artifacts (round-5 major):
    "sed -i s/request_changes/approve/ reviews/20260727-x.md",
    "dd if=/dev/zero of=.git/.review-approved",
    "ln -sf /bin/true .venv/bin/pytest",
    "truncate -s0 .git/.stop-gate-green",
    # long-form / alternate write verbs (round-6 major):
    "sed --in-place s/request_changes/approve/ reviews/20260727-x.md",
    "sed --in-place s/exit_2/exit_0/ .claude/hooks/bash_guard.py",
    "install /tmp/f.md reviews/x.md",
    # pushes that rewrite main without naming it (round-6 major):
    "git push --force --all origin",
    "git push --all --force origin",
    "git push --mirror origin",
    "git push origin :main",
    "git push --delete origin main",      # round-7 minor: --delete spelling
    "git push -d origin main",
    # git global-flag insertion before the subcommand (round-4 review major)
    "git -c foo=bar commit -nm x",
    "git -C . commit -n -m 'wip: x'",
    # n-flag AFTER other flags — the round-7 narrowing regressed exactly this,
    # and --amend is this repo's documented finalize command.
    "git commit --amend -n -m x",
    "git commit -a -n -m x",
    "git commit -q -nm x",
    "git commit --amend --no-edit -n",
    # n AFTER a value token (-m/-F consume their argument) — round-9 major
    "git commit -m x -n",
    "git commit -m 'wip: x' -n",
    "git commit -F /tmp/m -n",
    "git commit --message='wip: x' -n",
    "git commit -m x --no-verify",
    # -n after flags that do NOT take a separate-token value (round-12 major)
    "git commit -S -n -m x",
    "git commit --gpg-sign -n -m x",
    "git commit -S -nm x",
    "git commit -o -n -m x",
    # env prefixes must not disable rules (round-13 major)
    "LC_ALL=C rm -rf .git",
    "FOO=1 rm -rf /",
    # subcommand found by POSITION: a branch named 'commit' must not redirect
    # the scan away from push (round-13 major)
    "git push --no-verify origin commit",
    "git -c x=y push --no-verify origin main",
    # alternate recursive-delete spellings (round-14 major)
    "rm -R .git",
    "rm --recursive .git",
    "rm -R /",
    # the hooks honor ANY non-empty override value, not just =1 (round-14 major)
    "ALLOW_MAIN_COMMIT=yes git commit -m x",
    "ALLOW_LEDGER_EDIT=y git push",
    "ALLOW_TEST_REMOVAL=true git commit -m 'wip: x'",
    # gh self-approval: env prefix, short and attached flag forms (round-14)
    "LC_ALL=C gh pr merge 14",
    "gh pr review 14 -a",
    "gh pr review 14 --approve=true",
    "gh pr edit 14 --add-label=human-reviewed",
    # PRs ARE issues, and flags may sit between noun and action; the label is
    # the PROOF a human read the diff (PR 4 backstop majors — row 81)
    "gh issue edit 14 --add-label human-reviewed",
    "gh issue -R deepak0414/video-analytics edit 14 --add-label human-reviewed",
    "gh pr -R deepak0414/video-analytics merge 14",
    "gh api repos/o/r/issues/14/labels -f labels[]=human-reviewed",
    "gh api -X PATCH repos/o/r/issues/14 -f 'labels[]=human-reviewed'",
    "gh api -X POST repos/o/r/pulls/14/reviews -f event=APPROVE",
    "gh api graphql -f query='mutation { addLabelsToLabelable(input: {}) { clientMutationId } }'",
    "gh issue edit 14 --add-label golden-verified",
    # attached method spellings and file-fed bodies (PR 4 backstop major)
    "gh api -XPATCH repos/o/r/issues/14 --input /tmp/p.json",
    "gh api --method=PATCH repos/o/r/issues/14 --input /tmp/p.json",
    "gh api graphql -F query=@mutation.graphql",
    # any HTTP client reaches the same endpoint with the same token (D9)
    "curl -X POST https://api.github.com/repos/o/r/issues/14/labels -d '{\"labels\":[\"human-reviewed\"]}'",
    "wget --post-data '{}' https://api.github.com/repos/o/r/issues/14/labels",
    "curl https://api.github.com/graphql -d '{\"query\":\"mutation{addLabelsToLabelable(input:{})}\"}'",
    "gh auth token",
    "gh auth status --show-token",
    "gh auth status -t",
    "gh auth status --show-token=true",
    "gh alias set m 'pr merge'",
    "gh alias set lbl 'issue edit'",
    "gh alias import aliases.yml",
    "https POST api.github.com/repos/o/r/issues/14/labels labels:='[\"human-reviewed\"]'",
    # the WT.7 gate machinery is protected like the other trust scripts
    "sed -i 's/exit \"$missing\"/exit 0/' scripts/check_critical_paths.sh",
    "echo x > scripts/critical_paths.txt",
    # NEWLINE-separated commands: every line is its own command (round-15
    # critical — a multi-line command hid everything after line 1)
    "echo hi\ntouch .commit-approved",
    "echo hi\ngit push --force origin main",
    "echo hi\nrm -rf .git",
    "echo hi\ngh pr merge 14",
    "cd /tmp\n\n  git commit -nm x",
    # backslash-newline is a LINE CONTINUATION, not a separator (round-16 critical)
    "git commit \\\n-n -m x",
    "git push \\\n--force origin main",
    "git push \\\n--no-verify origin feature/x",
    "git \\\ncommit -nm x",
    "gh pr \\\nmerge 14",
    # backslash inside single quotes is literal, so quote tracking must not desync
    "echo 'a\\b'\ntouch .commit-approved",
    # the venv itself is the test runner's home (round-16 minor)
    "rm -rf .venv",
    "mv .venv /tmp/v",
    "git push --no-verify origin feature/x",   # skips pre-push (suite + review)
    # shell separators INSIDE quoted arguments must not break rule matching
    # (round-10 critical: a '&' in a commit message disabled the guard)
    'cp "/tmp/a&b.txt" .git/.review-approved',
    'touch "x&y" .commit-approved',
    'ln -sf "/bin/a&b" .venv/bin/pytest',
    'cp "/tmp/x&y" .githooks/pre-commit',
    "sed -i 's/request_changes/approve &/' reviews/x.md",
    'git commit -m "feat: ingest & query" -n',
    'git commit -am "wip: a & b" --no-verify',
    'git -c user.email="a&b" push --force origin main',
    # grouping wrappers must not hide the write verb (round-11 major)
    "( touch .commit-approved )",
    "{ cp /tmp/h .git/.review-approved; }",
    "(cd /tmp && rm -rf .git)",
    # every write-redirect spelling (round-11 major)
    "echo forged >| .commit-approved",
    "scripts/review_scope_hash.sh >& .git/.review-approved",
    "echo x &>> reviews/forged.md",
    "echo x 2> .git/.stop-gate-green",
    # bare-directory machinery destruction (no trailing slash)
    "rm -rf .githooks",
    "rm -rf .claude",
    "rm -rf reviews",
    "mv .claude /tmp/x",
    "git -c color.ui=false push --force origin main",
    # stop-gate self-blessing vectors (round-4 review minors)
    "echo abc > .git/.stop-gate-green",
    "mv .venv/bin/pytest /tmp/pytest",
    "git config core.hooksPath /dev/null",
    # override tokens, quoted or not (invariant: the shell sets them either way)
    "AGENT_REVIEW=skip git push",
    'AGENT_REVIEW="skip" git push',
    "AGENT_REVIEW='skip' git push",
    "ALLOW_TEST_REMOVAL=1 git commit -m 'wip: x'",
    "ALLOW_MAIN_COMMIT=1 git commit -m x",
    "ALLOW_LEDGER_EDIT=1 git push origin main",
    'ALLOW_LEDGER_EDIT="1" git push',
    # human-only sentinels and approval file
    "touch .commit-approved",
    "touch .guard-override",
    "scripts/review_scope_hash.sh > .git/.review-approved",
    "echo abc123 > .git/.review-approved",
    # gate machinery writable from Bash — all real bypasses found by review
    "echo 'exit 0' > .githooks/pre-push",
    "sed -i 's/exit 2/exit 0/' .claude/hooks/bash_guard.py",
    "cp /tmp/evil.py .claude/hooks/bash_guard.py",
    "chmod -x .githooks/pre-commit",
    "tee .claude/settings.json",
    "tee .claude/settings.local.json",
    "truncate -s0 scripts/agent-review.sh",
    "mv scripts/review_scope_hash.sh /tmp/",
    "echo x >> .github/workflows/offline-tests.yml",
    # ledger tampering
    "echo forged > reviews/fake-approve.md",
    "tee reviews/fake.md",
    "cp /tmp/x.md reviews/x.md",
    "rm reviews/20260727-old.md",
    # self-approval via gh
    "gh pr merge 14",
    "gh pr edit 14 --add-label human-reviewed",
    "gh pr review 14 --approve",
    # destructive deletes
    "rm -rf .git",
    "rm -rf /",
    "rm -rf ~",
    "rm -rf /tmp/x .git",
]

ALLOWED_COMMANDS = [
    "git commit -m 'need_agent_review: add feature'",
    "git commit -am 'wip: checkpoint'",   # clustered but no n
    "git commit -am 'wip: handle -n flag'",  # ' -n' inside the MESSAGE is fine
    "git commit -m 'fix: guard -n and --no-verify spellings'",
    "git commit -F /tmp/msg.txt",            # value flags consume their argument
    "git push -n origin feature/x",          # push -n is --dry-run, harmless
    # reading label state is legitimate; only applying is human-only
    "gh pr list --label human-reviewed",
    "gh issue list --label golden-verified",
    "gh pr view 14",
    "gh api repos/o/r/issues/16/labels",          # GET: reading label state is fine
    "gh pr create --title x --body 'checklist mentions human-reviewed'",
    "gh pr edit 16 --body 'checklist mentions human-reviewed'",
    "git stash push -f",                     # not a remote push (round-13 minor)
    "git subtree push --prefix=x origin gh-pages",
    # a quoted multi-line MESSAGE is data, not extra commands
    "git commit -m 'wip: line one\nline two'",
    "echo hi\nls -la",
    # attached-value message forms: the letters after -m are DATA (round-16)
    "git commit -m'no changes'",
    "git commit -am'new feature'",
    "git commit --amend -m 'feat: add feature'",
    "git push -u origin trust/branch",
    "git push origin main",               # plain push to main: allowed (no force)
    "git push origin feature -u",
    "git push origin +feature/x",         # +refspec force to a non-main ref
    "git push --force-with-lease origin feature/main-page",  # NOT main (exact dst match)
    "git push -f origin trust/l0-session-guards",
    "git push --force-with-lease origin trust/l2-review-lifecycle",
    "git add reviews/",
    "ls reviews/",
    "cat reviews/20260727-ledger.md",
    "git log --oneline -5",
    "gh pr create --title x --body y",
    ".venv/bin/pytest -q",
    "rm -rf /tmp/scratch/workdir",
    # read-only access to machinery and sentinels stays open
    "ls -la .guard-override 2>&1",
    "cat .git/.review-approved",
    "cat .claude/settings.json",
    "sed -n '5,10p' .claude/hooks/bash_guard.py",
    "git diff -- .githooks/pre-push",
    "bash scripts/setup-hooks.sh",
    "bash scripts/agent-review.sh --worktree",
]


@pytest.mark.parametrize("cmd", BLOCKED_COMMANDS)
def test_bash_guard_blocks(cmd, tmp_path):
    res = run_guard(BASH_GUARD, {"command": cmd}, project_dir=tmp_path)
    assert res.returncode == 2, f"should block: {cmd}"
    assert "BLOCKED" in res.stderr


@pytest.mark.parametrize("cmd", ALLOWED_COMMANDS)
def test_bash_guard_allows(cmd, tmp_path):
    res = run_guard(BASH_GUARD, {"command": cmd}, project_dir=tmp_path)
    assert res.returncode == 0, f"should allow: {cmd} ({res.stderr})"


def test_bash_guard_override_relaxes_only_machinery_rules(tmp_path):
    """Round-3 review minor: a maintenance window must not also unlock
    self-approval — approval/waiver/audit rules stay enforced."""
    (tmp_path / ".guard-override").touch()
    for cmd in ("chmod +x .githooks/newhook",
                "echo 'exit 0' > .githooks/pre-push",
                "git config core.hooksPath .githooks"):
        res = run_guard(BASH_GUARD, {"command": cmd}, project_dir=tmp_path)
        assert res.returncode == 0, f"maintenance should allow: {cmd}"
    for cmd in ("touch .commit-approved",
                "AGENT_REVIEW=skip git push",
                "gh pr merge 14",
                "echo forged > reviews/fake.md",
                "echo x > .git/.review-approved"):
        res = run_guard(BASH_GUARD, {"command": cmd}, project_dir=tmp_path)
        assert res.returncode == 2, f"override must NOT unlock: {cmd}"


def test_path_guard_override_relaxes_only_machinery_paths(tmp_path):
    (tmp_path / ".guard-override").touch()
    res = run_guard(PATH_GUARD, {"file_path": ".githooks/pre-commit"},
                    project_dir=tmp_path)
    assert res.returncode == 0  # machinery opens for maintenance
    for path in ("reviews/fabricated.md", ".commit-approved",
                 ".git/.review-approved"):
        res = run_guard(PATH_GUARD, {"file_path": path}, project_dir=tmp_path)
        assert res.returncode == 2, f"override must NOT unlock: {path}"


def _mini_repo(tmp_path, branch):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_CONFIG_SYSTEM": "/dev/null"}
    subprocess.run(["git", "init", "-b", branch, str(tmp_path)],
                   capture_output=True, env=env, check=True)
    return tmp_path


def test_bare_force_push_blocked_from_main_checkout(tmp_path):
    _mini_repo(tmp_path, "main")
    for cmd in (
        "git push --force", "git push -f", "git push origin -f",
        # HEAD/@ refspecs force the CURRENT branch — round-3 review majors
        "git push --force origin HEAD", "git push -f origin @",
        "git push origin +HEAD",
    ):
        res = run_guard(BASH_GUARD, {"command": cmd}, project_dir=tmp_path)
        assert res.returncode == 2, f"should block from main: {cmd}"


def test_bare_force_push_allowed_from_feature_checkout(tmp_path):
    _mini_repo(tmp_path, "feature/x")
    res = run_guard(
        BASH_GUARD, {"command": "git push --force-with-lease"}, project_dir=tmp_path
    )
    assert res.returncode == 0  # force to one's own feature branch is legitimate


def test_bash_guard_tolerates_malformed_payload():
    res = subprocess.run(
        [sys.executable, str(BASH_GUARD)], input="not json",
        capture_output=True, text=True,
    )
    assert res.returncode == 0  # never brick the session


BLOCKED_PATHS = [
    ".githooks/pre-commit",
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/hooks/bash_guard.py",
    ".claude/agents/code-reviewer.md",
    ".github/workflows/offline-tests.yml",
    "scripts/agent-review.sh",
    "scripts/review_scope_hash.sh",
    "reviews/fabricated-approve.md",
    ".commit-approved",
    ".guard-override",
    ".git/.review-approved",
    ".git/.stop-gate-green",
    ".venv/bin/pytest",
    ".git/config",
    ".git/hooks/pre-commit",
    ".git/worktrees/wt1/.stop-gate-green",  # linked worktree (round-14 minor)
    ".git/worktrees/wt1/.review-approved",
]

ALLOWED_PATHS = [
    "src/va/pipeline/ingest.py",
    "tests/test_e2e.py",
    "workflow-trust-plan.md",
    "scripts/other_tool.py",
    "CLAUDE.md",
]


@pytest.mark.parametrize("path", BLOCKED_PATHS)
def test_path_guard_blocks(path, tmp_path):
    for candidate in (path, str(tmp_path / path)):  # relative and absolute
        res = run_guard(PATH_GUARD, {"file_path": candidate}, project_dir=tmp_path)
        assert res.returncode == 2, f"should block: {candidate}"
        assert "BLOCKED" in res.stderr


@pytest.mark.parametrize("path", ALLOWED_PATHS)
def test_path_guard_allows(path, tmp_path):
    res = run_guard(PATH_GUARD, {"file_path": path}, project_dir=tmp_path)
    assert res.returncode == 0, f"should allow: {path} ({res.stderr})"


def test_path_guard_human_override_opens_session(tmp_path):
    (tmp_path / ".guard-override").touch()
    res = run_guard(
        PATH_GUARD, {"file_path": ".githooks/pre-commit"}, project_dir=tmp_path
    )
    assert res.returncode == 0


# --- stop gate (script-level equivalent of matrix row 12) ---


@pytest.fixture
def stop_repo(tmp_path):
    """Minimal repo with a toggleable stub pytest for stop_gate.sh."""
    work = tmp_path / "work"
    work.mkdir()
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=work, capture_output=True, text=True,
            env={**os.environ, **env},
        )

    git("init", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    (work / "src").mkdir()
    (work / "src" / "m.py").write_text("X = 1\n")
    git("add", "-A")
    git("commit", "-m", "seed")
    venv_bin = work / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    stub = venv_bin / "pytest"
    stub.write_text(
        '#!/bin/sh\nd="$(dirname "$0")"\n'
        'if [ "$(cat "$d/.pytest_result" 2>/dev/null)" = "fail" ]; then\n'
        '  echo "1 failed in 0.01s"; exit 1\nfi\necho "3 passed in 0.01s"; exit 0\n'
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    (venv_bin / ".pytest_result").write_text("pass")
    return work


def run_stop_gate(work, extra_env=None):
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(work)
    # Never inherit the recursion guard: inside a reviewer session (or any shell
    # exporting it) stop_gate no-ops and every assertion here would be vacuous
    # — the tests must control it explicitly (round-7 minor).
    env.pop("VA_AGENT_REVIEW", None)
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(STOP_GATE)], capture_output=True, text=True, env=env,
    )


def test_stop_gate_green_then_cached(stop_repo):
    (stop_repo / "src" / "m.py").write_text("X = 2\n")  # change triggers a run
    res = run_stop_gate(stop_repo)
    assert res.returncode == 0
    assert (stop_repo / ".git" / ".stop-gate-green").exists()
    # Unchanged state: cache hit, still allowed (and no re-run needed).
    assert run_stop_gate(stop_repo).returncode == 0


def test_stop_gate_cache_invalidated_by_commit(stop_repo):
    """Regression for the PR 3 review major: a clean tree after a commit must not
    reuse a green cached before the commit — the cache key includes HEAD."""
    res = run_stop_gate(stop_repo)  # clean tree, green suite: cache a green
    assert res.returncode == 0
    # Commit red code as wip: the tree is clean again, but HEAD moved.
    (stop_repo / "src" / "m.py").write_text("X = 99\n")
    (stop_repo / ".venv" / "bin" / ".pytest_result").write_text("fail")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_CONFIG_SYSTEM": "/dev/null"}
    subprocess.run(["git", "add", "-A"], cwd=stop_repo, env=env,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "wip: red"], cwd=stop_repo, env=env,
                   capture_output=True)
    res = run_stop_gate(stop_repo)
    assert res.returncode == 2  # cache miss: pytest ran and the suite is red
    assert "offline suite is red" in res.stderr


def test_stop_gate_blocks_red_suite(stop_repo):
    (stop_repo / "src" / "m.py").write_text("X = 3\n")
    (stop_repo / ".venv" / "bin" / ".pytest_result").write_text("fail")
    res = run_stop_gate(stop_repo)
    assert res.returncode == 2
    assert "offline suite is red" in res.stderr


def test_stop_gate_cache_tracks_untracked_file_content(stop_repo):
    """Regression for the round-2 major: editing an already-untracked file must
    invalidate the cache — names alone (git status) are not enough."""
    (stop_repo / "src" / "untracked.py").write_text("A = 1\n")
    assert run_stop_gate(stop_repo).returncode == 0  # green cached, file listed
    (stop_repo / "src" / "untracked.py").write_text("A = 2  # now red\n")
    (stop_repo / ".venv" / "bin" / ".pytest_result").write_text("fail")
    res = run_stop_gate(stop_repo)
    assert res.returncode == 2  # content change = cache miss, red suite blocks
    assert "offline suite is red" in res.stderr


def test_stop_gate_cache_covers_gate_machinery_paths(stop_repo):
    """Round-3 review major: the suite tests .claude/hooks, .githooks and
    scripts too — editing them must invalidate the cache."""
    hooks = stop_repo / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "guard.py").write_text("OK = True\n")
    assert run_stop_gate(stop_repo).returncode == 0  # green cached
    (hooks / "guard.py").write_text("OK = False\n")
    (stop_repo / ".venv" / "bin" / ".pytest_result").write_text("fail")
    res = run_stop_gate(stop_repo)
    assert res.returncode == 2  # machinery edit = cache miss, red blocks
    assert "offline suite is red" in res.stderr


def test_stop_gate_noop_in_reviewer_session(stop_repo):
    (stop_repo / "src" / "m.py").write_text("X = 4\n")
    (stop_repo / ".venv" / "bin" / ".pytest_result").write_text("fail")
    res = run_stop_gate(stop_repo, {"VA_AGENT_REVIEW": "1"})
    assert res.returncode == 0  # recursion guard wins even with a red suite


# --- critical-path label gate (WT.7; matrix row 16's automatable half) ---


@pytest.fixture
def cp_repo(tmp_path):
    """Repo with the real critical_paths table + checker, a base commit, and a
    branch whose diff the checker inspects."""
    work = tmp_path / "cp"
    work.mkdir()
    # LC_ALL=C: these tests assert on git's own wording ("fatal:", "warning:"),
    # which git localizes — without it a flake-fix test is itself locale-flaky.
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_CONFIG_SYSTEM": "/dev/null", "LC_ALL": "C", "LANGUAGE": ""}

    def git(*args):
        """Every setup command must SUCCEED. Silently ignoring git failures here
        turned a CI flake into three rounds of guesswork: the fixture carried on
        after a failed command and the test blew up later somewhere unrelated."""
        res = subprocess.run(["git", *args], cwd=work, capture_output=True,
                             text=True, env=env)
        assert res.returncode == 0, (
            f"fixture git {' '.join(args)!r} failed ({res.returncode}): "
            f"{res.stderr.strip()}")
        return res

    git("init", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    # test_large_changeset_does_not_fail_open makes ~900 commits in a few
    # seconds, which lands near git's gc.auto loose-object threshold (6700).
    # A DETACHED background `gc --auto` mutating the object store while the
    # checker's `git diff` reads it is a real source of nondeterminism, and this
    # fixture is the only one in the suite that gets anywhere near it.
    git("config", "gc.auto", "0")
    (work / "scripts").mkdir()
    for name in ("critical_paths.txt", "check_critical_paths.sh"):
        shutil.copy(REPO_ROOT / "scripts" / name, work / "scripts" / name)
    (work / "scripts" / "check_critical_paths.sh").chmod(0o755)
    (work / "README.md").write_text("seed\n")
    git("add", "-A")
    git("commit", "-m", "seed")
    base = git("rev-parse", "HEAD").stdout.strip()

    def check(labels="", base_sha=None):
        res = subprocess.run(
            ["bash", "scripts/check_critical_paths.sh", base_sha or base, labels],
            cwd=work, capture_output=True, text=True, env=env,
        )
        # Fixture-level, so EVERY cp_repo assertion inherits it: capture_output
        # is where three CI diagnoses of this gate were lost — git's real message
        # sat unread in res.stderr while the assertion showed only stdout.
        # pytest prints captured stdout only when the test fails, so this costs
        # nothing on green and hands the next failure its own evidence.
        if res.stderr:
            print(f"[check_critical_paths.sh stderr]\n{res.stderr}")
        return res

    def change(rel, content="x = 1\n"):
        p = work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        git("add", "-A")
        git("commit", "-m", f"wip: touch {rel}")

    class CpRepo:
        root = work
        __truediv__ = staticmethod(lambda rel: work / rel)

    def git_mv(src, dst):
        git("mv", src, dst)
        git("commit", "-m", f"wip: rename {src}")

    def mark_base():
        """Re-anchor the comparison base at the current HEAD — needed when a test
        must have a file ALREADY on main before the branch touches it."""
        nonlocal base
        base = git("rev-parse", "HEAD").stdout.strip()

    def set_config(key, value):
        git("config", key, value)

    CpRepo.set_config = staticmethod(set_config)
    CpRepo.git_mv = staticmethod(git_mv)
    CpRepo.mark_base = staticmethod(mark_base)
    CpRepo.check = staticmethod(check)
    CpRepo.change = staticmethod(change)
    return CpRepo()


def test_critical_path_requires_its_label(cp_repo):
    cp_repo.change("src/va/storage/structured/schema.py")
    res = cp_repo.check("")
    assert res.returncode == 1
    assert "lacks label 'human-reviewed'" in res.stdout
    res = cp_repo.check("human-reviewed")
    assert res.returncode == 0


def test_golden_verified_label_for_adapters(cp_repo):
    cp_repo.change("src/va/adapters/reasoner/x_inproc.py")
    assert cp_repo.check("human-reviewed").returncode == 1  # wrong label
    assert cp_repo.check("golden-verified").returncode == 0


def test_non_critical_change_needs_no_label(cp_repo):
    cp_repo.change("docs/notes.md", "# notes\n")
    assert cp_repo.check("").returncode == 0


def test_comments_and_blank_lines_are_ignored(cp_repo):
    """A '#' line must never be read as a pattern (it would match nothing, but a
    naive loop could also read its trailing words as a label)."""
    cp_repo.change("README.md", "seed 2\n")
    res = cp_repo.check("")
    assert res.returncode == 0
    assert "FAIL" not in res.stdout


def test_multiple_labels_on_one_pr(cp_repo):
    cp_repo.change("src/va/cli.py")
    cp_repo.change("config/roles.yaml", "x: 1\n")
    assert cp_repo.check("human-reviewed").returncode == 1
    assert cp_repo.check("human-reviewed golden-verified").returncode == 0


def test_missing_table_fails_closed(cp_repo):
    (cp_repo / "scripts" / "critical_paths.txt").unlink()
    res = cp_repo.check("human-reviewed golden-verified")
    assert res.returncode == 1
    assert "missing" in res.stdout


def test_prefix_match_is_anchored_not_substring(cp_repo):
    """`web/scripts/app.js` must NOT trigger the `scripts/` rule (round: PR 4
    backstop minor — grep -F was unanchored substring matching)."""
    cp_repo.change("web/scripts/app.js", "// x\n")
    assert cp_repo.check("").returncode == 0


def test_unreachable_base_reports_gits_own_error(cp_repo):
    """A diff failure must print what GIT said, not a presumed cause.

    The message used to assert one explanation ("fetch-depth: 0 required in
    CI"), so three consecutive CI failures were diagnosed by guesswork and the
    actual reason was never recorded anywhere. Whatever git's wording is on a
    given version, its `fatal:` line has to reach the operator.
    """
    res = cp_repo.check("", base_sha="deadbeef" * 5)

    assert res.returncode == 1
    assert "git said" in res.stdout
    assert "fatal:" in res.stdout, (
        f"git's own error was swallowed; operator sees only a guess:\n{res.stdout}")
    # ...and no eliminated hypothesis alongside it. The critical-paths job
    # already checks out with fetch-depth: 0, so that hint sent three diagnoses
    # to a cause that cannot apply.
    assert "fetch-depth" not in res.stdout, (
        f"a ruled-out cause is being suggested again:\n{res.stdout}")
    # ...and the conditional hint's TRUE arm: git's words DO implicate the
    # revision here, so the checker must say what it actually needs. Only the
    # negative arm was pinned, so narrowing the match pattern would have gone
    # unnoticed until an operator hit a force-pushed base with no pointer.
    assert "exist in this checkout" in res.stdout, (
        f"the base-sha requirement was not stated:\n{res.stdout}")


def test_a_silent_git_death_is_not_blamed_on_the_base_sha(cp_repo):
    """The scenario that most likely IS the open flake: git dies without saying
    anything (OOM-killed on a loaded runner — status 137 — is the usual way).

    Printing the shallow-clone/force-push explanation unconditionally would put a
    confident cause under an empty message, which is precisely the misattribution
    this change exists to remove. The exit status is the diagnosis here.
    """
    fake_bin = cp_repo / "fakebin"
    fake_bin.mkdir()
    real_git = shutil.which("git")
    (fake_bin / "git").write_text(
        f'#!/usr/bin/env bash\n'
        f'for a in "$@"; do [ "$a" = diff ] && exit 137; done\n'
        f'exec {real_git} "$@"\n'
    )
    (fake_bin / "git").chmod(0o755)

    res = subprocess.run(
        ["bash", "scripts/check_critical_paths.sh", "HEAD", ""],
        cwd=cp_repo.root, capture_output=True, text=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
             "GIT_CONFIG_SYSTEM": "/dev/null", "LC_ALL": "C",
             "PATH": f"{fake_bin}:{os.environ['PATH']}"},
    )

    detail = f"\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    assert res.returncode == 1, detail
    assert "git exited 137" in res.stdout, f"the status is the diagnosis:{detail}"
    assert "KILLED by signal" in res.stdout, detail
    assert "shallow" not in res.stdout, (
        f"a cause was presumed under an empty git message:{detail}")


def test_a_non_signal_death_invents_neither_a_signal_nor_a_cause(cp_repo):
    """The other two failure arms: exit <=128, and stderr that says something
    unrelated to the revision.

    Both were added to stop the checker inventing causes, and both were
    uncovered. Dropping the `rc > 128` guard would print "KILLED by signal 0"
    for a disk-full exit 128; widening the hint's match would blame the base sha
    for an unrelated error. Green suites hid each of those once already.
    """
    fake_bin = cp_repo / "fakebin2"
    fake_bin.mkdir()
    real_git = shutil.which("git")
    (fake_bin / "git").write_text(
        f'#!/usr/bin/env bash\n'
        f'for a in "$@"; do\n'
        f'  if [ "$a" = diff ]; then echo "error: unable to write file" >&2; exit 128; fi\n'
        f'done\n'
        f'exec {real_git} "$@"\n'
    )
    (fake_bin / "git").chmod(0o755)

    res = subprocess.run(
        ["bash", "scripts/check_critical_paths.sh", "HEAD", ""],
        cwd=cp_repo.root, capture_output=True, text=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
             "GIT_CONFIG_SYSTEM": "/dev/null", "LC_ALL": "C",
             "PATH": f"{fake_bin}:{os.environ['PATH']}"},
    )

    detail = f"\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    assert res.returncode == 1, detail
    assert "git exited 128" in res.stdout, detail
    assert "unable to write file" in res.stdout, f"git's words dropped:{detail}"
    assert "exist in this checkout" not in res.stdout, (
        f"an unrelated error was blamed on the base sha:{detail}")

    # SECOND case, and it is the one that guards `rc > 128`: the signal wording
    # lives in the EMPTY-stderr branch, so a fake git that writes to stderr (as
    # above) never reaches it. Written the obvious way, this test passed with the
    # guard replaced by `if true` — it has to be exit<=128 AND silent.
    silent = cp_repo / "fakebin3"
    silent.mkdir()
    (silent / "git").write_text(
        f'#!/usr/bin/env bash\n'
        f'for a in "$@"; do [ "$a" = diff ] && exit 128; done\n'
        f'exec {real_git} "$@"\n'
    )
    (silent / "git").chmod(0o755)

    res2 = subprocess.run(
        ["bash", "scripts/check_critical_paths.sh", "HEAD", ""],
        cwd=cp_repo.root, capture_output=True, text=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
             "GIT_CONFIG_SYSTEM": "/dev/null", "LC_ALL": "C",
             "PATH": f"{silent}:{os.environ['PATH']}"},
    )

    detail2 = f"\nstdout:\n{res2.stdout}\nstderr:\n{res2.stderr}"
    assert res2.returncode == 1, detail2
    assert "printed nothing and exited 128" in res2.stdout, detail2
    assert "KILLED by signal" not in res2.stdout, (
        f"a signal was invented for a non-signal exit:{detail2}")


def test_a_temp_file_failure_is_named_and_fails_closed(cp_repo):
    """The mktemp branch, in a change whose premise is that this gate's failure
    messages must be accurate.

    Two things matter and neither is free: the message must name the real
    problem (unchecked, a full /tmp gets reported as a base-sha failure), and it
    must exit NON-zero — a refactor dropping the `|| { … exit 1; }` would let the
    gate scan nothing and pass, which is fail-open on every critical path.
    """
    res = subprocess.run(
        ["bash", "scripts/check_critical_paths.sh", "HEAD", ""],
        cwd=cp_repo.root, capture_output=True, text=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
             "GIT_CONFIG_SYSTEM": "/dev/null", "LC_ALL": "C",
             "TMPDIR": str(cp_repo / "no-such-dir")},
    )

    detail = f"\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    assert res.returncode == 1, f"a temp-file failure must fail CLOSED:{detail}"
    assert "temp file" in res.stdout, f"the real cause is not named:{detail}"
    assert "cannot diff against" not in res.stdout, (
        f"a disk problem is being reported as a base-sha problem:{detail}")


def test_git_warnings_reach_the_log_without_entering_the_path_list(cp_repo):
    """The success-path stderr replay: git's warning reaches the log, and does
    not appear in the gate's own report.

    A critical path is touched (with its label) so the checker actually WRITES a
    report line: without that, stdout is empty in this scenario and the "stays
    out of the report" assertion is vacuous — it would pass even if the replay
    were moved to stdout.

    (A skipped rename detection is not itself a gate hole: --name-status reports
    an undetected rename as D <old> + A <new>, so the old path is scanned anyway.)
    """
    for i in range(40):
        (cp_repo / f"f{i}.txt").write_text(f"line{i}\nbody {i}\ncommon tail\n")
    cp_repo.change("seed.txt")          # commits the 40 files too
    cp_repo.mark_base()
    for i in range(40):                 # rename AND edit -> inexact detection
        cp_repo.git_mv(f"f{i}.txt", f"g{i}.txt")
        (cp_repo / f"g{i}.txt").write_text(f"line{i}\nbody {i} CHANGED\ntail\n")
    cp_repo.change("g0.txt", "line0\nbody 0 CHANGED\ntail\n")
    cp_repo.change("src/va/cli.py")     # so stdout carries a real report line
    cp_repo.set_config("diff.renameLimit", "1")

    res = cp_repo.check("human-reviewed")

    detail = f"\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    # The verdict itself: a warning must not make the gate fail closed (it would
    # if the line ever survived into the scanned list and prefix-matched a
    # pattern — spurious red on every rename-heavy PR).
    assert res.returncode == 0, detail
    assert "src/va/cli.py" in res.stdout, f"report line missing:{detail}"
    assert "warning:" in res.stderr, f"git's warning never reached the log:{detail}"
    assert res.stderr.count("git: ") >= 1, f"replay prefix missing:{detail}"
    assert "warning:" not in res.stdout, (
        f"a git warning leaked into the scanned output:{detail}")


def test_large_changeset_does_not_fail_open(cp_repo):
    """SIGPIPE regression. The list must EXCEED the 64 KB pipe buffer: the first
    version of this test wrote ~14 KB, which FITS, so the buggy piped
    implementation it names would have passed it (PR 4 backstop minor)."""
    long_seg = "d" * 90
    for i in range(900):                  # ~900 x ~110 B = ~99 KB > 64 KB buffer
        cp_repo.change(f"src/va/{long_seg}/mod_{i}.py", "x = 1\n")
    cp_repo.change("src/va/cli.py")
    res = cp_repo.check("")
    # git's stderr goes in the failure message on purpose: capture_output hides
    # it, and THAT — not the checker — is where the evidence for three CI
    # failures of this very test was lost (the assertion only showed stdout).
    detail = f"\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    assert res.returncode == 1, detail
    assert "src/va/cli.py" in res.stdout, detail


def test_non_ascii_and_spaced_paths_are_matched(cp_repo):
    """core.quotepath C-quotes non-ASCII paths ("sch\\303\\251ma.py") and naive
    whitespace splitting fragments paths with spaces — both fail OPEN on exactly
    the files this gate exists to catch (PR 4 finalize-round minor)."""
    cp_repo.change("src/va/contracts/sch\u00e9ma.py")
    assert cp_repo.check("").returncode == 1
    assert cp_repo.check("human-reviewed").returncode == 0
    cp_repo.change("src/va/contracts/my model.py")
    assert cp_repo.check("").returncode == 1


def test_rename_of_a_critical_file_is_caught(cp_repo):
    """`git diff --name-only` reports only a rename's DESTINATION, so renaming a
    critical file escaped every exact-file pattern (PR 4 backstop minor)."""
    cp_repo.change("src/va/cli.py")   # exists on main...
    cp_repo.mark_base()               # ...before this branch starts
    cp_repo.git_mv("src/va/cli.py", "src/va/cli_main.py")
    res = cp_repo.check("")
    assert res.returncode == 1
    assert "src/va/cli.py" in res.stdout
