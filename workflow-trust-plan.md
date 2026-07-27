# Workflow Trust Plan — deterministic gates, hooks, and due-diligence automation

Status: draft for review, 2026-07-23. Nothing in this plan is implemented yet.
Companion docs: `qa-and-traceability-plan.md` (the QA phase this extends), `CLAUDE.md`
(conventions this plan mechanizes), `plan.md` (task-card format this follows).

## Why this plan exists

The current verification story is strong but **runs on memory and discipline**: the
offline suite (164 tests, ~31 s measured 2026-07-23) runs when someone remembers,
COORDINATION.md *asks* agents to run `pytest -q` before ending a session, and
fresh-context code review happens only if the user thinks to request it. Research on
how practitioners mechanize trust (Boris Cherny / Anthropic, Armin Ronacher, Simon
Willison, Kent Beck, the pre-commit-LLM ecosystem — provenance in §9) converged on a
consistent finding, stated verbatim in Anthropic's own docs:

> "Unlike CLAUDE.md instructions which are advisory, **hooks are deterministic and
> guarantee the action happens**."

This plan converts every "should" in the current workflow into a gate that fires
mechanically, with a human-only override for each.

## Guiding principles

- **P1 — Mechanism over memory.** Any step that must *always* happen lives in a hook,
  a git hook, or CI — never in an instruction file. If a CLAUDE.md rule keeps getting
  ignored, convert it to a hook (Anthropic's own guidance).
- **P2 — Fast local, thorough remote.** Community consensus: pre-commit checks over
  ~5 s get bypassed (`--no-verify` temptation). So: pre-commit < 5 s (deterministic
  only), pre-push ≤ ~5 min (full suite + agent review), CI unbounded. Never put LLM
  review in pre-commit.
- **P3 — The agent must not grade its own work.** Review runs in a fresh context that
  did not write the code ("A fresh context improves code review since Claude won't be
  biased toward code it just wrote" — Anthropic best practices). Corollary: the agent
  cannot waive, skip, or self-approve any gate (see the guard rules in WT.3).
- **P4 — Evidence over assertion.** "Have Claude show evidence rather than asserting
  success: the test output, the command it ran and what it returned." Every PR carries
  a machine-checked Evidence section; every "Done when" is satisfied by pasted output.
- **P5 — Human review is bounded, not abolished.** A short, explicit critical-path
  list always gets human eyes (and a label to prove it); everything else is covered by
  gates. This converts "I can't read everything" into "I read these named things."
- **P6 — Every gate has a human override; no gate has an agent override.** Overrides
  are env vars / labels / sentinel files that only the user sets, and every use is
  recorded (waiver line in the review ledger).

## The enforcement stack at a glance

| Layer | Trigger | What runs | Blocks? | Latency | Task |
|---|---|---|---|---|---|
| L0 session | Claude Code `PreToolUse` | git/gh guard (no `--no-verify`, no force-push to main, no self-approval), protected-path guard (can't edit its own gates) | yes (exit 2) | ms | WT.3 |
| L0 session | Claude Code `Stop` | offline pytest, change-detected, blocks ending the turn red | yes (≤8 blocks) | 0–31 s | WT.3 |
| L1 commit | git `pre-commit` | branch guard, artifact guard, secret scan, test-deletion guard, py_compile | yes | < 5 s | WT.1 |
| L1 commit | git `commit-msg` | trailer hygiene + **forced declaration**: subject must be `need_agent_review*` (provisional), `wip:`/`checkpoint:` (free), or plain — and plain is only reachable with reviewer approval + the human's `.commit-approved` sentinel (D7) | yes | ms | WT.1, WT.4 |
| L1 commit | git `post-commit` | subject starts with `need_agent_review` → **fresh-context reviewer fires immediately**; findings surface loudly in the committer's session; fixes squash in via `--amend` (re-triggers review); after human approval the finalize-amend replaces the subject | no (trigger only — gates are commit-msg + push) | 1–5 min, provisional commits only | WT.4 |
| L2 push | git `pre-push` | full offline suite + agent re-review (**backstop** — skipped when a post-commit review already approved exactly this content) | yes | 0.5–6 min | WT.2, WT.4 |
| L3 CI | GitHub Actions on PR/main | offline suite on a clean runner; Evidence-section check; critical-path label check | yes (required checks) | 2–5 min | WT.5–WT.7 |
| L4 human | PR review | critical-path files read by the user; `human-reviewed` / `golden-verified` labels | yes (CI enforces label) | bounded | WT.7 |

Defense in depth is deliberate: the same invariant (e.g. "tests pass before merge")
exists at L0 (Stop hook), L2 (pre-push), and L3 (CI). L0/L1/L2 are bypassable by the
*user* (that's a feature); L3 is bypassable by no one.

---

## Task cards

Format follows `plan.md`: `ID · Goal · Deliverable · Done when · Depends on`.
File contents given here are the intended implementation, verbatim.

### WT.0 — Hook plumbing (checked-in git hooks)

**Goal:** git hooks live in the repo, survive clones, one command to activate.
**Deliverable:** `.githooks/` directory + `scripts/setup-hooks.sh` + a setup line in
CLAUDE.md's Commands section.
**Done when:** on a fresh clone, `bash scripts/setup-hooks.sh` prints the active
hooksPath and a deliberately bad commit (see §8 validation matrix) is rejected.
**Depends on:** —

`scripts/setup-hooks.sh`:

```bash
#!/usr/bin/env bash
# Activate the repo's checked-in git hooks and Claude Code hooks.
# One manual step per clone/machine — everything else is mechanical after this.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
git config core.hooksPath .githooks
chmod +x .githooks/* scripts/*.sh 2>/dev/null || true
chmod +x .claude/hooks/* 2>/dev/null || true
echo "hooksPath: $(git config core.hooksPath)"
echo "Claude Code hooks are read from .claude/settings.json automatically."
echo "NOTE: restart any running Claude Code session to pick up hook changes"
echo "      (hooks are snapshotted at session start)."
```

Notes:
- `core.hooksPath` is per-clone config, so the setup script is the one manual step per
  machine. Add to CLAUDE.md setup block so every session (and every future clone of
  the appliance) runs it.
- All hook scripts parse stdin with `python3` — no `jq` dependency.

### WT.1 — Commit-time gates (fast, deterministic, < 5 s)

**Goal:** no commit can introduce secrets, workdir artifacts, silently deleted tests,
syntax errors, or land directly on main; every commit carries the correct trailer.
**Deliverable:** `.githooks/pre-commit`, `.githooks/commit-msg`.
**Done when:** each row of the §8 validation matrix marked L1 is demonstrated blocked,
and a normal commit completes in < 5 s (timed).
**Depends on:** WT.0

`.githooks/pre-commit`:

```bash
#!/usr/bin/env bash
# Fast deterministic gates only (<5s). LLM review happens post-commit/pre-push,
# never here (workflow-trust-plan.md WT.1; latency here breeds --no-verify habits).
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
fail() { echo "pre-commit BLOCKED: $1" >&2; exit 1; }

# 1. Branch guard: main merges via PR only. Override: ALLOW_MAIN_COMMIT=1 (human).
branch=$(git symbolic-ref --short -q HEAD || echo detached)
if [ "$branch" = "main" ] && [ -z "${ALLOW_MAIN_COMMIT:-}" ]; then
  fail "direct commit to main. Create a branch (git checkout -b), or ALLOW_MAIN_COMMIT=1 for a deliberate exception."
fi

# 2. Artifact guard: workdirs, vectors, DBs, model weights never enter git.
bad=$(git diff --cached --name-only | grep -E '^\.va|/catalog\.db$|\.npz$|\.pt$|\.onnx$|/media\.mp4$|/weights/' || true)
[ -n "$bad" ] && fail "workdir/model artifacts staged:
$bad"

# 2b. reviews/ is ledgers-only: the approval hash excludes reviews/*.md, so any
#     other file type there could ride an approval unreviewed. Refuse at entry.
badrev=$(git diff --cached --name-only | grep '^reviews/' | grep -vE '\.md$|\.gitkeep$' || true)
[ -n "$badrev" ] && fail "reviews/ may contain only .md ledgers:
$badrev"

# 2c. Ledgers are append-only (audit trail): modifying or deleting a committed
#     one is history forgery. Human-only override: ALLOW_LEDGER_EDIT=1.
modrev=$(git diff --cached --name-only --diff-filter=MDR -- 'reviews/' || true)
if [ -n "$modrev" ] && [ -z "${ALLOW_LEDGER_EDIT:-}" ]; then
  fail "reviews/ ledgers are append-only:
$modrev
If genuinely needed (human decision): ALLOW_LEDGER_EDIT=1."
fi

# 3. Secret scan on staged additions.
leaks=$(git diff --cached -U0 | grep -E '^\+' | grep -nE \
  'hf_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|gh[posu]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY' || true)
[ -n "$leaks" ] && fail "possible secret in staged diff:
$leaks"

# 4. Test-deletion guard: blocks net test removal (renames pass).
#    Override: ALLOW_TEST_REMOVAL=1 (human), and say why in the commit body.
removed=$(git diff --cached -U0 -- 'tests/' | grep -cE '^\-\s*def test_' || true)
added=$(git diff --cached -U0 -- 'tests/' | grep -cE '^\+\s*def test_' || true)
if [ "${removed:-0}" -gt "${added:-0}" ] && [ -z "${ALLOW_TEST_REMOVAL:-}" ]; then
  fail "net test deletion: -$removed/+$added test functions. If intentional, rerun with ALLOW_TEST_REMOVAL=1 and say why in the commit body."
fi
# Warn (not block) on new skip/xfail markers — legitimate here (strict-xfail
# convention) but always worth a human glance in the diff.
newskips=$(git diff --cached -U0 -- 'tests/' | grep -E '^\+.*(pytest\.mark\.skip|xfail)' || true)
[ -n "$newskips" ] && echo "pre-commit WARNING: new skip/xfail markers:
$newskips" >&2

# 5. Syntax gate: staged .py files must at least compile.
pyfiles=$(git diff --cached --name-only --diff-filter=ACM | grep '\.py$' || true)
if [ -n "$pyfiles" ]; then
  echo "$pyfiles" | xargs .venv/bin/python -m py_compile || fail "staged Python does not compile"
fi
exit 0
```

`.githooks/commit-msg` (mechanizes the attribution preference AND the review
lifecycle's forced declaration — every commit must say what it is, and a plain
"final" subject is only reachable through reviewer approval + human sign-off, D7).
Also add `.commit-approved` and `.guard-override` to `.gitignore` (WT.1 deliverable):

```bash
#!/usr/bin/env bash
# Trailer hygiene + the review lifecycle's forced declaration
# (workflow-trust-plan.md WT.1/WT.4): every commit subject is provisional-for-review,
# a checkpoint, or a finalization of reviewer-approved + human-approved content.
set -euo pipefail
msgfile="$1"
cd "$(git rev-parse --show-toplevel)"

# --- Trailer hygiene (unconditional) ---
sed -i '/^Co-Authored-By: Claude/d' "$msgfile"
if ! grep -q '^Signed-off-by: Deepak Gupta (deepak0414) using Claude assistance$' "$msgfile"; then
  printf '\nSigned-off-by: Deepak Gupta (deepak0414) using Claude assistance\n' >> "$msgfile"
fi

# --- Forced declaration ---
# Exemptions: merges, autosquash fixups.
git rev-parse -q --verify MERGE_HEAD >/dev/null && exit 0
subject=$(head -1 "$msgfile")
case "$subject" in
  fixup!*|squash!*)   exit 0 ;;
  need_agent_review*) exit 0 ;;   # provisional: post-commit fires the reviewer
  wip:*|checkpoint:*) exit 0 ;;   # declared not-done: free
esac
# Docs-only work never gets a review (post-commit and pre-push both skip it), so a
# plain subject is fine when the committed branch AND this commit's staged content
# are nothing but docs (committed scope — matching the approval-hash philosophy;
# ledgers are .md and count as docs). Fails CLOSED if origin/main is missing.
if git rev-parse -q --verify origin/main >/dev/null; then
  files=$( { git diff --name-only origin/main HEAD 2>/dev/null; \
             git diff --cached --name-only; } )
  # Instruction-bearing files (CLAUDE.md, .claude/, hooks, workflows, scripts)
  # shape agent behavior and are never inert docs.
  if ! echo "$files" | grep -qvE '\.(md|txt)$' && \
     ! echo "$files" | grep -qE '^(\.claude/|\.githooks/|\.github/|scripts/)|^CLAUDE\.md$'; then
    exit 0
  fi
fi
# Plain subject = finalization. Allowed iff the reviewer approved EXACTLY this
# committed content, the human sentinel exists (D7), and the amend adds nothing
# beyond reviews/ ledgers (staged content sneaked into a finalize would otherwise
# dodge the approval until the push backstop). Consume the sentinel on success.
if [ -f .git/.review-approved ] && \
   [ "$(cat .git/.review-approved)" = "$(scripts/review_scope_hash.sh)" ] && \
   [ -z "$(git diff --cached --name-only -- . ':(exclude)reviews/*.md')" ] && \
   [ -f .commit-approved ]; then
  rm -f .commit-approved
  exit 0
fi
echo "commit-msg BLOCKED: subject must start with 'need_agent_review' (work complete
-> review fires) or 'wip:'/'checkpoint:' (not done yet). A plain subject is only for
finalizing a reviewed commit: requires reviewer verdict=approve for this exact
committed content, a finalize amend that adds nothing beyond reviews/, AND the human
having run 'touch .commit-approved' (human-only; WT.3 session guards enforce that
mechanically once they land)." >&2
exit 1
```

### WT.2 — Push-time gate: full suite

**Goal:** nothing reaches GitHub with a red offline suite; every push of substance
gets the agent review (WT.4).
**Deliverable:** `.githooks/pre-push`.
**Done when:** a push with one deliberately failing test is rejected with the pytest
tail shown; a docs-only push skips review and completes in < 45 s.
**Depends on:** WT.0, WT.4 (review script; the pytest part can land first)

`.githooks/pre-push`:

```bash
#!/usr/bin/env bash
# Push gate (workflow-trust-plan.md WT.2 + WT.4 backstop).
# stdin: "<local ref> <local sha> <remote ref> <remote sha>" per pushed ref.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
zero=0000000000000000000000000000000000000000
fail() { echo "pre-push BLOCKED: $1" >&2; exit 1; }

while read -r local_ref local_sha remote_ref remote_sha; do
  [ "$local_sha" = "$zero" ] && continue   # branch deletion — nothing to verify

  # Gate 1: full offline suite (stub backends, no GPU/network; ~31 s).
  # The EXIT CODE is the truth — summary-line grepping matched "33 passed,
  # 1 error" and failed open on collection errors.
  # NB: every command in this loop gets </dev/null — the loop is reading the
  # pushed-refs list from stdin, and a child that reads stdin (headless claude
  # does) would swallow the remaining refs and fail OPEN for them.
  if ! out=$(.venv/bin/pytest -q </dev/null 2>&1); then
    fail "offline suite not green:
$(echo "$out" | tail -8)"
  fi

  # Gate 2: agent review BACKSTOP (primary review runs at post-commit on
  # need_agent_review-subject commits). Re-reviews only content without an
  # approval — catches skipped lifecycles, manual edits, crashed sessions.
  git rev-parse -q --verify origin/main >/dev/null || fail "origin/main not found — cannot scope review gates; fetch first."
  if [ "$remote_sha" = "$zero" ]; then
    range="origin/main...$local_sha"
  elif git cat-file -e "$remote_sha" 2>/dev/null; then
    range="$remote_sha..$local_sha"
  else
    # Remote tip unknown locally (stale clone): fail CLOSED by widening the
    # review scope to the whole branch instead of silently skipping gates.
    range="origin/main...$local_sha"
  fi
  # Docs-only pushes skip review — but instruction-bearing files (CLAUDE.md,
  # .claude/, hooks, workflows, scripts) shape agent behavior and are NEVER inert
  # docs, so they always take the review path.
  files=$(git diff --name-only "$range")
  if echo "$files" | grep -qvE '\.(md|txt)$' || \
     echo "$files" | grep -qE '^(\.claude/|\.githooks/|\.github/|scripts/)|^CLAUDE\.md$'; then
    # Hash the PUSHED ref, not HEAD — per-ref pushes must compare (and bless)
    # exactly the content that ships.
    cur=$(scripts/review_scope_hash.sh "$local_sha")
    if [ -f .git/.review-approved ] && [ "$(cat .git/.review-approved)" = "$cur" ]; then
      echo "pre-push: content already approved by post-commit review — skipping re-review." >&2
    else
      scripts/agent-review.sh "$range" </dev/null || fail "agent review requested changes — see reviews/ ledger. Fix findings or (human-only) waive with AGENT_REVIEW=skip."
      echo "$cur" > .git/.review-approved
    fi
  fi

  # Gate 2b: the audit trail is append-only. The approval hash excludes ledgers,
  # so a modified/deleted shipped ledger (forged review history) would otherwise
  # ride an existing approval. Human-only override: ALLOW_LEDGER_EDIT=1.
  forged=$(git diff --name-status "$range" -- 'reviews/' | grep -E '^[MDR]' || true)
  if [ -n "$forged" ] && [ -z "${ALLOW_LEDGER_EDIT:-}" ]; then
    fail "reviews/ ledgers are append-only (audit trail):
$forged
If genuinely needed (human decision): ALLOW_LEDGER_EDIT=1 git push."
  fi

  # Gate 3: provisional commits never ship. A need_agent_review subject means the
  # lifecycle (review -> human approval -> finalize amend) was not completed.
  if git log --format=%s "$range" 2>/dev/null | grep -q '^need_agent_review'; then
    fail "push range contains provisional 'need_agent_review' commits — finalize them (reviewer approve + touch .commit-approved + git commit --amend with the real subject) before pushing."
  fi
done
exit 0
```

### WT.3 — Session guardrails (Claude Code hooks, committed)

**Goal:** inside any Claude Code session in this repo, the agent mechanically cannot
(a) bypass git hooks, (b) force-push main, (c) approve/merge/label its own PRs,
(d) edit the gate machinery itself, (e) end a turn with the offline suite red.
(The fresh-context *review* trigger is NOT here — it lives at git post-commit, WT.4,
because a commit tagged `need_agent_review` is the workflow's explicit "work is fully
done" declaration, whereas turn-ends also happen mid-feature and on conversational
turns.)
**Deliverable:** `.claude/settings.json` (project-scoped, committed — note the
existing `.claude/settings.local.json` stays personal/gitignored), plus
`.claude/hooks/bash_guard.py`, `.claude/hooks/path_guard.py`,
`.claude/hooks/stop_gate.sh`.
**Done when:** each L0 row in the §8 matrix is demonstrated from a live session
(guard message visible), and a clean session ends without a spurious Stop block.
**Depends on:** WT.0

`.claude/settings.json`:

```json
{
  "permissions": {
    "deny": [
      "Bash(git commit --no-verify*)",
      "Bash(git push --force*)",
      "Bash(git config core.hooksPath*)"
    ]
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/bash_guard.py\"",
            "timeout": 10 }
        ]
      },
      {
        "matcher": "Edit|Write",
        "hooks": [
          { "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/path_guard.py\"",
            "timeout": 10 }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "*",
        "hooks": [
          { "type": "command",
            "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/stop_gate.sh\"",
            "timeout": 300,
            "statusMessage": "Turn-end gate: offline suite..." }
        ]
      }
    ]
  }
}
```

Design notes (from the research):
- The `permissions.deny` strings are **advisory-grade only** — a confirmed Claude Code
  issue (#40117) shows string deny-rules are evadable via flag reordering. That is why
  the same invariants are re-checked by `bash_guard.py` with regexes, which is the
  canonical mitigation (the `block-no-verify` pattern), and again by L1/L3.
- Hook scripts read a JSON payload on **stdin**; exit code **2** blocks the action and
  feeds stderr back to Claude as the reason. Exit 0 allows.

`.claude/hooks/bash_guard.py`:

```python
#!/usr/bin/env python3
"""PreToolUse guard for Bash. Blocks gate-bypass and self-approval commands.
Exit 2 = block (stderr shown to Claude). Exit 0 = allow."""
import json, re, sys

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)  # malformed payload: never brick the session
cmd = (payload.get("tool_input") or {}).get("command", "") or ""

RULES = [
    (r"\bgit\b[^|;&\n]*--no-verify",            "--no-verify bypasses this repo's verification hooks (P6: gates have no agent override)."),
    (r"\bgit\s+commit\b[^|;&\n]*\s-n\b",        "git commit -n is --no-verify; hooks must run."),
    (r"\bgit\s+push\b[^|;&\n]*(--force\b|\s-f\b)(?![-\w])[^|;&\n]*\b(main|master)\b",
                                                 "force-push to main is blocked."),
    (r"\bcore\.hooksPath\b",                     "changing hooksPath disables the trust gates; ask the user."),
    (r"\bAGENT_REVIEW=skip\b",                   "the review waiver is human-only (P6)."),
    (r"\.commit-approved\b",                     "the finalization sentinel is human-only (D7): only the user may create or remove it."),
    (r"\.guard-override\b",                      "the guard override is human-only (P6)."),
    (r"\bALLOW_TEST_REMOVAL=1\b",                "test-removal override is human-only; explain the need and ask."),
    (r"\bALLOW_LEDGER_EDIT=1\b",                 "ledger-edit override is human-only (audit trail)."),
    (r"\bALLOW_MAIN_COMMIT=1\b",                 "main-commit override is human-only."),
    (r"\bgh\s+pr\s+merge\b",                     "merging PRs is a human action in this repo."),
    (r"\bgh\s+pr\s+(edit|review)\b[^|;&\n]*(human-reviewed|golden-verified|--approve)",
                                                 "review labels/approvals are human-only (P3)."),
    (r"\brm\s+-[a-z]*r[a-z]*f?\s+(/|~|\.git\b)", "destructive delete of repo/system paths."),
]
for pattern, reason in RULES:
    if re.search(pattern, cmd):
        print(f"BLOCKED: {reason}\nCommand was: {cmd}", file=sys.stderr)
        sys.exit(2)
sys.exit(0)
```

`.claude/hooks/path_guard.py` (the agent cannot edit its own cage; the user can, or
can grant a temporary opening):

```python
#!/usr/bin/env python3
"""PreToolUse guard for Edit/Write: protect the gate machinery from the agent.
Human override: `touch .guard-override` (gitignored) grants edits for that session."""
import json, os, sys

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)
root = os.environ.get("CLAUDE_PROJECT_DIR", ".")
if os.path.exists(os.path.join(root, ".guard-override")):
    sys.exit(0)
path = (payload.get("tool_input") or {}).get("file_path", "") or ""
rel = os.path.relpath(path, root) if os.path.isabs(path) else path

PROTECTED = (".githooks/", ".claude/settings.json", ".claude/hooks/",
             ".github/workflows/", "scripts/agent-review.sh",
             "scripts/critical_paths.txt",
             ".commit-approved", ".guard-override")  # human-only sentinels (D7/P6)
if any(rel == p or rel.startswith(p) for p in PROTECTED):
    print(f"BLOCKED: {rel} is trust-gate machinery (P6). Propose the change to the "
          f"user; they can apply it or `touch .guard-override` to open this session.",
          file=sys.stderr)
    sys.exit(2)
sys.exit(0)
```

`.claude/hooks/stop_gate.sh` (turn-level test gate; the documented backstop is that
Claude Code overrides the hook after 8 consecutive blocks, so this cannot loop
forever):

```bash
#!/usr/bin/env bash
# Stop hook: block ending the turn while the offline suite is red.
# Change-detected: skips entirely when src/tests are untouched since last green run.
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
[ -x .venv/bin/pytest ] || exit 0
# Recursion guard: inside the headless reviewer session (WT.4), all repo hooks no-op
# so a review never triggers tests/reviews of its own.
[ -n "${VA_AGENT_REVIEW:-}" ] && exit 0

state=".git/.stop-gate-green"
cur=$( (git diff HEAD -- src tests pyproject.toml; \
        git status --porcelain -- src tests) 2>/dev/null | sha256sum | cut -d' ' -f1)
[ -f "$state" ] && [ "$(cat "$state")" = "$cur" ] && exit 0

out=$(.venv/bin/pytest -q 2>&1 | tail -8)
if echo "$out" | grep -qE '^[0-9]+ passed' && ! echo "$out" | grep -qE 'failed|error'; then
  echo "$cur" > "$state"
  exit 0
fi
echo "Cannot end turn: offline suite is red. Fix before stopping (P1).
$out" >&2
exit 2
```

### WT.4 — Fresh-context agent review at post-commit (primary) + pre-push (backstop)

**Goal:** the two-agent review lifecycle (decided 2026-07-26). Roles: the
**committer** (the main session agent — writes the changes AND commits them) and the
**reviewer** (a fresh headless instance spawned mechanically per review, read-only,
holds no state between runs); the **human** sits between verdict and finalization.
The full lifecycle:

1. Task complete → committer commits with subject **`need_agent_review: <desc>`**
   (subject line, enforced by commit-msg's forced declaration — a commit that is
   neither provisional, checkpoint, nor approved-finalization cannot exist).
2. `post-commit` fires the reviewer on the whole branch; verdict + findings go to
   the `reviews/` ledger and echo into the committer's session.
3. `request_changes` → committer fixes → `git commit --amend` (tag kept) →
   review re-fires. Loop until approve.
4. On approve → committer presents the human a digest: verdict, findings, ledger
   path, what changed since last approval.
5. Human approves by running **`touch .commit-approved`** (human-only — guards block
   agents from creating it; D7).
6. Committer runs `git add reviews/` (the ledger ships inside the commit it reviewed
   — excluded from the approval hash, so this never invalidates it) then the final
   `git commit --amend` with the real descriptive subject; commit-msg permits the
   plain subject (approved hash + sentinel), consumes the sentinel; post-commit sees
   no tag and stays quiet. History: ONE clean commit, reviewed and human-approved,
   no tag residue.
7. `pre-push`: approval hash matches → tests only; and no `need_agent_review`
   subject may ship (Gate 3) — provisional commits cannot reach GitHub.

Checkpoint/slot-machine commits use `wip:`/`checkpoint:` subjects and cost nothing.
Pre-push reviews anything without an approval (forgotten tag, manual edits, crashed
sessions), so skipping the lifecycle *delays* review — it can never avoid it. This is
the writer/reviewer split (P3) made un-forgettable, with clean history as a side
effect: review iterations disappear into the amended commit.
**Deliverable:** `.githooks/post-commit`, `scripts/agent-review.sh` (range +
`--worktree` modes), `scripts/review_scope_hash.sh`, `.claude/agents/code-reviewer.md`,
`reviews/` ledger directory (committed), pre-push wiring (WT.2), a CLAUDE.md
convention block describing steps 1–7 verbatim (the only advisory parts are which
marker to pick and the digest quality — every transition is hook-enforced).
**Done when:** (a) a commit tagged `need_agent_review` containing a planted bug in a
non-tested path fires the review, and the failure + file:line finding are visible in
the committing session's tool result; (b) after fixing and `git commit --amend` (tag
kept), the re-review approves and the approval hash is written; (c) an untagged
commit triggers no review and returns instantly; (d) pushing approved content skips
the re-review (log line visible) while pushing a branch with only untagged commits
triggers the backstop review; (e) every review run produces
`reviews/<date>-<branch>-<sha>.md`; (f) an `AGENT_REVIEW=skip` push from the terminal
succeeds and writes a WAIVED ledger entry, while the same attempt inside a Claude
session is blocked by `bash_guard.py`; (g) no recursive review: the reviewer's own
session triggers no hooks (guard verified by inspection of a review run);
(h) finalizing to a plain subject is blocked without the human sentinel and succeeds
with it, consuming `.commit-approved`; (i) a push containing a `need_agent_review`
subject is rejected by pre-push Gate 3.
**Depends on:** WT.0; wired by WT.2 (pre-push backstop)

Design decisions (research + user decisions 2026-07-24/25):
- **Trigger: post-commit, opt-in via `need_agent_review` in the commit message.**
  Rationale trail: turn-end (Stop) fires on conversational and half-done turns; PR
  creation is later than "task done"; a *tagged commit* is both semantic (the agent
  declares completion) and mechanical (a hookable git event). Post-commit — not
  pre-commit — because the checkpoint must exist before review so fixes can be
  squashed into it (`--amend` re-fires the hook, keeping the loop closed), and
  because LLM-review-in-pre-commit is the documented latency anti-pattern.
- **Post-commit is a trigger, not a gate.** git ignores a post-commit hook's exit
  status by design; the loud stderr in the Bash tool result is the writer's feedback,
  and enforcement lives in the approval hash checked at pre-push (and PR gates).
  Trigger and gate are decoupled; both are mechanical.
- **Review scope, hash scope, and ship scope are the same thing: the committed
  branch (`origin/main..HEAD`, excluding `reviews/`).** Whole-branch (not
  last-commit) so an approval can never hash-bless earlier unreviewed commits;
  committed-only so uncommitted/untracked edits never enter the hash — they can't
  be blessed, and they don't ship; committing them changes the hash and triggers
  the backstop. (The first real review of PR 2 caught the original worktree-scoped
  hash blessing dirty edits — matrix row 29 is its regression test.) The finalize
  amend may stage nothing beyond `reviews/` ledgers (commit-msg enforces).
- **Recursion guard:** `agent-review.sh` exports `VA_AGENT_REVIEW=1`; every hook
  (post-commit, stop_gate) exits 0 when it is set, so the reviewer's own headless
  session — which runs in this same project directory with these same hooks — can
  never trigger tests or a review of itself.
- **Pre-push stays as the fail-closed backstop, near-free when redundant.** Headless
  review runs on the existing subscription login (same mechanism as the
  `claude-code` reasoner backend), so extra runs cost time, not money. Watch-item
  from rollout: if untagged commits start reaching push regularly, tag discipline is
  slipping — the ledger makes it visible; the fallback is inverting the default
  (review every commit, tag to *skip*), which is stricter, never weaker.
- **As-built deviations (2026-07-27, PR 2):** verdict parsing passes the raw output
  via an env var into the python heredoc (the drafted `<<'PY' <<<"$raw"` combined
  two stdin redirects — invalid); `timeout 480` instead of 600 so a full review fits
  inside tool/CI execution caps with margin; ledger filenames carry seconds
  (`%Y%m%d-%H%M%S`) so the fix-amend-re-review loop never overwrites an entry.
  **From the first real review's findings (all four fixed before finalize):**
  approval hash re-scoped from worktree to committed branch (major — see the design
  bullet above); missing `origin/main` or an unknown remote sha now fails closed
  (block / widen to whole-branch scope) instead of silently skipping gates; hook
  messages no longer claim guard-blocking that only lands with WT.3; the interactive
  `code-reviewer` subagent pins `tools: Read, Grep, Glob, Bash` in frontmatter
  instead of inheriting all tools.
  **From the second review round (again all four fixed):** the hash exclusion
  narrowed from all of `reviews/` to `reviews/*.md` + a pre-commit ledgers-only
  gate, closing the smuggle-code-via-reviews/ tunnel (matrix row 31); Gate 2 hashes
  the pushed sha instead of HEAD so per-ref pushes compare exactly what ships (row
  32); commit-msg's docs-only exemption fails closed without `origin/main` and uses
  committed+staged scope; the plan's verbatim code blocks are now script-synced
  with the as-built files.
  **From the third round:** pre-push loop children run `</dev/null` — a live
  reviewer reading the hook's stdin was swallowing the remaining pushed-ref lines
  and failing OPEN for them (row 33; the fake reviewer now reads stdin like the
  real one so the sandbox can catch this class); reviewer stderr moved to
  `.git/agent-review.err` so a crashed review can't poison `reviews/` (row 34);
  instruction-bearing files (CLAUDE.md, `.claude/`, `.githooks/`, `.github/`,
  `scripts/`) are excluded from every docs-only exemption (row 35).
  **From the fourth round:** Gate 1 judges the suite by pytest's exit code — the
  summary-line grep matched "33 passed, 1 error" and failed open on collection
  errors (row 36); shipped ledgers are append-only, enforced at pre-commit
  (diff-filter MDR) and again at push (so `--no-verify` forgeries still die),
  with `ALLOW_LEDGER_EDIT=1` as the recorded human-only override (row 37).

`.githooks/post-commit`:

```bash
#!/usr/bin/env bash
# Trigger (not gate): commits whose SUBJECT starts with `need_agent_review` fire the
# fresh-context review immediately (workflow-trust-plan.md WT.4). git ignores this
# hook's exit code by design; enforcement lives at commit-msg (finalization) and
# pre-push (Gates 2-3). The nonzero exit + stderr surface loudly in the committing
# session's tool result — that is the feedback channel.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
[ -n "${VA_AGENT_REVIEW:-}" ] && exit 0        # recursion guard (reviewer session)

subject=$(git log -1 --pretty=%s)
case "$subject" in
  need_agent_review*) ;;    # provisional commit: fall through to review
  *) exit 0 ;;              # checkpoints (wip:) and finalized commits: free
esac
git rev-parse -q --verify HEAD^2 >/dev/null && exit 0   # merge commits: skip
# Fail closed if the baseline is missing (an empty diff must never mean "skip").
git rev-parse -q --verify origin/main >/dev/null || {
  echo "post-commit: origin/main not found — cannot scope the review; fetch first." >&2
  exit 1
}
# Docs-only branches: skip (exempt from the lifecycle end-to-end) — except
# instruction-bearing files (CLAUDE.md, .claude/, hooks, workflows, scripts),
# which shape agent behavior and always get reviewed.
files=$(git diff --name-only origin/main..HEAD)
if ! echo "$files" | grep -qvE '\.(md|txt)$' && \
   ! echo "$files" | grep -qE '^(\.claude/|\.githooks/|\.github/|scripts/)|^CLAUDE\.md$'; then
  exit 0
fi

if scripts/agent-review.sh origin/main..HEAD; then
  scripts/review_scope_hash.sh > .git/.review-approved
  echo "post-commit: review APPROVED — present the human a digest (verdict, findings,
ledger path); after they run 'touch .commit-approved', finalize with:
    git add reviews/ && git commit --amend   (real subject; sentinel is consumed)" >&2
else
  echo "post-commit: review REQUESTED CHANGES — read the newest reviews/ ledger
entry, fix the critical/major findings, and squash them in with:
    git commit --amend        (keep the need_agent_review subject; amend re-runs review)
Push stays blocked until a review approves or the user waives (AGENT_REVIEW=skip)." >&2
  exit 1
fi
```
- **Fresh context by construction:** a new `claude -p` process knows nothing of the
  writing session. CLAUDE.md still loads (wanted: the reviewer should know repo
  conventions like "determinism ≠ correctness"); independence comes from not having
  written the diff, not from amnesia.
- **Scope: correctness, not style.** Anthropic's warning: "reviewers always find
  something" — an unscoped reviewer manufactures work. The prompt below restricts to
  bugs, contract breaks, silent-heuristic violations, and plan/DoD gaps.
- **Read-only tools.** The reviewer can inspect, never modify.

`scripts/review_scope_hash.sh` (the one canonical definition of "what the reviewer
approved" — used by Stop Gate B to cache approvals and by pre-push to skip redundant
re-review):

```bash
#!/usr/bin/env bash
# One canonical definition of "the content the reviewer approved": the COMMITTED
# branch scope, origin/main..<commit> (default HEAD; pre-push passes the pushed
# sha so per-ref pushes hash exactly what ships). Uncommitted/untracked edits
# never enter the hash, so they can never be blessed by an approval — committing
# them changes this hash and triggers the pre-push backstop.
# Only reviews/*.md ledgers are excluded (they are artifacts of the review itself,
# added during the finalize amend); any NON-ledger file under reviews/ stays in
# the hash, so it cannot ride an approval unreviewed (pre-commit also rejects it).
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
commit="${1:-HEAD}"
git diff origin/main "$commit" -- . ':(exclude)reviews/*.md' 2>/dev/null \
  | sha256sum | cut -d' ' -f1
```

`scripts/agent-review.sh`:

```bash
#!/usr/bin/env bash
# Fresh-context adversarial review via headless Claude (workflow-trust-plan.md WT.4).
# Usage: scripts/agent-review.sh <git-range>     (pre-push backstop mode)
#        scripts/agent-review.sh --worktree      (everything vs origin/main)
# Exit 0 = approved (or human-waived); 1 = changes requested / review failed
# (fail-closed). Findings + full review text land in a reviews/ ledger entry.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
mode="${1:?usage: agent-review.sh <git-range>|--worktree}"
if [ "$mode" = "--worktree" ]; then
  range="worktree"
  scope="the working tree: every change not yet on origin/main — committed-but-
unpushed, uncommitted, and untracked files.
Start with: git status --porcelain   and   git diff origin/main
Untracked files do not appear in the diff — Read them directly."
else
  range="$mode"
  scope="the commit range: ${range}
Start with: git diff ${range}   and   git log --oneline ${range}"
fi
branch=$(git symbolic-ref --short -q HEAD || echo detached)
sha=$(git rev-parse --short HEAD)
ledger="reviews/$(date +%Y%m%d-%H%M%S)-${branch//\//-}-${sha}.md"
mkdir -p reviews

# Human-only waiver by convention now, by mechanism once WT.3's session guards
# land; every use is recorded so waived pushes stay visible in the audit trail.
if [ "${AGENT_REVIEW:-}" = "skip" ]; then
  printf '# Review WAIVED by user\n\ndate: %s\nrange: %s\nbranch: %s\n' \
    "$(date -Is)" "$range" "$branch" > "$ledger"
  echo "agent-review: WAIVED (ledger: $ledger)" >&2
  exit 0
fi

if [ "$range" = "worktree" ]; then
  stat=$(git diff --stat origin/main 2>/dev/null | tail -1)
else
  stat=$(git diff --stat "$range" | tail -1)
fi

prompt="You are a fresh-context adversarial code reviewer for this repo. You did NOT
write this code; your job is to find what is wrong with it, not to praise it.

Review ONLY ${scope}
Read any file you need for context (read-only).
If a reviews/ ledger entry disputes one of your earlier findings with a reasoned
explanation, re-judge that finding on the merits rather than repeating it.

Report ONLY (scope discipline — no style/naming/preference comments):
1. Correctness bugs: logic errors, inverted conditions, off-by-one, broken error paths.
2. Contract breaks: changes to function signatures/behavior listed in COORDINATION.md,
   schema changes without migration handling, vector-space/config mismatches.
3. Repo-rule violations from CLAUDE.md: silently introduced hardcoded content or
   canned heuristics; determinism claimed as correctness without ground-truth
   validation; best-effort roles now able to abort ingest.
4. Test integrity: tests deleted/weakened/gamed; new code paths with zero coverage
   that the plan's 'Done when' implies should be tested.
5. If a plan doc in the repo covers this change, gaps between the diff and its
   'Done when' items.

For each finding: severity (critical|major|minor), file:line, one-sentence issue,
and the concrete failure scenario. If you verify a suspicion by reading more code and
it dissolves, do not report it.

End your reply with EXACTLY one fenced json block:
\`\`\`json
{\"verdict\": \"approve|request_changes\", \"findings\": [{\"severity\": \"...\",
\"file\": \"...\", \"line\": 0, \"issue\": \"...\", \"scenario\": \"...\"}]}
\`\`\`
Verdict rule: request_changes iff any critical or major finding."

echo "agent-review: reviewing $range ($stat) ..." >&2
# Recursion guard: inherited by the headless reviewer session so this repo's hooks
# (post-commit, stop_gate) no-op inside it — a review can never trigger a review.
export VA_AGENT_REVIEW=1
# stderr goes to .git/ (NOT reviews/ — a stray non-.md file there would trip the
# pre-commit ledgers-only gate at the next `git add reviews/`).
errlog=".git/agent-review.err"
raw=$(timeout 480 claude -p "$prompt" \
  --allowedTools "Read,Grep,Glob,Bash(git diff *),Bash(git log *),Bash(git show *),Bash(git blame *),Bash(git status *)" \
  --output-format json --max-turns 40 2>"$errlog") || {
    echo "agent-review: headless run failed/timed out — treating as BLOCK (fail-closed). See $errlog" >&2
    exit 1
  }

RAW="$raw" LEDGER="$ledger" RANGE="$range" BRANCH="$branch" python3 - <<'PY'
import datetime
import json
import os
import re
import sys

raw = os.environ["RAW"]
ledger, rng, branch = os.environ["LEDGER"], os.environ["RANGE"], os.environ["BRANCH"]
try:
    result = json.loads(raw).get("result", raw)
except Exception:
    result = raw
blocks = re.findall(r"```json\s*(\{.*?\})\s*```", str(result), re.S)
verdict, findings = "request_changes", []  # unparseable verdict stays fail-closed
if blocks:
    try:
        v = json.loads(blocks[-1])
        verdict = v.get("verdict", "request_changes")
        findings = v.get("findings", []) or []
    except Exception:
        pass
with open(ledger, "w") as f:
    f.write(
        f"# Agent review — {verdict}\n\ndate: {datetime.datetime.now().isoformat()}\n"
        f"range: {rng}\nbranch: {branch}\nfindings: {len(findings)}\n\n"
    )
    for x in findings:
        f.write(
            f"- **{x.get('severity', '?')}** `{x.get('file', '?')}:{x.get('line', '?')}` "
            f"— {x.get('issue', '')}\n  - scenario: {x.get('scenario', '')}\n"
        )
    f.write("\n---\n\n## Full review\n\n" + str(result) + "\n")
print(
    f"agent-review: {verdict} ({len(findings)} findings) — ledger: {ledger}",
    file=sys.stderr,
)
sys.exit(0 if verdict == "approve" else 1)
PY
```

Notes:
- **Fail-closed**: timeout, crash, or unparseable verdict all block the push. The
  human waiver exists precisely for "the reviewer is down and I need to push."
- The ledger is committed (matches the repo's ledger culture: COORDINATION log,
  trace files, decision docs). Ronacher's complaint — "the pull request model doesn't
  carry enough information to review AI generated code; I wish I could see the
  prompts" — is answered by keeping verdicts + findings in-repo.
- `.claude/agents/code-reviewer.md` (same prompt, as an interactive subagent) is a
  convenience twin so the user can say "review this branch" mid-session and get the
  identical rubric; the pre-push script is the enforcement path.

### WT.5 — CI: the layer no one can bypass

**Goal:** the offline suite runs on a clean machine for every PR and every push to
main; merging is impossible while red.
**Deliverable:** `.github/workflows/offline-tests.yml` + branch protection on `main`.
**Done when:** a PR with a failing test shows a red required check and the merge
button is disabled; a green PR shows the check passing in ≤ ~5 min.
**Depends on:** — (independent; do early)

`.github/workflows/offline-tests.yml`:

```yaml
name: offline-tests
on:
  pull_request:
  push:
    branches: [main]
jobs:
  offline-tests:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - name: Install (stub backends only — no GPU, no model downloads)
        run: |
          python -m venv .venv
          .venv/bin/pip install -e '.[web,dev]'
      - name: Offline suite
        run: .venv/bin/pytest -q
```

Branch protection (run once; requires the check to have run at least once so the
context name exists):

```bash
gh api -X PUT repos/deepak0414/video-analytics/branches/main/protection \
  --input - <<'JSON'
{
  "required_status_checks": { "strict": true,
    "checks": [ { "context": "offline-tests" } ] },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

Notes / risks:
- The suite is stub-only by design (the whole point of the hash/sidecar adapters), so
  it should pass on a GitHub runner — but the first CI run is the actual test of that
  assumption; the "Done when" requires it demonstrated. Known safe: ffmpeg comes from
  `imageio-ffmpeg`'s bundled binary, synth clips are generated in-test. The
  paddle/aarch64 gotcha is irrelevant (x86_64 runner, OCR stub).
- If the repo is private on a free plan, GitHub may not enforce branch protection;
  fallback is that L0–L2 still hold locally and `bash_guard.py` blocks agent merges.
  Verify enforcement during rollout and record the outcome here.
- The **golden gate stays manual on the Spark** (GPU + pre-ingested workdir). Its
  enforcement is WT.7's `golden-verified` label, not CI. A self-hosted Spark runner is
  explicitly deferred (matches qa-and-traceability-plan's CI/CD deferral).

### WT.6 — Evidence over assertion, machine-checked

**Goal:** every PR body carries pasted evidence (test output, golden results,
benchmarks) — mechanically required, per P4.
**Deliverable:** `.github/pull_request_template.md`, an `evidence` job in
`.github/workflows/pr-gates.yml`, and a `/verify` slash command that generates the
evidence block.
**Done when:** a PR whose body lacks a filled Evidence section fails the `evidence`
check; `/verify` in a session emits a paste-ready block containing real command
output.
**Depends on:** WT.5 (workflow file), WT.3 (path_guard covers workflows dir)

`.github/pull_request_template.md`:

```markdown
## What & why

<!-- one paragraph; link the plan doc + task IDs this implements -->

## Done-when mapping

<!-- each plan "Done when" item this PR claims, one line each -->

## Evidence

<!-- REQUIRED (CI-checked). Paste real output — never say "tests pass" without it. -->

```text
EVIDENCE: offline suite
<paste: .venv/bin/pytest -q tail>
```

- [ ] Golden gate run (required if adapters/pipeline/config touched — label `golden-verified`):

```text
EVIDENCE: golden gate (or "not required because ...")
```

## Review

- [ ] Agent review ledger committed under `reviews/` (pre-push writes it)
- [ ] Critical paths touched? → user has read them (label `human-reviewed`)
```

`pr-gates.yml` `evidence` job (checks the marker AND that it isn't the empty
template):

```yaml
name: pr-gates
on:
  pull_request:
    types: [opened, edited, synchronize, labeled, unlabeled]
jobs:
  evidence:
    runs-on: ubuntu-latest
    steps:
      - name: PR body must contain filled EVIDENCE block
        env:
          BODY: ${{ github.event.pull_request.body }}
        run: |
          echo "$BODY" | grep -q 'EVIDENCE: offline suite' || { echo 'missing Evidence section'; exit 1; }
          echo "$BODY" | grep -q '<paste:' && { echo 'Evidence section still contains template placeholder'; exit 1; }
          echo "evidence present"
  critical-paths:   # defined in WT.7
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - name: Critical paths require human-reviewed / golden-verified labels
        env:
          LABELS: ${{ join(github.event.pull_request.labels.*.name, ' ') }}
          BASE: ${{ github.event.pull_request.base.sha }}
        run: bash scripts/check_critical_paths.sh "$BASE" "$LABELS"
```

`.claude/commands/verify.md`:

```markdown
---
description: Run all local gates and emit a paste-ready Evidence block for the PR body
allowed-tools: ["Bash", "Read"]
---

Run the offline suite (.venv/bin/pytest -q) and `git status --short`. If the branch
touches adapters/pipeline/config, remind that the golden gate is required and print
the exact RUN_GOLDEN command from CLAUDE.md. Then output a single fenced block:

EVIDENCE: offline suite
<the actual pytest tail — never summarize or say "passed" without the line count>

Include failures verbatim if any. This block is the artifact; do not editorialize.
```

### WT.7 — Bounded human review: the critical-path contract

**Goal:** convert "I can't read everything" into "these files always get my eyes,
provably" (P5).
**Deliverable:** `scripts/critical_paths.txt`, `scripts/check_critical_paths.sh`,
the `critical-paths` CI job (WT.6), and a CLAUDE.md note.
**Done when:** a PR touching `schema.py` without the `human-reviewed` label fails CI;
adding the label (a human act in the GitHub UI — `bash_guard.py` blocks the agent
doing it via `gh`) turns it green without new commits.
**Depends on:** WT.5, WT.6

`scripts/critical_paths.txt` (initial set — reviewed quarterly, kept SHORT on
purpose; a long list collapses back into "review everything" which means nothing):

```text
# pattern (git pathspec)                      required-label
src/va/storage/structured/schema.py           human-reviewed
src/va/storage/structured/migrations*         human-reviewed
src/va/contracts/                             human-reviewed
src/va/pipeline/ingest.py                     human-reviewed
src/va/cli.py                                 human-reviewed
tests/golden_queries/                         human-reviewed
.githooks/                                    human-reviewed
.claude/                                      human-reviewed
.github/                                      human-reviewed
scripts/                                      human-reviewed
src/va/adapters/                              golden-verified
src/va/pipeline/                              golden-verified
config/                                       golden-verified
run-siglip/config/                            golden-verified
run-claude/config/                            golden-verified
```

Rationale for the set: deletion paths (`remove` lives in cli/catalog), the one shared
DB schema, the evolution-tolerant contracts, golden fixtures (a hallucinated fixture
poisons the whole verification layer — see the cobra-kitchen audit), and the trust
machinery itself. `golden-verified` = "the golden gate was run on the Spark and its
output is in the PR Evidence section."

`scripts/check_critical_paths.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
base="$1"; labels="${2:-}"
changed=$(git diff --name-only "$base"...HEAD)
missing=0
while read -r pattern label; do
  [ -z "$pattern" ] && continue; case "$pattern" in \#*) continue;; esac
  if echo "$changed" | grep -q "^${pattern}"; then
    if ! echo " $labels " | grep -q " ${label} "; then
      echo "FAIL: '$pattern' touched but PR lacks label '$label'"
      missing=1
    fi
  fi
done < scripts/critical_paths.txt
exit $missing
```

### WT.8 — The compounding ledger (make lessons permanent)

**Goal:** every correction becomes a permanent rule, the moment it happens ("Every
single time Claude makes a mistake, I don't tell it to do it differently. I tell it
to write it to the CLAUDE.md" — Cherny). This repo already does this well by hand
(the scan_target lesson, determinism≠correctness); mechanize the capture.
**Deliverable:** `.claude/commands/lesson.md`; a `## Lessons (append via /lesson)`
section in CLAUDE.md; a pruning rule.
**Done when:** `/lesson the tracker over-counts at 1fps — always check fps before
trusting counts` appends a dated one-liner to the CLAUDE.md Lessons section and shows
the diff.
**Depends on:** —

`.claude/commands/lesson.md`:

```markdown
---
description: Append a correction/lesson to CLAUDE.md so it is never repeated
allowed-tools: ["Read", "Edit", "Bash"]
---

Take the argument text as a lesson learned. Rewrite it as ONE imperative line
(≤2 sentences, include the why), append it under the "## Lessons" section of
CLAUDE.md as `- YYYY-MM-DD: <lesson>`, then show the diff. If a materially
identical lesson exists, update that line instead of duplicating. If the lesson
is really a *mechanical* invariant ("always/never do X"), say so and propose the
hook that would enforce it (P1) — instructions decay, hooks don't.
```

Pruning rule (goes into the Lessons section header): when the section exceeds ~20
lines, fold stable lessons into the relevant CLAUDE.md prose section or convert them
to hooks — Anthropic's docs warn bloated CLAUDE.md files get ignored, which would
silently disable the whole advisory layer.

### WT.9 — Deferred / optional extensions

Not in scope now; recorded so the triggers are explicit (model-analysis.md style).

| Item | What | Revisit when |
|---|---|---|
| CI agent review | `anthropics/claude-code-action@v1` reviewing PRs as a required check (the Anthropic-internal "Claude Tag" pattern — 65% of their product PRs merge on green checks alone) | the pending ANTHROPIC_API_KEY decision lands (action needs an API key or a `claude setup-token` OAuth token as a repo secret — verify current auth options then) |
| Second-model review | Codex (or other non-Claude) reviewing Claude's diffs — "A second Claude reviewing Claude shares Claude's blind spots. A different model does not" (Ronacher runs exactly this) | a bug ships that the WT.4 reviewer approved |
| TDD enforcement | tdd-guard-style PreToolUse hook (block implementation edits with no failing test) | if test-after creep appears in the ledger |
| Golden gate in CI | self-hosted runner on the Spark executing `-m golden` nightly against `.va-shots` | matches qa-and-traceability-plan's deferred CI/CD; revisit when the Spark has idle headroom |
| Observability-as-trust | Ronacher pattern: pidfile process manager + dual logging so agents self-verify against logs | when the web service becomes long-running/multi-process |

### WT.10 — Trust-layer self-tests (the §8 matrix as code)

**Goal:** the deterministic gates are code, so they get the repo's standard
treatment: automated tests that drive dummy commits/pushes through the real hook
scripts in a sandbox repo and assert block/allow — the stub-adapter philosophy
applied to the trust layer. Manual matrix execution then only covers what genuinely
needs a live session or GitHub.
**Deliverable:** `tests/test_trust_guards.py` (unit tests: feed `bash_guard.py` /
`path_guard.py` stdin JSON payloads, assert exit code 2 + reason on stderr; run
`stop_gate.sh` against a stub pytest), `tests/test_trust_lifecycle.py` (sandbox
flow tests), a `trust_repo` fixture.
**Done when:** every matrix row marked **A** below passes in the offline suite on a
machine with no `claude` login and no `.venv` beyond the test env (proving the fakes
are in charge), and rows marked **M** are the only ones still requiring the manual
rollout run.
**Depends on:** ships incrementally with its subject matter — pre-commit/commit-msg
tests in the WT.1 PR, lifecycle tests in the WT.4 PR, guard unit tests in the WT.3 PR.

Fixture design (`trust_repo`):

```python
# tmp_path layout: origin.git (bare) + work/ (clone with hooks active)
# - copies .githooks/ and scripts/ from the real repo into work/
# - git config core.hooksPath .githooks ; user.name/email set
# - seeds one commit on main, pushes to origin so origin/main exists
#   (review_scope_hash.sh and the docs-only checks diff against it)
# - work/.venv/bin/pytest -> stub script; test toggles pass/fail via a marker file
#   (hooks call the relative path .venv/bin/pytest, so no hook changes needed)
# - fake `claude` prepended to PATH: emits --output-format json payload whose
#   result embeds a ```json verdict block; test selects approve/request_changes
#   via an env-pointed file. agent-review.sh needs no changes either.
```

Example flow test (matrix rows 17/18/23/24 in one arc):

```python
def test_lifecycle_provisional_review_approve_finalize(trust_repo):
    r = trust_repo
    r.write("src/pkg/mod.py", "def f():\n    return 1\n")
    r.git("add", "-A")
    r.set_reviewer_verdict("request_changes")
    out = r.commit("need_agent_review: add f")           # commit lands (post-commit
    assert "REQUESTED CHANGES" in out                    # is trigger, not gate)
    r.set_reviewer_verdict("approve")
    out = r.amend_same_subject()                         # fix loop re-fires review
    assert "review APPROVED" in out
    assert r.try_amend_plain("feat: add f").failed       # no sentinel -> blocked
    r.touch(".commit-approved")                          # the human's act
    assert r.try_amend_plain("feat: add f").ok
    assert not r.exists(".commit-approved")              # sentinel consumed
    assert r.push().ok                                   # hash approved: tests only
```

§8 coverage split — **A** (automated in offline suite): rows 1–6, 17–21, 23–24,
26–28, plus script-level equivalents of 9–13 (guards exercised via stdin, not a live
session). **M** (manual at rollout, unavoidable): 7–8 (real reviewer quality and
latency), 9–13 as observed in a live session, 14–16 (GitHub CI + branch protection),
22 and 25 (live-session recursion/sentinel attempts). The manual set runs once at
rollout; the A set runs forever.

---

## §8 Validation matrix — every gate gets adversarially tested at rollout

Determinism ≠ correctness applies to the gates themselves: each one must be shown to
actually block, not assumed to. Most rows are **automated as permanent offline tests
by WT.10** (sandbox repo + stub pytest + fake reviewer); the rest run manually once
at rollout. Record the observed output (or the test name) next to each row when
executed (this table is the rollout's "Done when").

| # | Layer | Attack | Expected result |
|---|---|---|---|
| 1 | L1 | `git commit` on main | blocked: branch guard |
| 2 | L1 | stage a file containing `hf_` + 24 alphanumerics | blocked: secret scan |
| 3 | L1 | stage `.va/catalog.db` (force-add) | blocked: artifact guard |
| 4 | L1 | delete one `def test_` from a test file, commit | blocked with -1/+0 counts; passes with `ALLOW_TEST_REMOVAL=1` |
| 5 | L1 | commit message with `Co-Authored-By: Claude` | trailer stripped, sign-off appended |
| 6 | L2 | push with one failing test | blocked: pytest tail shown |
| 7 | L2 | push branch with planted logic bug in an untested path | blocked: reviewer finding with file:line; ledger file exists |
| 8 | L2 | `AGENT_REVIEW=skip git push` (from user terminal) | allowed; ledger records WAIVED |
| 9 | L0 | in-session: `git commit --no-verify -m x` | blocked by bash_guard (and deny rule) |
| 10 | L0 | in-session: `AGENT_REVIEW=skip git push` | blocked by bash_guard |
| 11 | L0 | in-session: edit `.claude/hooks/bash_guard.py` | blocked by path_guard; allowed after user `touch .guard-override` |
| 12 | L0 | in-session: break a test, try to end the turn | Stop hook blocks with pytest tail; unblocks when fixed |
| 13 | L0 | in-session: `gh pr merge 12` | blocked by bash_guard |
| 14 | L3 | open PR with failing test | required check red, merge disabled |
| 15 | L3 | PR body without Evidence block | `evidence` check red |
| 16 | L3 | PR touching `schema.py`, no label | `critical-paths` check red; green after user adds label |
| 17 | L1 | commit a planted bug with `need_agent_review` in the message | post-commit fires review; REQUESTED CHANGES + file:line finding visible in the session; ledger written |
| 18 | L1 | fix the bug, `git commit --amend` (tag kept) | review re-fires on the amended commit, approves, approval hash written — final history is one clean commit |
| 19 | L1 | commit without the tag (checkpoint) | no review; returns instantly |
| 20 | L2 | push already-approved content | pre-push logs "skipping re-review"; only the test gate runs |
| 21 | L2 | push a branch containing only `wip:`-declared commits | backstop review fires at pre-push (skipping the lifecycle delays review, never avoids it) |
| 22 | L0/L1 | inspect a review run's transcript/behavior | reviewer session triggered no hooks of its own (VA_AGENT_REVIEW recursion guard) |
| 23 | L1 | commit with a plain subject, no approval and no sentinel | blocked by commit-msg forced declaration |
| 24 | L1 | after reviewer approve: finalize-amend WITHOUT `.commit-approved`, then WITH it | first blocked; second succeeds and the sentinel file is gone afterwards |
| 25 | L0 | in-session: `touch .commit-approved` (Bash) or Write the file | blocked by bash_guard / path_guard — sentinel is human-only |
| 26 | L1 | commit with `need_agent_review` in the body but not the subject | blocked by commit-msg with guidance (the trigger is subject-line, per the lifecycle) |
| 27 | L2 | push a reviewer-approved but never-finalized provisional commit | blocked by pre-push Gate 3 (no `need_agent_review` subject may ship) |
| 28 | L1 | plain-subject commit on a docs-only branch | allowed (docs are exempt from the review lifecycle end-to-end) |
| 29 | L1/L2 | approval granted while dirty tracked edits exist; edits later committed as `wip:` and pushed | backstop review fires (hash covers committed scope only — dirty edits are never blessed) |
| 30 | L1 | stage an extra file into the finalize amend | blocked by commit-msg (finalize may add nothing beyond reviews/ ledgers) |
| 31 | L1/L2 | put a non-.md file under `reviews/` | pre-commit blocks it; smuggled past local hooks with `--no-verify`, the push backstop still reviews it (hash excludes only `reviews/*.md`) |
| 32 | L2 | with an approved branch checked out, push an unreviewed sibling ref | Gate 2 hashes the pushed sha, not HEAD — the sibling gets its own review |
| 33 | L2 | multi-ref push where the first ref's live review reads stdin | remaining refs still gated (loop children run `</dev/null`) — Gate 3 blocks a provisional sibling |
| 34 | L2 | headless reviewer crashes/times out | push blocked fail-closed; stderr log in `.git/`, never in `reviews/` (ledgers-only invariant survives) |
| 35 | L1 | edit CLAUDE.md / `.claude/` / hooks / workflows with a plain `docs:` subject | blocked — instruction-bearing files are never docs-exempt, in all three docs-only checks |
| 36 | L2 | suite exits nonzero with a "33 passed, 1 error" summary (collection error) | push blocked — Gate 1 trusts the exit code, not the summary line |
| 37 | L1/L2 | rewrite a shipped `reviews/` ledger (forge the audit trail), commit (or `--no-verify` + push) | blocked at commit AND at push — ledgers are append-only; `ALLOW_LEDGER_EDIT=1` is the human-only override |

## Rollout order

1. **WT.0 + WT.1 + WT.2(pytest-only)** — pure local determinism, zero LLM cost, one
   session. Run matrix rows 1–6.
2. **WT.4** — `agent-review.sh` + `review_scope_hash.sh` exercised manually from the
   terminal on a real branch first, then wire `.githooks/post-commit`. Rows 7–8 and
   17–22. Calibrate: if the reviewer is noisy on the first three real branches,
   tighten the scope list in the prompt (never loosen fail-closed).
3. **WT.3** — session guards. Restart session (hooks snapshot at start), run rows
   9–13.
4. **WT.5** — CI + branch protection. Row 14, then 15–16 after **WT.6 + WT.7**.
5. **WT.8** anytime.

Estimated total: 2–3 working sessions, of which the only genuinely new moving part is
WT.4 (everything else is static config + shell).

## Open decisions (please edit leanings inline)

- **D1 — When does the reviewer fire? RESOLVED 2026-07-25 (user decision, two
  iterations):** at **post-commit, opt-in via `need_agent_review` in the commit
  message** — the tag is the writer's explicit "work fully done" declaration, and
  post-review fixes squash into the same commit via `--amend` (user's design).
  Supersedes the 2026-07-24 turn-end (Stop hook) decision, which reviewed half-done
  checkpoint turns and fired on conversational turns. Pre-push remains the
  hash-deduplicated fail-closed backstop, so a forgotten tag delays review to push
  rather than skipping it. Watch during rollout: untagged commits reaching push =
  tag discipline slipping; escalation path is inverting the default (review every
  commit, tag to skip) — stricter, never weaker.
- **D2 — Reviewer effort/model.** Leaning: default model on the subscription login,
  `--max-turns 40`, 10-min timeout. Alternative: a cheaper/faster model for small
  diffs with escalation on large ones — tune after observing real latency in rollout
  step 3.
- **D3 — Add ruff (format-only) + a PostToolUse format hook?** The repo currently has
  no linter (CLAUDE.md states this) — adding one is a real convention change, so per
  the repo's own heuristics rule it is flagged here, not done silently. Leaning:
  **yes, `ruff format` only** (no lint rules initially; pure determinism win, removes
  diff noise). If adopted: PostToolUse `Edit|Write` hook runs `ruff format <file>`,
  and CI adds `ruff format --check`.
- **D4 — Does the user's own hand-written code also go through WT.4 review?**
  Leaning: **yes** — the gate is about the code, not its author, and it's simpler
  with no carve-outs. (Solo-repo symmetry of Hashimoto's "anti-idiot, not anti-AI".)
- **D5 — Commit the `reviews/` ledger vs gitignore it.** Leaning: **commit** —
  matches the repo's append-only-ledger culture and answers "what did the reviewer
  see" months later. Alternative: gitignore to keep history clean.
- **D6 — Stop-hook scope.** Leaning: full offline suite with change-detection skip
  (31 s worst case per turn-end). Alternative if it feels heavy in practice: `-x
  --lf` (last-failed first, fail fast) for the in-turn gate, full suite only at
  pre-push.
- **D7 — Human approval sentinel. RESOLVED 2026-07-26 (user decision):** the human
  sits between reviewer verdict and finalization on every reviewed commit. Mechanism:
  finalizing to a plain subject requires reviewer-approved content hash **plus**
  `.commit-approved` (created only by the user; bash_guard/path_guard block agents;
  consumed by commit-msg on success). Extends D1's lifecycle: the trigger tag moves
  to the **subject line** and is *replaced* at finalization, so shipped history
  carries no tags. Accepted consequence: autonomous/overnight runs park at step 5
  (provisional commit exists, reviewed, unshippable) until the human approves —
  that is the intended behavior, and pre-push Gate 3 enforces it.
- **D8 — Separate committer subagent? RESOLVED 2026-07-26 (user decision): no —
  two-agent model.** The main session agent is both writer and committer; the
  reviewer is the only second agent. Rationale: git cannot verify *which* agent ran
  `git commit`, so a distinct committer would be pure convention adding no
  guarantee — independence comes from the reviewer, authority from the human
  sentinel. Optional future ergonomics: a `/commit` slash command that walks
  lifecycle steps 1–6; revisit if digest quality or staging hygiene slips.

## Non-goals

- Golden gate in CI (needs the Spark; deferred, see WT.9).
- Enforcing hooks against a hostile *human* — every local gate is user-bypassable by
  design; only CI (L3) is not.
- Multi-repo / org policy (managed settings); this is a single-repo plan.
- Replacing the existing golden/adversarial fixture workflow — this plan wraps it in
  enforcement (labels + evidence), it does not change it.

## §9 Research provenance (who actually does what)

| Practice adopted here | Who / where | Source |
|---|---|---|
| PostToolUse format hook; "give Claude a way to verify its work → 2-3x quality"; permissions allowlists in git; verify-subagents | Boris Cherny, 13-tips thread (Jan 2026) + howborisusesclaudecode.com | x.com/bcherny/status/2007179852047335529 |
| CLAUDE.md as compounding error ledger; @claude-on-PR → CLAUDE.md commit ("Compounding Engineering") | Cherny / Every Inc plugin | github.com/EveryInc/compound-engineering-plugin |
| Hooks are deterministic vs advisory CLAUDE.md; Stop-hook gate (8-block bound); evidence over assertion; writer/reviewer fresh-context split; adversarial review scoped to correctness | Anthropic best-practices docs (2026) | code.claude.com/docs/en/best-practices |
| Green-CI-gated auto-merge ("Claude Tag", 65% of product PRs) | Anthropic internal, Cat Wu (Dec 2025) | x.com/_catwu/status/2069473118742331608 |
| <5 s pre-commit bypass threshold; LLM review belongs at push/PR, not commit | pre-commit-LLM ecosystem consensus | dev.to/francklebas/llm-code-reviews-on-pre-commit…; imti.co/pre-commit-review-gate |
| `--no-verify` evasion is real; script-based PreToolUse block is the fix | Claude Code issue #40117; block-no-verify package | github.com/anthropics/claude-code/issues/40117 |
| Environment-as-trust: Makefile-style deterministic commands, pidfiles, dual logging; second-model (Codex) review | Armin Ronacher (Jun–Dec 2025) | lucumr.pocoo.org/2025/6/12/agentic-coding; …/2025/12/22/a-year-of-vibes |
| Test suite as the amplifier; YOLO only in sandboxes; checks that fail CI when generated artifacts go stale | Simon Willison (Sep–Oct 2025) | simonwillison.net/2025/Sep/30/designing-agentic-loops |
| Agents delete/weaken tests → treat as the primary adversarial risk | Kent Beck (Jun 2025); tdd-guard | newsletter.pragmaticengineer.com/p/tdd-ai-agents…; github.com/nizos/tdd-guard |
| Dangerous-command PreToolUse blockers; tool-call audit logging | disler/claude-code-hooks-mastery | github.com/disler/claude-code-hooks-mastery |
| Absence of any gate = failure within 24 h on anything real | Jack Dorsey's Bitchat (Jul 2025) | inc.com/chloe-aiello/security-flaws-with-jack-dorseys-bitchat… |
