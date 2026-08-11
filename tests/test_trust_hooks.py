"""Trust-layer self-tests (workflow-trust-plan.md WT.10).

Drives dummy commits/pushes through the REAL hook scripts inside a sandbox git
repo (bare origin + working clone, hooks active via core.hooksPath). Two fakes
keep this offline and deterministic:
- a stub `.venv/bin/pytest` the tests toggle green/red (pre-push Gate 1), and
- a fake `claude` binary on PATH emitting a canned verdict (the reviewer), so
  the full need_agent_review lifecycle runs with no LLM, network, or login.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ZERO_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}

FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
# Like the real headless claude, consume piped stdin to EOF — this is what
# exposed the pre-push stdin-swallowing bug (round-3 major finding).
if not sys.stdin.isatty():
    sys.stdin.read()
v = "approve"
p = os.environ.get("FAKE_VERDICT_FILE")
if p and os.path.exists(p):
    v = open(p).read().strip() or "approve"
if v == "fail-run":
    sys.stderr.write("simulated headless crash\\n")
    sys.exit(2)
findings = []
if v == "request_changes":
    findings = [{"severity": "major", "file": "src/pkg/mod.py", "line": 2,
                 "issue": "planted bug", "scenario": "boom"}]
inner = json.dumps({"verdict": v, "findings": findings})
print(json.dumps({"result": "review done\\n```json\\n" + inner + "\\n```"}))
"""


class TrustRepo:
    def __init__(self, root: Path, base_env: dict):
        self.root = root
        self.base_env = base_env

    def run(self, *cmd, env=None, text_input=None):
        merged = os.environ.copy()
        # Never inherit the recursion guard: it short-circuits post-commit, so
        # inside a reviewer session (agent-review.sh exports it) 5 tests here
        # would fail and the suite would report a false red — the suite this PR
        # makes the required check and /verify's evidence artifact must not be
        # environment-dependent. Tests set it explicitly when they mean it.
        merged.pop("VA_AGENT_REVIEW", None)
        merged.update(ZERO_ENV)
        merged.update(self.base_env)
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

    def amend(self, msg, env=None):
        return self.git("commit", "--amend", "-m", msg, env=env)

    def push(self, *args, env=None):
        return self.git("push", *args, env=env)

    def set_suite(self, passing: bool):
        (self.root / ".venv/bin/.pytest_result").write_text(
            "pass" if passing else "fail"
        )

    def set_suite_raw(self, mode: str):
        (self.root / ".venv/bin/.pytest_result").write_text(mode)

    def set_reviewer_verdict(self, verdict: str):
        Path(self.base_env["FAKE_VERDICT_FILE"]).write_text(verdict)

    def last_message(self):
        return self.git("log", "-1", "--pretty=%B").stdout

    def approved_hash_file(self):
        return self.root / ".git" / ".review-approved"

    def ledgers(self):
        d = self.root / "reviews"
        return sorted(p for p in d.glob("*.md")) if d.exists() else []


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

    # Fakes live OUTSIDE the sandbox repo so they never pollute git state.
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(FAKE_CLAUDE)
    fake_claude.chmod(0o755)
    verdict_file = tmp_path / "verdict.txt"
    base_env = {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "FAKE_VERDICT_FILE": str(verdict_file),
    }

    repo = TrustRepo(work, base_env)
    repo.git("config", "user.email", "trust-test@example.com")
    repo.git("config", "user.name", "Trust Test")
    repo.git("checkout", "-b", "main")
    # Seed main WITH the real hooks/scripts committed (parity with the real repo,
    # where they are tracked — untracked infra would defeat the docs-only checks)
    # and push BEFORE activating hooks so origin/main exists as the baseline.
    shutil.copytree(REPO_ROOT / ".githooks", work / ".githooks")
    (work / "scripts").mkdir(exist_ok=True)
    for script in (REPO_ROOT / "scripts").glob("*.sh"):
        shutil.copy(script, work / "scripts" / script.name)
    # agent-review.sh single-sources its rubric from the reviewer agent file
    # (WT.11) and fails closed without it — sandbox parity requires the copy.
    (work / ".claude" / "agents").mkdir(parents=True)
    shutil.copy(
        REPO_ROOT / ".claude" / "agents" / "code-reviewer.md",
        work / ".claude" / "agents" / "code-reviewer.md",
    )
    for hook in (work / ".githooks").iterdir():
        hook.chmod(0o755)
    for script in (work / "scripts").iterdir():
        script.chmod(0o755)
    repo.write("README.md", "seed\n")
    repo.write(
        ".gitignore",
        "__pycache__/\n*.pyc\n.venv/\n.commit-approved\n.guard-override\n",
    )
    (work / "reviews").mkdir()
    (work / "reviews" / ".gitkeep").touch()
    repo.add_all()
    assert repo.commit("seed").returncode == 0
    assert repo.push("-u", "origin", "main").returncode == 0
    repo.git("config", "core.hooksPath", ".githooks")

    # Stub venv: pytest result toggled per-test; python is the real interpreter
    # (the pre-commit syntax gate genuinely compiles staged files).
    venv_bin = work / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    stub = venv_bin / "pytest"
    stub.write_text(
        '#!/bin/sh\nd="$(dirname "$0")"\n'
        'case "$(cat "$d/.pytest_result" 2>/dev/null)" in\n'
        '  fail) echo "1 failed, 4 passed in 0.01s"; exit 1;;\n'
        '  error) echo "33 passed, 1 error in 0.02s"; exit 1;;\n'
        '  *) echo "5 passed in 0.01s"; exit 0;;\n'
        'esac\n'
    )
    stub.chmod(0o755)
    repo.set_suite(True)
    os.symlink(sys.executable, venv_bin / "python")
    repo.set_reviewer_verdict("approve")

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
    res = r.commit("wip: add config")
    assert res.returncode != 0
    assert "possible secret" in res.stderr


def test_artifact_guard_blocks_workdir_files(trust_repo):
    r = trust_repo
    r.write(".va/catalog.db", "not a real db")
    r.add_all()
    res = r.commit("wip: oops workdir")
    assert res.returncode != 0
    assert "artifacts staged" in res.stderr


def test_net_test_deletion_blocked_and_human_overridable(trust_repo):
    r = trust_repo
    r.write(
        "tests/test_sample.py",
        "def test_a():\n    assert True\n\n\ndef test_b():\n    assert True\n",
    )
    r.add_all()
    assert r.commit("wip: add tests").returncode == 0
    r.write("tests/test_sample.py", "def test_a():\n    assert True\n")
    r.add_all()
    res = r.commit("wip: trim tests")
    assert res.returncode != 0
    assert "net test deletion" in res.stderr
    res = r.commit("wip: trim tests", env={"ALLOW_TEST_REMOVAL": "1"})
    assert res.returncode == 0


def test_syntax_gate_blocks_uncompilable_python(trust_repo):
    r = trust_repo
    r.write("src/broken.py", "def f(:\n")
    r.add_all()
    res = r.commit("wip: add broken module")
    assert res.returncode != 0
    assert "does not compile" in res.stderr


# --- commit-msg (matrix rows 5, 23, 26, 28) ---


def test_commit_msg_rewrites_trailers(trust_repo):
    r = trust_repo
    r.write("g.txt", "y\n")
    r.add_all()
    msg = "feat: thing\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
    res = r.git("commit", "-m", msg)
    assert res.returncode == 0  # docs-only branch: plain subject allowed
    body = r.last_message()
    assert "Co-Authored-By" not in body
    assert "Signed-off-by: Deepak Gupta (deepak0414) using Claude assistance" in body


def test_plain_subject_blocked_on_code_branch(trust_repo):
    r = trust_repo
    r.write("src/pkg/mod.py", "def f():\n    return 1\n")
    r.add_all()
    res = r.commit("feat: add f")
    assert res.returncode != 0
    assert "commit-msg BLOCKED" in res.stderr


def test_tag_in_body_not_subject_blocked(trust_repo):
    r = trust_repo
    r.write("src/pkg/mod.py", "def f():\n    return 1\n")
    r.add_all()
    res = r.commit("feat: add f\n\nneed_agent_review\n")
    assert res.returncode != 0
    assert "commit-msg BLOCKED" in res.stderr


def test_docs_only_plain_subject_allowed(trust_repo):
    r = trust_repo
    r.write("notes.md", "# notes\n")
    r.add_all()
    assert r.commit("docs: add notes").returncode == 0


# --- the review lifecycle (matrix rows 17-20, 24, 27) ---


def test_lifecycle_provisional_review_approve_finalize_push(trust_repo):
    r = trust_repo
    r.write("src/pkg/mod.py", "def f():\n    return 1\n")
    r.add_all()

    r.set_reviewer_verdict("request_changes")
    res = r.commit("need_agent_review: add f")
    assert res.returncode == 0  # post-commit is a trigger, not a gate
    assert "REQUESTED CHANGES" in res.stderr
    assert "planted bug" in r.ledgers()[-1].read_text()
    assert not r.approved_hash_file().exists()

    r.set_reviewer_verdict("approve")
    res = r.amend("need_agent_review: add f")  # fix loop re-fires the review
    assert res.returncode == 0
    assert "review APPROVED" in res.stderr
    assert r.approved_hash_file().exists()

    res = r.amend("feat: add f")  # no sentinel -> finalization blocked
    assert res.returncode != 0
    assert "commit-msg BLOCKED" in res.stderr

    (r.root / ".commit-approved").touch()  # the human's act
    r.git("add", "reviews")  # ledger ships inside the finalized commit
    res = r.amend("feat: add f")
    assert res.returncode == 0
    assert not (r.root / ".commit-approved").exists()  # sentinel consumed
    assert r.last_message().startswith("feat: add f")

    res = r.push("-u", "origin", "feature/x")
    assert res.returncode == 0
    assert "skipping re-review" in res.stderr  # approval hash honored at push


def test_provisional_commit_cannot_be_pushed(trust_repo):
    r = trust_repo
    r.write("src/pkg/mod.py", "def f():\n    return 1\n")
    r.add_all()
    assert r.commit("need_agent_review: add f").returncode == 0  # approve verdict
    res = r.push("-u", "origin", "feature/x")
    assert res.returncode != 0
    assert "provisional" in res.stderr


def test_wip_only_branch_gets_backstop_review_at_push(trust_repo):
    r = trust_repo
    r.write("src/pkg/mod.py", "def f():\n    return 1\n")
    r.add_all()
    assert r.commit("wip: partial work").returncode == 0
    assert r.ledgers() == []  # wip never triggers post-commit review
    res = r.push("-u", "origin", "feature/x")
    assert res.returncode == 0
    assert len(r.ledgers()) == 1  # the backstop reviewed it


def test_backstop_blocks_push_on_request_changes(trust_repo):
    r = trust_repo
    r.write("src/pkg/mod.py", "def f():\n    return 1\n")
    r.add_all()
    assert r.commit("wip: partial work").returncode == 0
    r.set_reviewer_verdict("request_changes")
    res = r.push("-u", "origin", "feature/x")
    assert res.returncode != 0
    assert "requested changes" in res.stderr


def test_human_waiver_is_recorded(trust_repo):
    r = trust_repo
    r.write("src/pkg/mod.py", "def f():\n    return 1\n")
    r.add_all()
    assert r.commit("wip: partial work").returncode == 0
    res = r.push("-u", "origin", "feature/x", env={"AGENT_REVIEW": "skip"})
    assert res.returncode == 0
    assert "WAIVED" in r.ledgers()[-1].read_text()


def test_dirty_worktree_edits_are_never_blessed_by_an_approval(trust_repo):
    """Regression for the first real review's major finding: the approval hash
    must cover exactly the reviewed committed scope, so uncommitted edits present
    at approval time cannot later ship under 'skipping re-review'."""
    r = trust_repo
    r.write("src/pkg/mod.py", "def f():\n    return 1\n")
    r.add_all()
    assert r.commit("need_agent_review: add f").returncode == 0  # approved
    # Dirty tracked edit, present but uncommitted at approval/finalize time.
    r.write("src/pkg/mod.py", "def f():\n    return 2  # sneak\n")
    (r.root / ".commit-approved").touch()
    assert r.amend("feat: add f").returncode == 0  # dirty file doesn't block
    # Now commit the never-reviewed edit as wip and push: the backstop MUST fire.
    r.add_all()
    assert r.commit("wip: sneak").returncode == 0
    before = len(r.ledgers())
    res = r.push("-u", "origin", "feature/x")
    assert res.returncode == 0
    assert "skipping re-review" not in res.stderr
    assert len(r.ledgers()) == before + 1  # backstop reviewed the sneak


def test_finalize_amend_cannot_stage_extra_content(trust_repo):
    r = trust_repo
    r.write("src/pkg/mod.py", "def f():\n    return 1\n")
    r.add_all()
    assert r.commit("need_agent_review: add f").returncode == 0  # approved
    r.write("src/pkg/extra.py", "X = 1\n")
    r.git("add", "src/pkg/extra.py")  # sneak content into the finalize amend
    (r.root / ".commit-approved").touch()
    res = r.amend("feat: add f")
    assert res.returncode != 0
    assert "commit-msg BLOCKED" in res.stderr


def test_reviews_dir_is_ledgers_only(trust_repo):
    r = trust_repo
    r.write("reviews/evil.py", "X = 1\n")
    r.add_all()
    res = r.commit("wip: ledger tidy")
    assert res.returncode != 0
    assert "only .md ledgers" in res.stderr


def test_smuggled_reviews_file_cannot_ride_an_approval(trust_repo):
    """Regression for round-2 major #1: a non-.md file under reviews/ (snuck past
    local hooks with --no-verify) must still hit the push backstop — the approval
    hash only excludes reviews/*.md."""
    r = trust_repo
    r.write("src/pkg/mod.py", "def f():\n    return 1\n")
    r.add_all()
    assert r.commit("need_agent_review: add f").returncode == 0  # approved
    (r.root / ".commit-approved").touch()
    r.git("add", "reviews")
    assert r.amend("feat: add f").returncode == 0
    r.write("reviews/evil.py", "X = 1\n")
    r.git("add", "reviews/evil.py")
    assert r.git("commit", "--no-verify", "-m", "wip: tidy").returncode == 0
    before = len(r.ledgers())
    res = r.push("-u", "origin", "feature/x")
    assert res.returncode == 0
    assert "skipping re-review" not in res.stderr
    assert len(r.ledgers()) == before + 1  # backstop reviewed the smuggle


def test_pushing_non_head_ref_hashes_that_ref(trust_repo):
    """Regression for round-2 major #2: Gate 2 must hash the PUSHED sha, not HEAD,
    or an approved checked-out branch would bless an unreviewed sibling ref."""
    r = trust_repo
    r.write("src/pkg/mod.py", "def f():\n    return 1\n")
    r.add_all()
    assert r.commit("need_agent_review: add f").returncode == 0  # approved
    (r.root / ".commit-approved").touch()
    r.git("add", "reviews")
    assert r.amend("feat: add f").returncode == 0
    r.git("checkout", "-b", "feature/y")
    r.write("src/pkg/other.py", "Y = 2\n")
    r.add_all()
    assert r.commit("wip: unreviewed extra").returncode == 0
    r.git("checkout", "feature/x")  # approved branch checked out...
    before = len(r.ledgers())
    res = r.push("origin", "feature/y")  # ...but pushing the sibling
    assert res.returncode == 0
    assert "skipping re-review" not in res.stderr
    assert len(r.ledgers()) == before + 1  # feature/y got its own review


def test_plain_docs_subject_fails_closed_without_origin_main(trust_repo):
    r = trust_repo
    r.git("update-ref", "-d", "refs/remotes/origin/main")
    r.write("notes.md", "# notes\n")
    r.add_all()
    res = r.commit("docs: add notes")
    assert res.returncode != 0
    assert "commit-msg BLOCKED" in res.stderr


def test_reviewer_rubric_is_single_sourced_from_agent_file(trust_repo):
    """Matrix row 106: an edit to the reviewer agent file must appear in the
    prompt agent-review.sh assembles — the rubric cannot drift between the
    interactive twin and the enforced headless path."""
    # (Also covers the round-1 finding: print mode must ignore a lingering
    # waiver env var instead of writing a spurious WAIVED ledger.)
    r = trust_repo
    marker = "DRIFT-CANARY-9a7f: flag any use of the frobnicate() helper."
    agent_file = r.root / ".claude" / "agents" / "code-reviewer.md"
    agent_file.write_text(agent_file.read_text() + f"\n{marker}\n")
    before = len(r.ledgers())
    res = r.run(
        "bash", "scripts/agent-review.sh", "--print-prompt", "--worktree",
        env={"AGENT_REVIEW": "skip"},  # print mode must ignore a lingering waiver
    )
    assert res.returncode == 0
    assert marker in res.stdout
    assert "Review ONLY" in res.stdout  # scope appendix still present
    assert len(r.ledgers()) == before  # no spurious WAIVED ledger from print mode


def test_missing_rubric_fails_closed(trust_repo):
    r = trust_repo
    (r.root / ".claude" / "agents" / "code-reviewer.md").write_text("---\n")
    res = r.run("bash", "scripts/agent-review.sh", "--print-prompt", "--worktree")
    assert res.returncode != 0
    assert "fail-closed" in res.stderr


def test_recursion_guard_disables_hooks_in_reviewer_session(trust_repo):
    r = trust_repo
    r.write("src/pkg/mod.py", "def f():\n    return 1\n")
    r.add_all()
    res = r.commit("need_agent_review: add f", env={"VA_AGENT_REVIEW": "1"})
    assert res.returncode == 0
    assert r.ledgers() == []  # no review fired inside a reviewer session
    assert not r.approved_hash_file().exists()


def test_multi_ref_push_gates_every_ref(trust_repo):
    """Regression for round-3 major: a live review reading the hook's stdin must
    not swallow the remaining pushed-ref lines (which would fail OPEN for them)."""
    r = trust_repo
    r.write("src/pkg/mod.py", "def f():\n    return 1\n")
    r.add_all()
    assert r.commit("wip: branch a work").returncode == 0  # triggers backstop review
    r.git("checkout", "-b", "feature/z")
    r.write("src/pkg/other.py", "Y = 2\n")
    r.add_all()
    assert r.commit("need_agent_review: unfinished lifecycle").returncode == 0
    r.git("checkout", "feature/x")
    # Push BOTH refs: feature/x runs a stdin-eating review; feature/z's provisional
    # subject must still be seen by Gate 3 and block the push.
    res = r.push("origin", "feature/x", "feature/z")
    assert res.returncode != 0
    assert "provisional" in res.stderr


def test_failed_review_leaves_no_droppings_in_reviews_dir(trust_repo):
    r = trust_repo
    r.write("src/pkg/mod.py", "def f():\n    return 1\n")
    r.add_all()
    assert r.commit("wip: work").returncode == 0
    r.set_reviewer_verdict("fail-run")  # headless reviewer crashes
    res = r.push("-u", "origin", "feature/x")
    assert res.returncode != 0
    assert "fail-closed" in res.stderr
    # stderr log lives in .git/, never in reviews/ (which must stay ledgers-only
    # so the documented `git add reviews/` finalize step cannot be poisoned).
    assert not list((r.root / "reviews").glob("*.err"))
    assert (r.root / ".git" / "agent-review.err").exists()


def test_instruction_files_are_never_docs_exempt(trust_repo):
    r = trust_repo
    r.write("CLAUDE.md", "# instructions\nDo things differently.\n")
    r.add_all()
    res = r.commit("docs: tweak instructions")
    assert res.returncode != 0  # .md, but behavior-shaping: lifecycle required
    assert "commit-msg BLOCKED" in res.stderr


# --- pre-push Gate 1 (matrix row 6) ---


def test_pre_push_blocks_red_suite_and_allows_green(trust_repo):
    r = trust_repo
    r.write("h.txt", "z\n")
    r.add_all()
    assert r.commit("wip: work").returncode == 0
    r.set_suite(False)
    res = r.push("-u", "origin", "feature/x")
    assert res.returncode != 0
    assert "offline suite not green" in res.stderr
    r.set_suite(True)
    res = r.push("-u", "origin", "feature/x")
    assert res.returncode == 0


def test_pre_push_blocks_on_collection_errors(trust_repo):
    """Regression for round-4 major: '33 passed, 1 error' matched the old summary
    grep — the exit code, not the summary line, is the truth."""
    r = trust_repo
    r.write("h.txt", "z\n")
    r.add_all()
    assert r.commit("wip: work").returncode == 0
    r.set_suite_raw("error")
    res = r.push("-u", "origin", "feature/x")
    assert res.returncode != 0
    assert "offline suite not green" in res.stderr


def test_committed_ledgers_are_append_only(trust_repo):
    """Regression for round-4 minor: audit-trail forgery must be blocked at
    commit (honest path) and at push (--no-verify path), with a human override."""
    r = trust_repo
    r.write("src/pkg/mod.py", "def f():\n    return 1\n")
    r.add_all()
    assert r.commit("need_agent_review: add f").returncode == 0
    (r.root / ".commit-approved").touch()
    r.git("add", "reviews")
    assert r.amend("feat: add f").returncode == 0
    assert r.push("-u", "origin", "feature/x").returncode == 0

    ledger = r.ledgers()[-1]
    ledger.write_text(ledger.read_text().replace("approve", "forged"))
    r.git("add", "reviews")
    res = r.commit("wip: tidy ledger")
    assert res.returncode != 0
    assert "append-only" in res.stderr  # honest path: blocked at commit

    assert r.git(
        "commit", "--no-verify", "-m", "wip: tidy ledger"
    ).returncode == 0  # hostile path: sneak past local hooks...
    res = r.push("origin", "feature/x")
    assert res.returncode != 0
    assert "append-only" in res.stderr  # ...still blocked at push
    res = r.push("origin", "feature/x", env={"ALLOW_LEDGER_EDIT": "1"})
    assert res.returncode == 0  # human override works


def test_branch_deletion_push_skips_gates(trust_repo):
    r = trust_repo
    r.write("i.txt", "w\n")
    r.add_all()
    assert r.commit("wip: work").returncode == 0
    assert r.push("-u", "origin", "feature/x").returncode == 0
    r.set_suite(False)  # gates would fail if they ran
    res = r.push("origin", ":feature/x")
    assert res.returncode == 0


def test_deleting_remote_main_is_blocked_at_push(trust_repo):
    """Matrix row 50's pre-push half: ref deletions skip the other gates, so
    main's deletion must be refused explicitly (round-16 minor: untested)."""
    r = trust_repo
    res = r.push("origin", ":main")
    assert res.returncode != 0
    assert "deleting remote main" in res.stderr


def test_wt7_embedded_mirror_matches_the_shipped_checker():
    """WT.7 embeds a byte-identical copy of `check_critical_paths.sh`. Nothing
    enforced that, and it drifted: the plan kept showing a failure message whose
    presumed cause the same document declared eliminated 70 lines below. A stale
    mirror is worse than no mirror — the next reader trusts it and either
    re-diagnoses the ruled-out cause or restores the listing over the real file.
    """
    repo = Path(__file__).resolve().parents[1]
    plan = (repo / "workflow-trust-plan.md").read_text().split("\n")
    script = (repo / "scripts" / "check_critical_paths.sh").read_text().rstrip("\n")

    table = (repo / "scripts" / "critical_paths.txt").read_text().rstrip("\n")

    # Both WT.7 listings, because declaring ONE of them authoritative makes the
    # other more likely to be trusted: the table block was stale (it omitted
    # run-qwen3vl/config/, so copying it back would have dropped a whole config
    # dir out of golden-verified enforcement).
    for marker, header_lines, actual, source in (
        ("# MIRROR of scripts/check_critical_paths.sh", 3, script,
         "scripts/check_critical_paths.sh"),
        ("# MIRROR of scripts/critical_paths.txt", 2, table,
         "scripts/critical_paths.txt"),
    ):
        starts = [i for i, l in enumerate(plan) if l.startswith(marker)]
        assert len(starts) == 1, f"{source}: expected 1 mirror, found {len(starts)}"
        body = starts[0] + header_lines
        end = next(i for i in range(body, len(plan)) if plan[i].strip() == "```")

        assert "\n".join(plan[body:end]) == actual, (
            f"workflow-trust-plan.md's WT.7 listing has drifted from {source} — "
            "re-copy the file into the block")
