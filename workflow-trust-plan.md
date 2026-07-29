# Workflow Trust Plan — deterministic gates, hooks, and due-diligence automation

Status: **in rollout**, updated 2026-07-28. WT.0–WT.4 + WT.10 are SHIPPED (PR #13
L1 git hooks, #14 review lifecycle, #15 session guards — all merged to main); WT.5–WT.7
(CI gates) are in flight on `trust/l3-ci`; WT.8 (`/lesson`) and the WT.9 deferrals remain.
Task cards carry as-built notes where reality diverged from the draft — read those before
implementing from a code block.
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
  if [ "$local_sha" = "$zero" ]; then      # remote ref deletion
    case "$remote_ref" in
      refs/heads/main|refs/heads/master)
        fail "deleting remote main is blocked." ;;
    esac
    continue                                # other deletions — nothing to verify
  fi

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
"""PreToolUse guard for Bash (workflow-trust-plan.md WT.3).

Blocks gate-bypass, self-approval, and audit-tampering commands inside Claude
Code sessions. Exit 2 = block (stderr is shown to the agent as the reason);
exit 0 = allow.

DESIGN (learned the hard way across the PR 3 review rounds): decisions are made
by TOKENIZING the command the way a shell does, never by matching flag positions
or by splitting on separator characters with a regex. Every regex attempt leaked
— flag-position anchors missed `commit --amend -n` and `commit -m x -n`, and
`[^|;&\\n]*` / `re.split(r"[|;&]")` broke on an ampersand inside a quoted commit
message, disabling the guard with no intent to evade. Tokenization handles
quoting, clustering, ordering, and value-consuming flags uniformly.

Bash is still a full shell — this layer is hardening against realistic evasion,
not a proof; the un-bypassable layer is CI (WT.5).
"""
import json
import os
import re
import shlex
import sys

ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Every write-redirect spelling shlex can emit as one token: >, >>, 1>, 2>>,
# >| (noclobber override), >& / &> / &>> (fd duplication + merge). Round-11
# major: a hardcoded 5-item set missed >| and >&.
REDIRECT = re.compile(r"^(?:\d*(?:>>?\|?|>&\d*)|&>>?)$")

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)  # malformed payload: never brick the session
cmd = (payload.get("tool_input") or {}).get("command", "") or ""

ROOT = os.environ.get("CLAUDE_PROJECT_DIR", ".")
# Human maintenance window (`touch .guard-override`, honored by path_guard too).
# Scope is NARROW: it relaxes only the gate-machinery write rules — sentinels,
# waivers, approvals, ledgers, PR self-approval and push protection stay live.
OVERRIDE = os.path.exists(os.path.join(ROOT, ".guard-override"))


def block(reason):
    print(f"BLOCKED: {reason}\nCommand was: {cmd}", file=sys.stderr)
    sys.exit(2)


def segments(command):
    """Split into command segments on shell operators WITHOUT cutting inside
    quoted arguments (the round-10 critical). Returns lists of tokens.

    NEWLINES ARE COMMAND SEPARATORS: shlex's whitespace_split swallows them, so
    a multi-line command collapsed into one segment and every rule keyed on the
    first command name skipped lines 2+ (round-15 critical). Lines are split
    first — but only OUTSIDE quotes, so a quoted multi-line message stays one
    token."""
    toks = []
    for line in _split_lines_outside_quotes(command):
        toks += _tokenize(line) + ["\n"]
    return _group(toks)


def _split_lines_outside_quotes(command):
    """Logical command lines, following POSIX quoting rules:
    - inside single quotes nothing escapes and newlines are literal data;
    - a backslash-newline is a LINE CONTINUATION and is removed (round-16
      critical: keeping it prefixed a newline onto the next token, so `git
      commit \\<nl>-n` no longer matched any flag rule);
    - a newline outside quotes separates commands.
    """
    lines, cur, quote, i, n = [], [], None, 0, len(command)
    while i < n:
        ch = command[i]
        if quote == "'":                      # single quotes: literal, no escapes
            cur.append(ch)
            if ch == "'":
                quote = None
            i += 1
            continue
        if ch == "\\" and i + 1 < n:          # escape (unquoted or double-quoted)
            if command[i + 1] == "\n":
                i += 2                        # line continuation: join lines
                continue
            cur.append(ch)
            cur.append(command[i + 1])
            i += 2
            continue
        if quote == '"':
            cur.append(ch)
            if ch == '"':
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            cur.append(ch)
            i += 1
            continue
        if ch == "\n":
            lines.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    lines.append("".join(cur))
    return lines


def _tokenize(command):
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:  # unbalanced quotes: fall back, still quote-naive
        return command.split()


def _group(toks):
    # Operators AND grouping tokens end a segment, so a write verb wrapped in a
    # subshell or brace group is still analyzed as its own command (round-11
    # major: `( touch .commit-approved )` had the group token read as the verb).
    segs, cur = [], []
    for t in toks:
        if t and (all(ch in "|;&\n()" for ch in t) or t in ("{", "}")):
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        segs.append(cur)
    return segs


def norm(p):
    p = p.strip()
    while p.startswith("./"):
        p = p[2:]
    return p if p in ("/", "~", "~/") else p.rstrip("/")


def parts(p):
    return [c for c in norm(p).split("/") if c and c != "."]


# --- protected artifacts -----------------------------------------------------
# Never writable by the agent, maintenance window or not: human authority
# (sentinels), the approval/cache state the gates read, the audit trail, and the
# test runner the gates depend on.
SENTINELS = {".commit-approved", ".guard-override", ".review-approved",
             ".stop-gate-green"}


def is_always_protected(path):
    ps = parts(path)
    if not ps:
        return False
    if ps[-1] in SENTINELS:
        return True
    if "reviews" in ps:                                    # the ledger trail
        return True
    if ps[-3:] == [".venv", "bin", "pytest"] or ps[-1] == ".venv":
        return True  # the test runner IS the gates — and so is its venv
    if ps[-2:] == [".git", "config"] or (".git" in ps and "hooks" in ps):
        return True                                        # git-side gate config
    return False


def is_machinery(path):
    ps = parts(path)
    if not ps:
        return False
    if {".githooks", ".claude", ".github"} & set(ps):
        return True
    if ps[-2:-1] == ["scripts"] and ps[-1] in (
            "agent-review.sh", "review_scope_hash.sh", "setup-hooks.sh",
            # The WT.7 gate IS these two files, and CI runs the PR's own copy —
            # unprotected, a PR could neuter the gate it must pass, and the same
            # PR would then sail through it (PR 4 backstop major).
            "check_critical_paths.sh", "critical_paths.txt"):
        return True
    return False


# Commands that write to the files they are given. (Readers like `cat` are NOT
# here — reading protected files stays allowed; their redirection targets are
# caught separately.)
WRITE_CMDS = {"tee", "cp", "mv", "rm", "ln", "truncate", "dd", "chmod", "chown",
              "touch", "install", "sed"}
# Flags whose VALUE is a separate token (so the token after them is data, not a
# flag). Only genuinely arg-taking ones belong here: -S/--gpg-sign takes an
# ATTACHED optional keyid and -o/--only takes nothing, so listing them swallowed
# the next token and let `git commit -S -n` through (round-12 major).
VALUE_FLAGS = {"-m", "--message", "-F", "--file", "-c", "--reedit-message",
               "-C", "--reuse-message", "--author", "--date",
               "-t", "--template", "--cleanup", "--fixup", "--squash",
               "--trailer"}


def command_name(argv):
    """(basename of the command, its remaining args), skipping leading ENV=value
    assignments. ONE definition, used by every rule — a rule that read seg[0]
    directly was disabled by an env prefix (`LC_ALL=C rm -rf .git`, round 13)."""
    for i, t in enumerate(argv):
        if ENV_ASSIGN.match(t):
            continue
        return os.path.basename(t), argv[i + 1:]
    return None, []


# git global flags that consume the NEXT token, so the subcommand is found by
# position rather than by searching for a known word anywhere in the segment
# (a branch literally named `commit` redirected the scan away from push).
GIT_VALUE_GLOBALS = {"-c", "-C", "--git-dir", "--work-tree", "--namespace",
                     "--exec-path", "--config-env"}


def git_subcommand(seg):
    """(subcommand, its args) for a git invocation, else (None, [])."""
    name, rest = command_name(seg)
    if name != "git":
        return None, []
    i = 0
    while i < len(rest):
        t = rest[i]
        if t in GIT_VALUE_GLOBALS:
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        return t, rest[i + 1:]
    return None, []


def write_targets(toks):
    """Paths this segment could write to: redirection targets, plus the operands
    of a write command (sed only counts with an in-place flag)."""
    targets, redirect_next = [], False
    for t in toks:
        if redirect_next:
            targets.append(t)
            redirect_next = False
            continue
        if REDIRECT.match(t):
            redirect_next = True
    # Drop redirection operators and their targets (already collected above).
    argv, skip_next = [], False
    for t in toks:
        if skip_next:
            skip_next = False
            continue
        if REDIRECT.match(t):
            skip_next = True
            continue
        argv.append(t)
    name, rest = command_name(argv)
    if name in WRITE_CMDS:
        if name == "sed" and not any(
                f == "--in-place" or f.startswith("--in-place=")
                or (f.startswith("-") and not f.startswith("--") and "i" in f)
                for f in rest if f.startswith("-")):
            return targets
        if name == "dd":  # operand syntax: of=<file> is the write target
            targets += [t.split("=", 1)[1] for t in rest if t.startswith("of=")]
            return targets
        targets += [t for t in rest if not t.startswith("-")]
    return targets


for seg in segments(cmd):
    if not seg:
        continue
    lowered = [t.lower() for t in seg]

    # 1. Human-only override tokens (quoting is already stripped by the lexer).
    for t in seg:
        if t.startswith("AGENT_REVIEW=") and t.split("=", 1)[1] == "skip":
            block("the review waiver is human-only (P6).")
        # The hooks honor ANY non-empty value ([ -z ] tests), so `=yes` waives
        # just as `=1` does — match the invariant, not the literal (round 14).
        if "=" in t and t.split("=", 1)[0] in (
                "ALLOW_TEST_REMOVAL", "ALLOW_MAIN_COMMIT", "ALLOW_LEDGER_EDIT",
        ) and t.split("=", 1)[1] != "":
            block("ALLOW_* overrides are human-only; explain the need and ask.")
        if "core.hookspath" in t.lower() and not OVERRIDE:
            block("changing hooksPath disables the trust gates; ask the user.")

    # 2. Writes to protected artifacts / gate machinery.
    for target in write_targets(seg):
        if is_always_protected(target):
            block(f"{target} is a human-only artifact or gate state (sentinels, "
                  "approval/cache files, reviews/ ledgers, the test runner, git "
                  "config) — the hooks and the user write these, not the agent.")
        if is_machinery(target) and not OVERRIDE:
            block(f"{target} is trust-gate machinery — writable only in a human-"
                  "opened maintenance session (.guard-override).")

    # 3. Destructive deletes of the repo/system.
    vname, vargs = command_name(seg)
    if vname == "rm" and any(
            f == "--recursive" or (not f.startswith("--") and ("r" in f or "R" in f))
            for f in vargs if f.startswith("-")):
        for t in vargs:
            if t.startswith("-"):
                continue
            ps, n = parts(t), norm(t)
            if n in ("/", "~") or n.startswith("~/") or ".git" in ps or \
                    ".githooks" in ps or ".claude" in ps:
                block("destructive delete of repo/system paths.")

    # 4. gh self-approval / self-merge / self-labeling (env-prefix safe; attached
    # and short flag spellings included — round-14 major).
    # GitHub treats a PR as an issue, so `gh issue edit <PR#> --add-label` and the
    # REST route both apply REVIEW labels to a PR: gating only `gh pr` left the
    # WT.7 label gate self-servable (PR 4 backstop major).
    gname, gargs = command_name(seg)
    REVIEW_LABELS = ("human-reviewed", "golden-verified")

    # Any HTTP client can hit the same endpoints with the same token
    # (`gh auth token` + curl was demonstrated by the PR 4 backstop). Blocking
    # them is a SPEED BUMP against accident, not a guarantee: with a shared
    # credential, anything the human can do the agent can do. See D9 in
    # workflow-trust-plan.md — the real fix is a separate, label-incapable
    # credential for agent sessions.
    if gname in ("curl", "wget", "http", "https", "httpie", "xh"):
        joined_http = " ".join(gargs)
        if "api.github.com" in joined_http or "/repos/" in joined_http:
            if any(lbl in joined_http for lbl in REVIEW_LABELS) or re.search(
                    r"/(labels|reviews|merge)\b", joined_http) or any(
                    m in joined_http for m in ("addLabelsToLabelable", "addLabels",
                                               "mergePullRequest",
                                               "addPullRequestReview")):
                block("applying review labels / approvals / merges over HTTP is "
                      "human-only (P5). NB: this is defense-in-depth, not a "
                      "guarantee — see D9.")
    # Every spelling that PRINTS the credential, not just `gh auth token`:
    # `gh auth status --show-token` / `-t` emits the same secret.
    if gname == "gh" and gargs[:1] == ["auth"]:
        if gargs[1:2] == ["token"] or any(
                t in ("--show-token", "-t") or t.startswith("--show-token=")
                for t in gargs[1:]):
            block("extracting the human's credential is forbidden — it is the key "
                  "to every human-only action (labels, approvals, merges).")
    # Persistent flags may sit between the noun and the action
    # (`gh issue -R owner/repo edit …`), so positions are parsed, not assumed —
    # the same leak class git_subcommand() exists to avoid (PR 4 backstop major).
    GH_VALUE_FLAGS = {"-R", "--repo", "-H", "--hostname", "-F", "--field",
                      "-f", "--raw-field", "-X", "--method", "-H", "--header",
                      "-q", "--jq", "-t", "--template", "-i", "--input"}

    def gh_words(args):
        out, i = [], 0
        while i < len(args):
            t = args[i]
            if t in GH_VALUE_FLAGS:
                i += 2
                continue
            if t.startswith("-"):
                i += 1
                continue
            out.append(t)
            i += 1
        return out

    if gname == "gh":
        words = gh_words(gargs)
        # `gh alias set m 'pr merge'` then `gh m 14` renames the action past every
        # noun/action rule. Aliases are opaque to a static guard, so block their
        # CREATION rather than pretend to resolve them (finalize-round minor).
        if words[:1] == ["alias"] and words[1:2] in (["set"], ["import"]):
            block("installing gh aliases hides the real subcommand from the "
                  "guards (`alias set`, and `alias import` from a file); ask "
                  "the user instead.")
        noun = words[0] if words else ""
        action = words[1] if len(words) > 1 else ""
        # Any mention of a review label anywhere in a gh command is decisive:
        # -f 'labels[]=human-reviewed', a GraphQL mutation body, --add-label=…
        if noun == "pr" and action == "merge":
            block("merging PRs is a human action in this repo.")
        # Reading label state is legitimate (checking whether the human has
        # applied one); only APPLYING is human-only (PR 4 backstop minor).
        # Applying a label is signalled by a FLAG (--add-label/--approve) or an
        # API field — never by prose. Blocking any command whose text mentions a
        # label name false-blocked `gh pr create/edit --body "<filled template>"`,
        # because the repo's own template names both labels: the guard was
        # breaking the lifecycle steps that open and fix a PR. Prose is not an
        # action; the explicit-flag rule below is what actually gates this.
        if noun in ("pr", "issue") and action in ("edit", "review") and any(
                t in ("--approve", "-a", "--add-label")
                or t.startswith(("--add-label=", "--approve="))
                for t in gargs):
            block("review labels/approvals are human-only (P3/P5).")
        # REST/GraphQL: routes with an explicit verb, PATCH/POST on an issue or
        # pull (PRs ARE issues), and label/review/merge GraphQL mutations.
        if noun == "api":
            joined = " ".join(gargs)
            # A route alone is not an action: `gh api …/issues/N/labels` with no
            # method is a GET, and reading label state is legitimate (row 89).
            # Only an explicit mutating method — or a GraphQL mutation — counts.
            explicit_method = re.search(
                r"(^|\s)(-X\s*|--method[= ])([A-Z]+)", joined)
            # gh api sends POST automatically when fields are supplied, so
            # `gh api …/labels -f 'labels[]=x'` is a mutation with no -X at all.
            has_fields = any(
                t in ("-f", "-F", "--field", "--raw-field", "--input")
                or t.startswith(("-f", "-F", "--field=", "--raw-field=", "--input="))
                for t in gargs)
            verb = (explicit_method.group(3) if explicit_method
                    else ("POST" if has_fields else "GET"))
            if verb != "GET" and any(
                    s in joined for s in ("/labels", "/reviews", "/merge")) or any(
                    s in joined for s in ("addLabelsToLabelable", "addLabels",
                                          "mergePullRequest", "addPullRequestReview")):
                block("applying labels / approving / merging via the API is "
                      "human-only (P3/P5).")
            # Attached spellings (-XPATCH, --method=PATCH) and body-from-file
            # forms (--input f, -F q=@f) keep the method/label out of argv, so
            # match the method textually and treat any file-fed body on an
            # issue/pull route as mutating (PR 4 backstop major).
            mutating = bool(re.search(r"(^|\s)(-X\s*|--method[= ])(PATCH|POST|PUT|DELETE)\b",
                                      joined))
            body_from_file = bool(re.search(r"(--input|[-][fF]\s*\S*@)", joined))
            if (mutating or body_from_file) and (
                    re.search(r"/(issues|pulls)/\d+", joined) or "graphql" in gargs):
                block("mutating a PR/issue (or a GraphQL mutation) via the API is "
                      "human-only (P3/P5) — labels and approvals come from the user.")

    # 5. git: hook-skipping flags, in any position/spelling.
    sub, sub_args = git_subcommand(seg)
    if sub in ("commit", "push"):
        skip = False
        for t in sub_args:
            if skip:
                skip = False
                continue
            if t in VALUE_FLAGS:
                skip = True
                continue
            if not t.startswith("-") or t == "--":
                continue
            if t == "--no-verify":
                block(f"git {sub} --no-verify skips this repo's verification "
                      "hooks (P6: gates have no agent override).")
            # Attached-value forms carry DATA after the value flag: -m'no
            # changes' tokenizes as -mno changes, whose 'n' is message text,
            # not a flag (round-16 minor). Cut at the value flag.
            letters = t[1:]
            for vf in ("m", "F", "c", "C", "t"):
                if vf in letters:
                    letters = letters[:letters.index(vf)]
            # short -n is --no-verify for commit, --dry-run for push
            if sub == "commit" and not t.startswith("--") and "n" in letters:
                block("git commit -n (any position, clustered, or after "
                      "-m/-F values) is --no-verify; the hooks must run.")

    # 6. Pushes that rewrite or delete main, in every git-native spelling.
    # Only a real `git push` — `git stash push -f` is a different command.
    if sub == "push":
        args = sub_args
        flags = [t for t in args if t.startswith("-")]
        nonflags = [t for t in args if not t.startswith("-")]

        def dst(refspec):
            r = refspec.lstrip("+")
            d = r.split(":")[-1] if ":" in r else r
            return d[len("refs/heads/"):] if d.startswith("refs/heads/") else d

        force = any(
            f in ("--force", "--force-if-includes") or f.startswith("--force-with-lease")
            or (not f.startswith("--") and "f" in f[1:])
            for f in flags
        )
        deleting = "--delete" in flags or any(
            not f.startswith("--") and "d" in f[1:] for f in flags)
        if "--mirror" in flags:
            block("git push --mirror force-updates and prunes every remote ref, "
                  "including main.")
        if force and ("--all" in flags or "--branches" in flags):
            block("force-push with --all/--branches rewrites remote main without "
                  "naming it.")
        refspecs = nonflags[1:] if len(nonflags) > 1 else [
            t for t in nonflags if t.startswith("+")]
        dsts = [dst(r) for r in refspecs]
        if any(d in ("main", "master") for d in dsts):
            if force or any(r.startswith("+") for r in refspecs):
                block("force-push to main is blocked (flag, +refspec, refs/heads "
                      "and reordered forms included).")
            if deleting or any(r.startswith(":") for r in refspecs):
                block("deleting remote main is blocked.")
        if (force or any(r.startswith("+") for r in refspecs)) and (
                not refspecs or any(d in ("HEAD", "@", "") for d in dsts)):
            try:
                import subprocess
                branch = subprocess.run(
                    ["git", "-C", ROOT, "symbolic-ref", "--short", "-q", "HEAD"],
                    capture_output=True, text=True, timeout=5,
                ).stdout.strip()
            except Exception:
                branch = ""
            if branch in ("main", "master"):
                block("force-push of the checked-out main rewrites remote main.")

sys.exit(0)
```

`.claude/hooks/path_guard.py` (the agent cannot edit its own cage; the user can, or
can grant a temporary opening):

```python
#!/usr/bin/env python3
"""PreToolUse guard for Edit/Write (workflow-trust-plan.md WT.3).

The agent cannot modify its own cage: trust-gate machinery, human-only
sentinels, and the append-only review ledgers are off-limits to file tools.
Human override for gate maintenance: `touch .guard-override` (gitignored),
which opens the session; remove it to close.
"""
import json
import os
import sys

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)
root = os.environ.get("CLAUDE_PROJECT_DIR", ".")
override = os.path.exists(os.path.join(root, ".guard-override"))
path = (payload.get("tool_input") or {}).get("file_path", "") or ""
rel = os.path.relpath(path, root) if os.path.isabs(path) else path
rel = os.path.normpath(rel)

# Never writable by the agent, even during a maintenance window: the audit
# trail and the human-authority artifacts.
ALWAYS_PROTECTED = (
    "reviews/",                 # append-only audit trail: only agent-review.sh writes here
    ".commit-approved",         # human-only sentinels (D7/P6)
    ".guard-override",
    ".git/.review-approved",    # written only by the hooks after a real review
    ".git/.stop-gate-green",    # stop-gate cache: written only by the hook (row 44)
    ".venv/bin/pytest",         # the test runner IS the test gates (row 44)
    ".git/config",              # one Write of core.hooksPath here disables every git gate
    ".git/hooks/",              # ditto via direct hook placement
)
# Gate machinery: writable only when the human opened a maintenance window.
MAINTENANCE_PROTECTED = (
    ".githooks/",
    ".claude/",                 # settings(.local), hooks, agents — all of it
    ".github/",
    "scripts/agent-review.sh",
    "scripts/review_scope_hash.sh",
    "scripts/setup-hooks.sh",
    # The WT.7 gate is made of these two, and CI executes the PR's own copy —
    # unprotected, a PR could neuter the gate it must pass (PR 4 backstop major).
    "scripts/check_critical_paths.sh",
    "scripts/critical_paths.txt",
)
# Sentinel/state files are matched by BASENAME anywhere, not just at their usual
# path: in a linked worktree the stop-gate cache lives at
# .git/worktrees/<name>/.stop-gate-green, which a prefix match missed (round-14).
STATE_BASENAMES = {".commit-approved", ".guard-override", ".review-approved",
                   ".stop-gate-green"}
PROTECTED = ALWAYS_PROTECTED + (() if override else MAINTENANCE_PROTECTED)
if os.path.basename(rel) in STATE_BASENAMES or \
        any(rel == p or rel.startswith(p) for p in PROTECTED):
    print(
        f"BLOCKED: {rel} is trust-gate machinery or a human-only artifact (P6). "
        f"Propose the change to the user; they can apply it or `touch "
        f".guard-override` to open this session for gate maintenance.",
        file=sys.stderr,
    )
    sys.exit(2)
sys.exit(0)
```

`.claude/hooks/stop_gate.sh` (turn-level test gate; the documented backstop is that
Claude Code overrides the hook after 8 consecutive blocks, so this cannot loop
forever):

```bash
#!/usr/bin/env bash
# Stop hook (workflow-trust-plan.md WT.3): block ending the turn while the
# offline suite is red. Change-detected: skips entirely when src/tests are
# untouched since the last green run. Bounded by Claude Code's 8-consecutive-
# blocks override, so it cannot loop forever.
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
[ -x .venv/bin/pytest ] || exit 0
# Recursion guard: inside the headless reviewer session (WT.4), all repo hooks
# no-op so a review never triggers tests/reviews of its own.
[ -n "${VA_AGENT_REVIEW:-}" ] && exit 0

# Resolve the real git dir: in a linked worktree `.git` is a FILE, and a
# hardcoded path made the cache write fail silently there (round-5 finding).
state="$(git rev-parse --git-dir 2>/dev/null || echo .git)/.stop-gate-green"
# Cache key = HEAD sha + tracked dirtiness + untracked file CONTENT. Names alone
# (git status) let an edited-but-still-untracked file reuse a stale green
# (PR 3 round-3 major, reproduced by the reviewer).
cur=$( (git rev-parse HEAD; \
        git diff HEAD -- src tests config pyproject.toml .claude .githooks scripts; \
        git ls-files -z --others --exclude-standard -- src tests config .claude .githooks scripts \
          | sort -z | xargs -0 -r sha256sum) 2>/dev/null | sha256sum | cut -d' ' -f1)
[ -f "$state" ] && [ "$(cat "$state")" = "$cur" ] && exit 0

# The EXIT CODE is the truth (a summary grep matched "33 passed, 1 error" —
# PR 2 round-4 finding). </dev/null: never consume the harness's stdin.
if out=$(.venv/bin/pytest -q </dev/null 2>&1); then
  echo "$cur" > "$state"
  exit 0
fi
echo "Cannot end turn: offline suite is red. Fix before stopping (P1).
$(echo "$out" | tail -8)" >&2
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
  Reviewer timeout raised 480→900 s during PR 3: a guards-sized diff hit the
  480 s ceiling (empty errlog, fail-closed block — correct behavior, wrong bound);
  commits that trigger reviews run as background jobs so no tool cap applies.
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
  **WT.3 as-built (2026-07-27, PR 3):** PR 2's round-5 carry landed — bash_guard
  blocks writes to `.git/.review-approved` (self-blessing) and to `reviews/`
  (fabricated ledgers), path_guard protects both paths plus all gate machinery;
  sentinel rules are scoped to write verbs so read-only checks pass (row 39);
  stop_gate judges pytest by exit code with `</dev/null` (the round-4 class);
  the guards went LIVE mid-development and blocked their own author — the
  path_guard/stop_gate wedge (row 40) resolved exactly as designed, via the
  human's `.guard-override`.
  **From WT.3 rounds 2-3:** round 2 was a CRITICAL catch — the "fixed" amend had
  committed a tree byte-identical to the rejected one (the fixes sat unstaged;
  the committed-scope review model caught exactly what would have shipped).
  Round 3: stop-gate cache now hashes untracked file CONTENT, not just names
  (reviewer reproduced the stale-green repro); force-push detection extended to
  `+refspec` (`git push origin +main`, `+HEAD:main`) and bare `--force` with the
  upstream branch resolved; bash_guard honors the `.guard-override` maintenance
  sentinel its own block message advertises (full-exemption semantics, matching
  path_guard). Guard test tables grew to ~90 cases incl. every demonstrated bypass.
  **From WT.3 round 4 (two racing reviews, union of 10 findings, all fixed):**
  git global-flag insertion (git -c/-C before commit/push) no longer evades
  subcommand anchors; force-push detection is refspec-aware (exact destination
  component — HEAD/@/refs/heads/ resolved, feature/main-page no longer a false
  positive); stop-gate cache scope includes the gate machinery the suite tests;
  stop-gate state + the pytest binary are self-bless-protected like the review
  hash; the state file resolves `git rev-parse --git-dir` (linked worktrees);
  the maintenance window is scoped to machinery-write rules only, and the
  settings deny list no longer over-blocks --force-with-lease to features.
  **From WT.3 round 5:** quoted refspecs are stripped before destination
  matching; the hand-rolled verb subsets in the artifact rules were unified on
  WRITE_VERBS after the reviewer verified ledger-forging (sed -i), approval
  overwrite (dd), and a pytest symlink attack; path_guard now protects
  .git/.stop-gate-green and .venv/bin/pytest (Edit/Write parity with row 44);
  the stop-gate state path resolves the real git dir (the plan documented the
  linked-worktree fix before the code had it — plan-vs-code drift caught by
  review).
  **From WT.3 round 6:** WRITE_VERBS gained long-form spellings (sed
  --in-place, install); push protection covers --mirror, force --all/--branches,
  and :main deletions (bash_guard) plus refs/heads/main deletion at pre-push;
  path_guard protects .git/config and .git/hooks/ (a single Write there would
  have disabled every git-side gate); stop-gate cache scope includes config/
  (the suite reads config/roles.yaml). Reminder recorded: the human removes
  .guard-override at finalize — leaving it relaxes machinery-write rules for
  every future session on this machine.
  **Round 7 = APPROVE** (4 minors, all fixed before finalize): `--delete`/`-d`
  push spellings added to bash_guard (pre-push already blocked them);
  the commit `-n` rule anchored to flag position so messages containing " -n"
  are no longer false positives; the stop-gate test helper strips
  VA_AGENT_REVIEW so its assertions can never go vacuous inside a reviewer
  session (verified: 123 pass with the guard exported). Seven rounds, 43
  findings, 11 majors + 1 critical — every one a real defect in code that had
  passing tests.
  **Confirming round (round 8) — the payoff for re-reviewing after fixes:** my
  round-7 minor fix REGRESSED the -n rule (anchoring it to the first flag token
  let `git commit --amend -n` through — and --amend is the documented finalize
  command); the same round found that machinery patterns required a trailing
  slash, so `rm -rf .githooks` (deleting the directory core.hooksPath points at)
  was allowed. Both fixed with variant-based tests. Lesson recorded: a fix
  applied AFTER an approval still needs a review round — approvals bless
  content, not intentions.
  **Round 9 — the fix that ended the regex arms race:** three successive
  positional regexes for `-n` each missed a spelling (first-token-only, then
  flag-run-only, then broken by a value token: `git commit -m x -n`). Replaced
  with shell tokenization that parses argv the way git does — value flags
  (-m/-F/--message=…) consume their arguments, so hook-skip flags are caught in
  ANY position while a commit message merely mentioning them stays legitimate;
  `git push --no-verify` (which skips pre-push, i.e. the suite AND the review
  backstop) is covered too, while `git push -n` (dry-run) is not. Structural
  lesson for future guards: when a rule needs to understand a command's
  grammar, parse it — do not iterate on patterns.
  **Round 10 — CRITICAL, and the end of regex guarding entirely:** the
  write-verb rules joined verb to target with `[^|;&\n]*`, and both tokenizer
  loops pre-split on `re.split(r"[|;&]")` — so a single `&` inside a quoted
  argument (e.g. the commit message "feat: ingest & query") made every
  protected-artifact and machinery rule fail to match, and the reviewer chained
  it into a full self-approval: write the approval hash, touch the sentinel,
  finalize, push unreviewed. bash_guard is now built entirely on quote-aware
  shell tokenization (`shlex` with punctuation_chars): segments split on real
  operator tokens, write targets come from redirections + write-command
  operands (incl. `dd of=`), protected paths compare by path COMPONENTS, and
  readers like `cat` are not gated at all. Rewrite bugs the new tests caught
  immediately: `cat` mis-listed as a write verb, `dd`'s operand syntax, and
  `norm("/")` collapsing to empty.
  **Round 11:** two more structural gaps in the same class — grouping tokens
  (`( … )`, `{ …; }`) made the group symbol read as the command name, hiding the
  real write verb; and the redirect-operator list was hardcoded, missing `>|`,
  `>&`, `&>>`. Both fixed by construction (segments break on grouping tokens;
  redirects matched by pattern). Pattern across rounds 9-11: every remaining
  bypass has been "the guard assumed a simpler shell grammar than bash has" —
  which is exactly why WT.5's CI, where no shell runs at all, is the layer the
  design calls un-bypassable.
  **Round 12:** the value-flag skip list wrongly included `-S/--gpg-sign`
  (attached optional arg) and `-o/--only` (no arg), so the token after them was
  swallowed — hiding a following `-n`. Fixed by listing only flags whose value
  is a separate token. Note the trend line: findings per round fell 9 -> 5 -> 5
  -> 4 -> 4 -> 2, and severity fell from architecture (approval scope) to
  grammar (shell parsing) to a single flag-table entry. Rounds are converging,
  and each remaining one costs ~10 min of reviewer time against a real defect
  class — the loop is still paying for itself, but WT.5 (CI) is what makes the
  shell layer's residual risk acceptable rather than load-bearing.
  **Round 13:** both majors were INTERNAL INCONSISTENCY rather than new shell
  exotica — one rule read `seg[0]` directly while another skipped ENV prefixes
  (so `LC_ALL=C rm -rf .git` slipped), and the subcommand was chosen by tuple
  order rather than token position (so a branch named `commit` steered the scan
  away from `push --no-verify`). Fixed by giving the module ONE `command_name()`
  and ONE `git_subcommand()` that every rule calls — the same de-duplication
  lesson the repo already applies to adapters and stores.
  **Round 14:** three more of the same species — `rm -R`/`--recursive` missed
  by a lowercase-only check; `ALLOW_*=yes` unguarded because the guard matched
  `=1` while the hooks test for ANY non-empty value (guard and gate must agree
  on the INVARIANT, not the spelling); and the gh rules still read positional
  tokens instead of the shared helper. Plus a worktree-path gap: state files are
  now matched by basename anywhere. Converged view after 14 rounds: the residual
  finding rate is now one narrow class per round (a flag spelling, a value form,
  a path shape), each caught by an adversary that RUNS the guard rather than
  reading it — which is precisely the "evidence over assertion" principle (P4)
  applied to security code.
  **Round 15 — a second CRITICAL, from the tool contract rather than the shell:**
  `shlex(whitespace_split=True)` consumes newlines as whitespace, so a MULTI-LINE
  Bash command collapsed into one segment and every rule keyed on the first
  command name simply skipped lines 2+ — `echo hi\ntouch .commit-approved`
  forged the human sentinel, and the same trick reached force-push, `rm -rf
  .git`, and `gh pr merge`. The Bash tool accepts multi-line strings routinely,
  so this was reachable without any evasive intent. Fixed by splitting lines
  OUTSIDE quotes before tokenizing (a quoted multi-line commit message stays one
  token). Lesson: a guard must model the exact input its TOOL accepts, not the
  convenient single-line case its tests happened to use.
  **Round 16 — a THIRD critical, in the fix for the second:** my line-splitter
  preserved backslash-newline continuations, so shlex prefixed a literal newline
  onto the next token and every flag/subcommand rule stopped matching (`git
  commit \\<nl>-n`, `git push \\<nl>--force origin main`, `gh pr \\<nl>merge`);
  the same hand-rolled state machine also honored backslashes inside single
  quotes, where POSIX defines none, desyncing quote tracking. Both fixed by
  following the quoting rules exactly. Also: attached-value message forms
  (`-m'no changes'`) were false-positived as `-n`; `rm -rf .venv` was allowed
  even though the Stop gate no-ops without pytest; and pre-push's main-deletion
  branch had no test. RECURRING META-LESSON: three of the last four criticals
  were introduced BY a fix for the previous round — which is the strongest
  possible argument for the re-review-after-fix rule (D1) and for CI (WT.5) as
  the layer that does not depend on getting a shell parser right.
  NB (meta): writing THIS note via a bash heredoc was itself blocked by
  bash_guard (prose quoting blocked commands) — docs go through file tools.
  **From the WT.3 review round (9 findings, all fixed):** the reviewer tested the
  guards EMPIRICALLY and proved five majors — clustered `git commit -nm` bypassed
  every commit gate; gate machinery was writable from Bash (`echo > .githooks/…`,
  `sed -i`, `chmod -x`) since path_guard only sees file tools; the Stop-gate cache
  omitted HEAD so a stale green blessed committed red code; force-push rules
  required `--force` BEFORE `main` (the #40117 reordering); quoted override values
  (`AGENT_REVIEW="skip"`) evaded literal matches. Root cause named by the
  reviewer: the test tables mirrored the regexes, not the invariants — tables are
  now variant-based (clustered/reordered/quoted forms, machinery writes) with 85
  guard tests + a cache-staleness regression. Honest residual: bash_guard is
  hardening, not proof (arbitrary shell, e.g. inline python writes, can evade);
  the un-bypassable layer is CI + branch protection (WT.5). Foreground pushes
  whose backstop review may exceed the ~10-min tool cap should run in background
  (the 900 s reviewer timeout assumes the background path). Known ergonomic cost,
  twice observed: bash heredocs whose PROSE mentions guarded commands trip
  bash_guard — write such docs via the Edit/Write tools instead.
  **From the WT.3 second review round (4 findings, all fixed):** the Stop-gate
  cache now content-hashes untracked src/tests files (names alone let an edited
  untracked file reuse a stale green — reproduced by the reviewer); force-push
  detection covers the git-native spellings `+main`/`+HEAD:main` and bare
  `git push --force` (resolves the checked-out branch, blocks when it is main,
  allows feature-branch force-with-lease); bash_guard honors `.guard-override`
  like path_guard (its own remedy text promised it); guard tests are now 93,
  parametrized against an explicit project dir so a dev-machine override can
  never mask a regression.
  **From the WT.3 third review round (5 findings, all fixed):** the Stop-gate
  cache scope now includes the gate-machinery paths the suite itself tests
  (.claude, .githooks, scripts) — editing a hook no longer reuses a stale green;
  force-push detection resolves HEAD/@ refspecs (which force the CURRENT branch)
  against the checkout; the settings deny prefix `git push --force*` was dropped
  (it over-blocked force-with-lease to feature branches — bash_guard's
  branch-aware logic is the enforcement); and `.guard-override` breadth is now
  split by design: it relaxes ONLY machinery-write rules, while approval/waiver/
  audit rules (sentinels, AGENT_REVIEW, gh pr merge, reviews/) stay enforced in
  both guards even during maintenance — fixing a hook never grants the power to
  approve one's own work. 95 guard tests.

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
# 1800s: review cost scales with BRANCH size, not commit size — the backstop
# re-reads the whole range every round, so a long-lived branch eventually times
# out (480 -> 900 -> 1800 across PRs 3-4). The real fix is smaller PRs; this is
# the ceiling that keeps fail-closed from becoming fail-stuck.
raw=$(timeout 1800 claude -p "$prompt" \
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
# The strongest gate — but NOT an absolute one (workflow-trust-plan.md WT.5).
#
# Local gates (git hooks, session guards) are hardening: 17 review rounds on the
# session guards showed a determined shell can usually find one more spelling,
# and every local gate is deliberately human-overridable. This job runs on
# GitHub's machine, where there is no session to snapshot, no shell to out-parse
# and no sentinel to forge.
#
# The honest limit (found by review, do not restore the stronger claim): a
# `pull_request` run executes the PR-HEAD copy of this file and of
# scripts/check_critical_paths.sh, so a PR can weaken the very checks that gate
# it. What still holds is branch protection: the required CONTEXT names are
# fixed server-side, so deleting or renaming a check leaves it never-reported
# and the merge stays blocked. Weakening a check's *contents* while keeping its
# name is caught only by human review of .github/ and scripts/ — which is the
# `human-reviewed` label, and per D9 that is attestation, not proof.
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

      # cache-dependency-path is REQUIRED here: this repo is pyproject-only with
      # no requirements.txt, and setup-python keys its pip cache off a hashed
      # dependency file. Without it the step can error before installing
      # anything, which would make the required check permanently red.
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: pyproject.toml

      # Stub backends only: no GPU, no model downloads, no network in-test.
      # -e is LOAD-BEARING: configuration.py resolves config/ as
      # parents[2]/"config", which sits outside src/ and is not package data.
      # [web] is not optional either: tests/test_web.py imports
      # fastapi.testclient unguarded, so a missing extra is a collection error.
      - name: Install (stub backends only)
        run: |
          python -m venv .venv
          .venv/bin/pip install --upgrade pip
          .venv/bin/pip install -e '.[web,dev]'

      # The golden modules already skip themselves without RUN_GOLDEN; -m keeps
      # a future module that forgets that guard from pulling a GPU path into CI.
      - name: Offline suite
        run: .venv/bin/pytest -q -m "not golden"
```

Branch protection (run once; requires the check to have run at least once so the
context name exists):

```bash
gh api -X PUT repos/deepak0414/video-analytics/branches/main/protection \
  --input - <<'JSON'
{
  "required_status_checks": { "strict": true,
    "checks": [ { "context": "offline-tests" },
                { "context": "evidence" },
                { "context": "critical-paths" } ] },
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

**As-built (2026-07-28, PR 4) — CI-readiness audit before the first run.** Two install
facts make the drafted YAML wrong as written; both would have produced a red first run:
- **The `web` extra is not optional for the offline suite.** `tests/test_web.py:10`
  imports `fastapi.testclient` unguarded, so a missing extra is a collection ERROR, not
  a skip. Install `-e '.[web,dev]'`.
- **The install must stay editable.** `src/va/configuration.py:22` resolves config as
  `Path(__file__).resolve().parents[2] / "config"`; `config/` lives outside `src/` and is
  not package data, so a non-editable install breaks `load_config()` (and with it every
  e2e test). `-e` is load-bearing, not a convenience.
Also added: `-m "not golden"` on the CI invocation. The golden modules already skip at
module level without `RUN_GOLDEN`, so this is belt-and-braces against a future module
that forgets the guard silently pulling a GPU path into CI.
Verified clean for CI (no change needed): core deps carry no torch/CUDA; `imageio-ffmpeg`
ships its binary in the wheel (no first-use download); no test touches the network, GPU,
or the checked-out repo's git state; `tests/test_trust_hooks.py` / `test_trust_guards.py`
build their own bare origin + clone and neutralize `GIT_CONFIG_GLOBAL/SYSTEM`, so
`actions/checkout@v4`'s shallow clone, detached HEAD and absent git identity cannot
affect them. Expected runtime ~1–3 min on a 2-core runner (~389 offline tests).

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

<!-- One paragraph. Link the plan doc + task IDs this implements. -->

## Done-when mapping

<!-- Each plan "Done when" item this PR claims, one line each. -->

## Evidence

<!-- REQUIRED and CI-checked (P4: evidence over assertion). Paste REAL output —
     never write "tests pass" without the numbers. `/verify` generates this block. -->

```text
EVIDENCE: offline suite
<paste: .venv/bin/pytest -q tail>
```

- [ ] Golden gate run — required if `src/va/adapters/`, `src/va/pipeline/` or any
      `config/` was touched (then also apply the `golden-verified` label):

```text
EVIDENCE: golden gate (or "not required because ...")
```

## Review

- [ ] Agent review ledger committed under `reviews/` (post-commit writes it)
- [ ] Critical paths touched? → the user has read them and applied `human-reviewed`
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
          if ! printf '%s' "$BODY" | grep -q 'EVIDENCE: offline suite'; then
            echo 'missing Evidence section'; exit 1
          fi
          if printf '%s' "$BODY" | grep -q '<paste:'; then
            echo 'Evidence section still contains template placeholder'; exit 1
          fi
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
description: Run the local gates and emit a paste-ready Evidence block for the PR body
allowed-tools: ["Bash", "Read"]
---

Run the offline suite and report it as evidence, not as a claim (P4).

1. Run `.venv/bin/pytest -q` and capture the summary line verbatim.
2. Run `git status --short` and `git diff --name-only origin/main...HEAD`.
3. If the diff touches `src/va/adapters/`, `src/va/pipeline/` or any `config/`
   directory, say that the golden gate is REQUIRED for this PR and print the exact
   command from CLAUDE.md (`RUN_GOLDEN=1 VA_CONFIG_DIR=run-claude/config
   GOLDEN_WORKDIR=.va-shots .venv/bin/pytest -m golden`) — it needs the Spark's GPU
   and a pre-ingested workdir, so it cannot run in CI.

Then output exactly one fenced block, ready to paste into the PR body:

```text
EVIDENCE: offline suite
<the actual pytest tail — the real counts, never a summary>
```

Rules: paste failures verbatim if the suite is red; never write "passed" without the
test count; do not editorialize around the block. The block IS the artifact.
```

**As-built (2026-07-28, PR 4).** The drafted `evidence` step had a latent failure: its
second check was `grep -q '<paste:' && { …; exit 1; }`, and when that grep correctly finds
nothing the compound returns non-zero — as the step's LAST command, that fails the job on
a *good* PR. Rewritten as `if … then … fi` (shown above). Trigger types keep `edited` and
`labeled` deliberately: fixing a PR body or adding a label must re-run the checks WITHOUT
a new commit, which is exactly what WT.7's "Done when" requires.

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
# Bounded human review (workflow-trust-plan.md WT.7 / P5).
#
# PRs touching these paths require the named label before CI goes green. The
# list is deliberately SHORT: a long list collapses back into "review
# everything", which means nothing. Reviewed quarterly.
#
#   human-reviewed   = the user has read this diff themselves.
#   golden-verified  = the golden gate was run on the Spark and its output is
#                      pasted in the PR's Evidence section.
#
# Labels are applied by the human in the GitHub UI; bash_guard.py blocks agents
# from setting them via gh (matrix row 70).
#
# pattern (path prefix)                       required-label

# --- the one shared DB schema + the deletion path ---
src/va/storage/structured/schema.py           human-reviewed
src/va/storage/structured/migrations          human-reviewed
src/va/cli.py                                 human-reviewed
src/va/pipeline/ingest.py                     human-reviewed

# --- evolution-tolerant runtime contracts (QueryPlan/Evidence/Answer) ---
src/va/contracts/                             human-reviewed

# --- the verification layer itself: a hallucinated fixture poisons everything
#     downstream (see the cobra-kitchen audit in CLAUDE.md) ---
tests/golden_queries/                         human-reviewed

# --- the trust machinery: gates that can be weakened silently ---
.githooks/                                    human-reviewed
.claude/                                      human-reviewed
.github/                                      human-reviewed
scripts/                                      human-reviewed

# --- model/backend behavior: needs the real-model gate, not just stubs ---
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
# Bounded human review (workflow-trust-plan.md WT.7).
# Usage: check_critical_paths.sh <base-sha> "<space-separated PR labels>"
# Exit 0 = every touched critical path carries its required label; 1 = missing.
#
# NB: no `set -e` — `grep -q` returning 1 on a non-match is the NORMAL path here,
# and errexit would abort the scan at the first unmatched pattern (making the
# gate silently pass). Failures are accumulated explicitly instead.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
base="${1:?usage: check_critical_paths.sh <base-sha> [labels]}"
labels="${2:-}"
table="scripts/critical_paths.txt"

[ -f "$table" ] || { echo "FAIL: $table missing — the gate cannot verify anything"; exit 1; }

# Three-dot: what THIS branch changed since it diverged, ignoring main's moves.
# -M + --name-status so RENAMES yield BOTH paths: `--name-only` reports only the
# destination, so `git mv src/va/cli.py src/va/cli_main.py` would have escaped the
# exact-file patterns entirely (PR 4 backstop minor).
# core.quotepath=off: git C-quotes non-ASCII paths by default ("sch\303\251ma.py"),
# which never matches a literal prefix — fail-open on exactly the files this gate
# exists to catch. -z + NUL parsing also keeps paths with spaces intact.
raw=$(git -c core.quotepath=off diff -M --name-status "$base"...HEAD) || {
  echo "FAIL: cannot diff against '$base' (fetch-depth: 0 required in CI)"; exit 1; }
# Lines are TAB-delimited: "STATUS<TAB>path" (or "R100<TAB>old<TAB>new").
# cut -f2- keeps every path field, tr splits renames onto their own lines —
# TAB-splitting (not whitespace) so paths containing SPACES survive intact.
# NB: -z is unusable here — bash cannot hold NUL bytes in a variable, so a
# NUL-delimited stream collapses into a single line under command substitution.
changed=$(printf '%s\n' "$raw" | cut -f2- | tr '\t' '\n')

missing=0
while read -r pattern label _rest; do
  case "$pattern" in ""|\#*) continue ;; esac
  [ -n "${label:-}" ] || continue
  # Pure-bash PREFIX matching over the file list. Not `grep` through a pipe:
  # with pipefail a large `changed` list makes printf die of SIGPIPE once grep
  # matches early, the pipeline returns 141, and a genuinely touched critical
  # path reads as untouched — fail-open exactly on the huge PRs where bounded
  # review matters most. `case` is also literal-prefix (grep -F was unanchored
  # substring: `web/scripts/app.js` would have demanded a `scripts/` label).
  hit=0
  while IFS= read -r f; do
    case "$f" in "$pattern"*) hit=1; break ;; esac
  done <<EOF
$changed
EOF
  if [ "$hit" -eq 1 ]; then
    if printf ' %s ' "$labels" | grep -qF -- " $label "; then
      echo "ok:   '$pattern' touched, label '$label' present"
    else
      echo "FAIL: '$pattern' touched but PR lacks label '$label'"
      missing=1
    fi
  fi
done < "$table"

if [ "$missing" -ne 0 ]; then
  echo
  echo "Critical paths need a human in the loop (P5). The user applies the label"
  echo "in the GitHub UI after reading the diff; agents are guard-blocked from"
  echo "setting it. Re-runs on 'labeled' — no new commit needed."
fi
exit "$missing"
```

**As-built (2026-07-28, PR 4).** Two corrections to the drafted checker, both about
failing OPEN: (1) `set -euo pipefail` + `grep -q` aborts the scan at the first pattern
that does NOT match — which is the normal case — so the gate would pass silently after
one miss; errexit is dropped and failures accumulate explicitly. (2) A missing
`critical_paths.txt` or an unresolvable base sha now fails CLOSED with a message instead
of scanning nothing and exiting 0. Matching uses `grep -qF` (fixed strings) so a path
containing regex metacharacters cannot misbehave, and the table is read with a `_rest`
catch-all so trailing words in a row can never be mistaken for a label. Six unit tests
cover this in `tests/test_trust_guards.py` (label required / wrong label / no label
needed / comments ignored / two labels on one PR / missing table).

NB: the table lists `scripts/` and `.claude/` under `human-reviewed`, so every trust PR —
including the one that introduces this gate — requires the human label. That is the
intended behavior.

**As-built (2026-07-28, PR 4 backstop review — 5 findings).** The push-time backstop caught
a real hole in the gate's own premise: **`gh issue edit <PR#> --add-label` applies review
labels to a PR** (GitHub models PRs as issues), and bash_guard gated only `gh pr …` — so an
agent could have applied its own `human-reviewed` label and turned the critical-path check
green with no human ever reading the diff. Now `gh issue` and the `gh api …/labels|reviews|merge`
REST routes are blocked too (row 81). Also: the checker's `printf | grep -qF` form died of
SIGPIPE under `pipefail` on large file lists and failed OPEN (row 82), and `grep -F` matched
substrings rather than path prefixes (row 83) — both replaced with pure-bash `case` prefix
matching. Plan hygiene: the PR-template code block had been corrupted by a sync helper that
stops at the first closing fence — the template CONTAINS nested ```text fences, so the block
kept a stale duplicate. Lesson for the sync scripts: nested fences make naive fence-matching
wrong; verify synced blocks, do not assume.

**Backstop round 2 (4 findings) — the CI gate would never have gone green.** `cache: pip`
on a pyproject-only repo can error at the setup step *before installing anything*, which
would have left the new required check permanently red and blocked every merge;
`cache-dependency-path: pyproject.toml` makes it explicit (row 84). The label hole was
still open by two more routes — `gh api -X PATCH …/issues/N -f 'labels[]=…'` (no `/labels`
in the path) and GraphQL mutations (row 85) — and, most instructively, `gh issue -R o/r
edit …` slipped past because the new gh rule read noun/action at FIXED ARGV POSITIONS: the
exact leak class `git_subcommand()` was written to eliminate in round 13, reintroduced in
new code in the same session (row 86). Standing lesson, now twice-earned: when adding a
rule, reuse the parsing helper — a fresh positional shortcut is a fresh bypass.

**Backstop round 3 (6 findings) — the gate could disable itself.** The sharpest finding of
PR 4: `scripts/check_critical_paths.sh` and `critical_paths.txt` were NOT in the guards'
protected sets, and `pr-gates.yml` executes the PR's OWN copy of them — so a PR could
delete the `scripts/` row (or make the checker `exit 0`) and then sail through the gate it
had just disabled. Both files are now guarded like the other trust scripts (row 87). Also
fixed: attached API method spellings and file-fed bodies (`-XPATCH … --input p.json`,
`-F query=@mutation.graphql`) kept the method and the label string out of argv (row 88);
label READS are no longer blocked, only applications (row 89); renames are caught by
diffing `-M --name-status` and taking both paths (row 90); and the branch-protection
recipe now requires all three contexts — it listed only `offline-tests`, so the evidence
and critical-path checks would have been advisory while COORDINATION.md announced them as
merge-blocking. Test-quality lesson: the SIGPIPE regression test wrote ~14 KB, which FITS
in the 64 KB pipe buffer — it would have passed against the very bug it was named for.
A regression test that cannot reproduce the original failure is decoration.

**Operational lesson (PR 4): review cost scales with BRANCH size, not commit size.**
The backstop re-reviews `origin/main...HEAD` every round, so a branch that keeps growing
gets slower every round — the timeout went 480s (PR 2) -> 900s (PR 3) -> 1800s (PR 4),
and round 4 of PR 4 hit the ceiling and blocked the push with an empty error log (correct
fail-closed behavior, wrong reason). Two implications: (1) prefer SMALLER PRs — the
lifecycle rewards them structurally, since review time is roughly proportional to
cumulative branch diff; (2) a timeout is indistinguishable from a hostile reviewer crash,
so it must stay fail-closed, with the human waiver as the escape hatch.

**Backstop round 4 (4 findings) — the label's guarantee is weaker than the docs claimed.**
`gh auth token` + `curl` applies a label with the same credential, and *no CI layer sits
above the label because CI is what consumes it as proof*. HTTP clients are now blocked as
defense-in-depth (row 91) and the overclaim in CLAUDE.md/COORDINATION.md is corrected, but
the honest conclusion is recorded as **D9**: with a shared credential, client-side blocking
is a speed bump, not a guarantee. Also fixed: the evidence check accepted an EMPTY block
(deleting the placeholder was the natural "cleanup") — it now requires real counts (row
92); `critical-paths` checked out the MERGE ref, so another PR merging to main could
demand labels on an unrelated PR (row 93); and — most consequentially for this PR — the
`trust_repo` fixture inherited `VA_AGENT_REVIEW`, so **inside a reviewer session the suite
reported 5 false failures**, which is exactly the artifact this PR makes the required check
and `/verify`'s evidence.

**Backstop round 5 — the reviewer caught a cross-session contamination (CRITICAL).**
`git add -A tests` swept the OTHER session's uncommitted `tests/test_migrations.py` into
this branch's commit; that test imports `MIGRATIONS`/`SCHEMA_VERSION` from a `schema.py`
change that exists only in the shared worktree. On a clean checkout — i.e. the CI job this
very PR introduces — pytest collection would have failed, turning the branch's own
required check permanently red (row 95). Fixed by rebuilding the commit from an explicit
file list rather than `-A` on a directory. Two lessons: (1) **never `git add -A` a
directory in a shared worktree** — stage explicit paths, because the "other agent's files"
problem is exactly what COORDINATION.md exists to manage; (2) the test-deletion guard
correctly blocked the naive `--amend` fix (it looked like removing 7 tests), and the right
move was to rebuild the commit rather than reach for the human-only override — a gate
firing on a legitimate action is a signal to change the approach, not to escalate.
Also fixed: `gh auth status --show-token` printed the credential the round-4 speed bump
was written to protect (row 94).

**Finalize round — APPROVE with 2 minors (both fixed before shipping).** The critical-path
checker read git's default C-quoted output, so a non-ASCII filename (`sch\303\251ma.py`)
never matched a literal prefix and needed no label — fail-open on precisely the files the
gate protects; now `core.quotepath=off` with TAB-delimited parsing, which also keeps paths
containing spaces intact (row 96). NB an implementation trap found while fixing it: `-z`
is unusable here because **bash cannot store NUL bytes in a variable**, so a NUL-delimited
stream collapses to one line under command substitution — the first fix broke 5 tests and
the suite caught it immediately. Second minor: `gh alias set m 'pr merge'` renames a
subcommand past every noun/action rule; since aliases are opaque to a static guard, alias
CREATION is now refused rather than pretending to resolve them (row 97).

**Confirming round — APPROVE with 3 minors, and two of them were the guard blocking
LEGITIMATE work:** `gh api …/issues/N/labels` with no method is a GET, and reading label
state is explicitly allowed by row 89 — the route-substring match had made it un-runnable
(row 99); and `gh pr create --body "$(cat body.md)"` was refused because the repo's own PR
template mentions both label names, i.e. the guard blocked the very lifecycle step that
opens a PR (row 100). Third: `--show-token=true` slipped the credential guard — the
attached-spelling class, now closed for the third time in this project. Worth stating as a
standing lesson: **an over-broad guard is a defect too.** Every false block pushes the
agent toward a workaround, and a workaround is exactly the behavior the guards exist to
prevent; measure a rule by both what it stops and what it breaks.

**Final round — APPROVE, 3 minors, fixed at the ROOT rather than by another spelling.**
Two of the three were false blocks again (`gh pr edit --body "<filled template>"`), which
made the underlying error clear: the guard treated *any command whose text mentioned a
label name* as an application. **Prose is not an action.** Applying a label is signalled by
a FLAG (`--add-label`, `--approve`) or an API field — so the blanket text rule is gone and
the explicit-flag rule does the gating (row 102). Removing it immediately exposed a real
gap the tests caught in seconds: `gh api …/labels -f 'labels[]=x'` carries no `-X`, but
**gh api sends POST when fields are present** — encoded properly now (row 103). Also
closed: `gh alias import` (file-based sibling of `alias set`, row 101) and GraphQL mutation
names on the curl path (row 104). Rounds since finalize: approve/approve/approve, findings
2 -> 3 -> 3, all minors, all speed-bump or false-block class — the convergence signal that
says stop.

**Closing round — APPROVE, and the most important finding was about a CLAIM, not code.**
The reviewer rejected this PR's own framing: calling CI "the un-bypassable layer" overstates
it, because a `pull_request` run executes the **PR-head copy** of the workflow YAML and of
`check_critical_paths.sh` — so a PR can weaken the checks that gate it. What actually holds
is narrower and worth stating precisely: **branch protection pins the required CONTEXT
names server-side**, so deleting or renaming a check leaves it never-reported and the merge
stays blocked; weakening a check's *contents* while keeping its name is caught only by
human review of `.github/` and `scripts/` — i.e. by the `human-reviewed` label, which per
D9 is attestation, not proof. The claim is corrected in the workflow header and CLAUDE.md.
Also: `--approve=true` (attached spelling, row 105), and the now-dead `mentions_label`
variable removed along with the comment that still described the deleted blanket rule —
stale comments describing removed behavior are how the next reader gets misled.
**Stopping here:** four consecutive approves, findings 2/3/3/3, all minors of the
speed-bump or false-block class. The remaining tail is enumeration, not risk.

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
description: Record a correction in CLAUDE.md's Lessons section so it is never re-learned
allowed-tools: ["Read", "Edit", "Bash"]
---

Take the argument text as a lesson learned — usually something the user just corrected,
or a mistake a review caught.

1. **Decide where it belongs first.** If the lesson is a *mechanical invariant*
   ("always/never do X", "never run Y"), say so and propose the hook that would enforce
   it (`.claude/hooks/` for session actions, `.githooks/` for git actions, a test for
   code invariants). Instructions decay; hooks don't. Offer the prose line as the
   fallback, not the default.
2. Otherwise rewrite it as ONE imperative line, ≤2 sentences, that **includes the why** —
   a rule without its reason gets discarded by the next reader who thinks it's arbitrary.
   Prefer the concrete failure ("the SIGPIPE test wrote 14 KB against a 64 KB buffer")
   over the abstraction ("test your tests").
3. Append it under `## Lessons` in CLAUDE.md as `- YYYY-MM-DD: <lesson>` (get today's
   date with `date +%F`; newest at the bottom). If a materially identical lesson already
   exists, UPDATE that line instead of adding a near-duplicate.
4. Show the diff.
5. If the section now exceeds ~20 entries, say so and propose which stable entries to
   fold into the relevant prose section above (or into a hook) and delete from the list —
   `tests/test_lessons_section.py` enforces this, so an oversized list fails the suite.

Never silently reword an existing lesson's meaning: correcting a lesson is itself a
lesson, and the reason it changed is the part worth keeping.
```

Pruning rule (goes into the Lessons section header): when the section exceeds ~20
lines, fold stable lessons into the relevant CLAUDE.md prose section or convert them
to hooks — Anthropic's docs warn bloated CLAUDE.md files get ignored, which would
silently disable the whole advisory layer.

**As-built (2026-07-28, PR 5).** WT.8 shipped as specified plus two additions the work
itself demanded:
- **The pruning rule is a test, not a comment** (`tests/test_lessons_section.py`): dated
  one-liners, no duplicates, and a hard 20-entry budget whose failure message says to fold
  rather than raise the limit. A prose rule about not letting prose rot is self-defeating.
- **The first lesson recorded became a hook** (see the deferred-hook note in WT.8), which is what the command's
  own step 1 demands: a polling loop whose `pgrep`/`ps|grep` pattern is written in the
  matcher call matches its own command line and can never terminate. Two such watchers hung
  in a single session, and the user spotted one before the author did.
Writing that hook reproduced the project's most persistent error a third time: the first
draft searched the command TEXT for "until", which false-blocked a python heredoc whose
test strings mentioned loops. Keyed on the first token instead. **Prose is not an action**
now has three independent occurrences — label rules, `--no-verify` matching, and this —
which is the signal to treat it as a design rule for every future guard, not a lesson.
The `/lesson` command's dedup rule fired on its first real use: the invocation matched an
existing entry and updated it in place instead of appending a near-duplicate.

**PR 5 review round 1 (4 findings, 2 major) — the new guard failed the file's own design
rule.** The loop check keyed on the FIRST segment's first token, so `cd /tmp && until …`
bypassed it entirely (see the deferred-hook note in WT.8); and it scraped the pattern with a regex, capturing `root`
from `pgrep -u root X` — a dead-end block whose suggested fix could not even be applied
(see the deferred-hook note in WT.8). The reviewer cited the module's own header back at it: *tokenize, don't regex*.
Rewritten to walk tokens, skip value-taking flags, scope to the loop CONDITION (a matcher
in the body is not the terminator), and require a full-cmdline matcher — plain `pgrep X`
matches comm, which for a shell loop is "bash", so it cannot self-observe (see the deferred-hook note in WT.8). A
dead comment describing a per-segment check that never existed was also removed from both
the guard and this plan, where it would have advertised coverage the code did not have.

**PR 5 review round 2 (5 findings, 1 major).** The loop guard examined only the FIRST
`until`/`while`, so a self-observing loop placed after an earlier one was never checked
(see the deferred-hook note in WT.8) — the same "first occurrence only" shape as PR 4's first-segment bug, one level
up. Three spelling/semantics fixes: `pgrep --full`, and `grep -e X` where the flag's value
IS the pattern (it was being skipped as a mere flag value, discarding the thing being
matched); `$VAR` patterns are now allowed because the loop's own cmdline holds the literal
text while pgrep searches the expanded value — blocking them also offered an unusable fix.
And a test-quality finding worth more than the code ones: the Lessons length budget read
only each entry's FIRST PHYSICAL LINE, but CLAUDE.md wraps at ~90 columns, so the 400-char
assertion **could never fail** — the second unenforceable test caught in two PRs (after the
14 KB SIGPIPE probe). Entries are now joined before measuring; the longest is 301/400, so
the budget can actually bite.

**PR 5 review round 3 (4 findings, 1 major) — the plan asserted a fix it did not have.**
Round 2's note and its matrix row (since removed with the descope) claimed nested loops were covered; they were not. Openers were
found only when a segment's FIRST token was `until`/`while`, and a nested opener sits after
`do` inside the same segment — so `while true; do until ! pgrep -f X; …` sailed through,
with no test covering it. This is the FOURTH time in this project that documentation
asserted a guarantee the code did not provide (after the label proof, the gate's own
machinery, and "un-bypassable CI"). The check now walks the RAW token stream, so nesting
and pipeline structure are both visible; the claim was rewritten to say what was actually true.
Three supporting fixes: clustered value flags (`pgrep -fu root X` — the round-1 defect
returning in short-flag clusters), the `grep -v grep` self-exclusion idiom (a filter, not
a matcher), and per-pipeline `ps` scoping. Ordering bug found by the harness while fixing:
the clustered-flag skip ran before the `-e`-is-the-pattern case and swallowed the pattern.

**PR 5 review round 4 — DISPUTED (the reviewer's first demonstrably false report).**
Round 4 restated round 3's four findings VERBATIM — identical wording, identical stale
line numbers — against a commit (6d5db3a) that had already fixed all four. Verified by
running the COMMITTED guard against each claim:

| Round-4 claim | Reviewer said | Actual behavior of the reviewed commit |
|---|---|---|
| nested opener `while true; do until ! pgrep -f X; …` | allowed, "no test covers it" | **blocked**, and the test is present in the reviewed commit |
| `pgrep -fu root "[p]ortscan.py"` | falsely blocked | **allowed** |
| file-grep `&&` ps-pipeline | falsely blocked | **allowed** |
| `\| grep -v grep` self-exclusion idiom | falsely blocked | **allowed** |

No code change is warranted for round 4; the findings are recorded here as disputed with
their evidence, per the reviewer prompt's instruction to re-judge disputed findings on the
merits. Two lessons, both about the review LAYER rather than the code:
- **The reviewer is fallible in a specific way: it can regurgitate its own prior ledger
  instead of re-verifying.** Roughly thirty rounds across five PRs produced almost entirely
  real findings — and then four confident, specific, fabricated ones. Treat a repeated
  finding as a prompt to re-test, not as confirmation; "the reviewer said so" is not
  evidence, which is the same P4 rule the humans in this loop are held to.
- **The prompt's dispute channel is unusable by the agent.** It invites a rebuttal in a
  `reviews/` ledger, but `reviews/` is guard-protected against agent writes (rows 37/48),
  so disputes have to land somewhere the agent may actually write — here, the plan. Either
  the prompt should point at the plan, or agent-authored dispute files need a carve-out
  (e.g. `reviews/disputes/`, append-only, still barred from editing verdicts).

**PR 5 review round 5 — the dispute WORKED, and the re-review earned its keep.** Round 5
dropped all four disputed findings (no repetition) and produced five genuinely new ones
against the token-stream rewrite. The major: `SEPARATORS` listed `"\n"` but the stream was
built WITHOUT newline tokens, so a loop opener on line 2+ never passed the previous-token
test and **multi-line commands bypassed the guard entirely** — the same multi-line class as
PR 3's round-15 critical, in new code, for the second time. Also fixed: condition tokens
were re-joined and re-split on whitespace, discarding quoting (a quoted multi-word pattern
was judged by its first word); clustered `-qe` and attached `-m1` flag values were
mis-consumed; stage commands used `basename(token0)` instead of `command_name()`, so an
`ENV=value` prefix disabled the rule — violating the file's OWN round-13 single-definition
rule for the second time; and `else`/`elif` were missing from the separator set.
Standing observation after five PRs: the recurring defects are not novel, they are the
same four shapes (multi-line, quoting, position-not-prose, use-the-shared-helper) returning
in each new rule. A checklist for new guard rules would be worth more than another fix.

**PR 5 review round 6 (4 findings, 1 major) — a false block that would have hit daily.**
Heredoc bodies were tokenized as commands, so writing a loop INTO a file (`cat > w.sh
<<'EOF' … EOF`) was blocked — and heredocs are how most of this repo's tooling gets
authored, so the guard would have obstructed the exact workflow it lives in. Bodies are now
skipped as data (see the deferred-hook note in WT.8). Also: the check recurses into `bash -c '…'` payloads, which is
how a backgrounded watcher is actually spelled — the realistic accident path, and the one
gap that would have shipped had this stopped at round 6 (see the deferred-hook note in WT.8); subshell parens made
`command_name()` return "(" so no branch fired; and the self-exclusion idiom was recognized
only in its short `-v` spelling (see the deferred-hook note in WT.8). The loop check is now a depth-bounded recursive
function rather than a straight-line block.

**PR 5 review round 7 (4 findings, 1 major) — second-order bugs inside round 6's fixes.**
The `bash -c` recursion rebuilt its own token stream WITHOUT the heredoc filter, so a
heredoc body containing `bash -c '<loop>'` was false-blocked while the note claimed bodies
are data — the filter existed in one scan and not the other. Heredoc bodies are now
stripped ONCE, on raw lines, before any quote-aware pass: an apostrophe in a body used to
desync quote tracking and swallow the terminator (so everything after a heredoc was skipped
as data), and the opener regex matched herestrings (`<<<`) and `<<` inside quotes. The
payload scan also ran past command separators, attributing a later command's `-c` to bash.
Pattern worth noting: rounds 6 and 7 were both about **a fix's interaction with an earlier
fix**, not about the original problem. Each new special case multiplies with the existing
ones, which is the real cost of special-case-driven hardening — and the argument for
keeping such rules small enough to reason about whole.

**PR 5 SCOPE DECISION (2026-07-29, user's call): the self-observing-loop hook is REMOVED
from this PR and deferred to its own.** WT.8's deliverable — `/lesson`, the Lessons
section, and `tests/test_lessons_section.py` — has been clean since round 1. The hook was
a bonus, added because `/lesson`'s own step 1 says "propose the hook"; it then consumed
EIGHT review rounds and ~26 findings, while the deliverable sat finished behind it.
The decisive signal came in rounds 6-8: findings stopped being coverage gaps and became
**bugs in the fixes for the previous round** — a herestring guard whose comparison could
never be true, a `break` on a token never emitted, parity counting where a state machine
was needed. Code that looked right, passed the cases I imagined, and did nothing.
Recorded for whoever picks the hook up (matrix rows 106-124 were removed with it):
- Rounds 1-5 found REAL gaps and are worth keeping: multi-line commands, nesting, quoting,
  `bash -c` payloads, env prefixes, clustered/attached flag values, `pgrep` without `-f`
  (matches comm, cannot self-observe), `$VAR` patterns, `grep -v` self-exclusion, heredoc
  bodies as data.
- The recommended shape is SMALL: top-level `until`/`while` conditions containing
  `pgrep -f X` or `ps … | grep X` with an unbracketed X — ~15 lines, no recursion, no
  shell model, with heredocs/`bash -c`/nesting explicitly out of scope. It catches both
  watchers that actually hung; the elaborate version's extra coverage cost three rounds
  and produced only inert patches.
- Standing lesson: **a rule small enough to reason about whole beats a complete one**,
  especially for a speed bump whose failure mode is a wasted background process.

**Confirming round after the descope — my cleanup broke the record it was cleaning.**
The regex that retargeted dangling citations (`\(rows? \d{3}\)`) was over-broad and
also rewrote FOUR STILL-VALID ones — rows 100, 102, 103, 104 in PR 4's label-guard
narrative — pointing live history at an unrelated note. Restored by hand and verified
against the rows, which still exist. The lesson is not "be careful with sed": it is that
**a bulk edit over prose needs the same verification as a code change** — I ran it,
saw a plausible count, and moved on without checking WHICH citations matched. Second
finding: `entries()` in the Lessons test only recognized conforming bullets, so a
malformed one was invisible to every check (budget, length, date, dedup all iterate over
`entries()`), and nothing asserted the section contains nothing else. A conformance test
now covers it — and, per the lesson this project has now learned twice, it was verified
to FAIL against a planted malformed bullet before being trusted.

### WT.9 — Deferred / optional extensions

Not in scope now; recorded so the triggers are explicit (model-analysis.md style).

| Item | What | Revisit when |
|---|---|---|
| CI agent review | `anthropics/claude-code-action@v1` reviewing PRs as a required check (the Anthropic-internal "Claude Tag" pattern — 65% of their product PRs merge on green checks alone) | the pending ANTHROPIC_API_KEY decision lands (action needs an API key or a `claude setup-token` OAuth token as a repo secret — verify current auth options then) |
| Second-model review | Codex (or other non-Claude) reviewing Claude's diffs — "A second Claude reviewing Claude shares Claude's blind spots. A different model does not" (Ronacher runs exactly this) | a bug ships that the WT.4 reviewer approved |
| TDD enforcement | tdd-guard-style PreToolUse hook (block implementation edits with no failing test) | if test-after creep appears in the ledger |
| Golden gate in CI | self-hosted runner on the Spark executing `-m golden` nightly against `.va-shots` | matches qa-and-traceability-plan's deferred CI/CD; revisit when the Spark has idle headroom |
| Observability-as-trust | Ronacher pattern: pidfile process manager + dual logging so agents self-verify against logs | when the web service becomes long-running/multi-process |
| Self-observing-loop guard | block `until ! pgrep -f X` where the pattern is written in the matcher call (both of this session's hung watchers) | its own PR — see the WT.8 scope decision for the recommended small shape and the five rounds of real findings to keep |

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
| 38 | L0 | in-session: write `.git/.review-approved` directly (self-bless) or fabricate a `reviews/*.md` ledger | blocked by bash_guard (write-verb rules) and path_guard (`reviews/`, `.git/.review-approved` protected) — PR 2 round-5 carry |
| 39 | L0 | read-only sentinel checks (`ls .guard-override`, `cat .git/.review-approved`) | allowed — sentinel rules scope to write verbs only |
| 40 | L0 | the wedge: red suite whose only fix is in a guard-protected file | Stop gate blocks the turn, path_guard blocks the fix — resolution is the human's `.guard-override` (observed live during PR 3 development, before the guards' own PR existed) |
| 41 | L0 | `git -c x=y commit -nm` / `git -C . push --force origin main` (global-flag insertion) | blocked — subcommand rules carry a git-global-flag prefix (GITCMD) |
| 42 | L0 | force-push via `HEAD`/`@`/`refs/heads/main` refspecs, or bare from a main checkout | blocked — refspec-aware destination matching + upstream resolution |
| 43 | L0 | `git push --force-with-lease origin feature/main-page` | allowed — exact ref-component match, no substring false positive |
| 44 | L0 | write `.git/.stop-gate-green` or tamper with `.venv/bin/pytest` | blocked — stop-gate self-blessing closed like row 38's review hash |
| 45 | L0 | edit gate machinery, then end the turn on a red suite | Stop-gate cache covers `.claude/`, `.githooks/`, `scripts/` — stale greens don't bless machinery edits |
| 46 | L0 | during a maintenance window: `touch .commit-approved` / `AGENT_REVIEW=skip` | still blocked — `.guard-override` relaxes ONLY machinery-write rules |
| 47 | L0 | force-push to a QUOTED refspec (`origin "main"`) | blocked — push tokens are shell-quote-stripped before destination matching |
| 48 | L0 | `sed -i` a ledger / `dd` the approval hash / `ln -sf` over pytest, or Edit/Write the stop-gate cache and pytest paths | blocked — one WRITE_VERBS list guards every protected artifact in bash_guard, and path_guard covers both self-bless paths |
| 49 | L0 | long-form write verbs (`sed --in-place`, `install`) on protected artifacts/machinery | blocked — WRITE_VERBS covers long-form spellings |
| 50 | L0/L2 | rewrite main without naming it: `push --force --all`, `push --mirror`, delete via `:main` / ref-deletion push | blocked in bash_guard AND pre-push (deletion of refs/heads/main) |
| 51 | L0 | Write to `.git/config` (hooksPath) or `.git/hooks/` | blocked — path_guard protects the git-config attack surface |
| 52 | L0 | edit `config/roles.yaml`, end turn on the resulting red suite | blocked — stop-gate cache scope includes `config/` |
| 53 | L0 | `git push --delete origin main` / `-d origin main` | blocked in bash_guard too (was pre-push only) |
| 54 | L0 | commit message legitimately containing " -n" | allowed — the -n rule matches flags before the message, not message text |
| 55 | L0 | n-flag after other flags: `commit --amend -n`, `-a -n`, `-q -nm` | blocked — the rule scans the whole flag run, not just the first token |
| 56 | L0 | bare-directory destruction: `rm -rf .githooks`, `rm -rf .claude`, `rm -rf reviews`, `mv .claude /tmp` | blocked — machinery patterns no longer require a trailing slash |
| 57 | L0 | hook-skip flags in every argv position: `commit -m x -n`, `-F file -n`, `--message=… -n`, `push --no-verify` | blocked — decided by shell tokenization, not flag-position regex |
| 58 | L0 | a commit MESSAGE that mentions `-n` or `--no-verify`; `git push -n` (dry-run) | allowed — value flags consume their argument; short -n only means no-verify for commit |
| 59 | L0 | shell separator inside a QUOTED argument (commit message with `&`, `cp "/tmp/a&b" .git/.review-approved`) | blocked — segmentation is quote-aware tokenization, not `re.split` on separator chars |
| 60 | L0 | `dd of=<protected>` operand syntax; `rm -rf /` | blocked — operand-form write targets and root paths parsed explicitly |
| 61 | L0 | reading protected files (`cat .git/.review-approved`, `cat .claude/settings.json`) | allowed — only write commands and redirection targets are gated |
| 62 | L0 | write verb wrapped in a subshell or brace group: `( touch .commit-approved )`, `{ cp /tmp/h .git/.review-approved; }` | blocked — grouping tokens end a segment, so the real verb is analyzed |
| 63 | L0 | alternate write redirects into protected artifacts: `>\|`, `>&`, `&>>`, `2>` | blocked — redirect operators matched by pattern, not a hardcoded list |
| 64 | L0 | `-n` behind flags that take no separate-token value: `commit -S -n`, `--gpg-sign -n`, `-o -n` | blocked — VALUE_FLAGS lists only genuinely arg-taking flags |
| 65 | L0 | env-prefixed destructive command: `LC_ALL=C rm -rf .git` | blocked — every rule resolves the command name through one helper that skips ENV=value prefixes |
| 66 | L0 | branch literally named `commit`: `git push --no-verify origin commit` | blocked — the git subcommand is found by POSITION (after global flags), not by searching for a known word |
| 67 | L0 | `git stash push -f`, `git subtree push …` | allowed — push rules apply only to the real `git push` subcommand |
| 68 | L0 | `rm -R .git`, `rm --recursive .git` | blocked — every recursive spelling, not just `-rf` |
| 69 | L0 | `ALLOW_MAIN_COMMIT=yes`, `ALLOW_LEDGER_EDIT=y` | blocked — the hooks honor ANY non-empty value, so the guard matches the invariant, not `=1` |
| 70 | L0 | `gh pr review -a`, `gh pr edit --add-label=human-reviewed`, env-prefixed `gh pr merge` | blocked — gh rules resolve the command through the shared helper and cover short/attached flag forms |
| 71 | L0 | Write to a linked worktree's `.git/worktrees/<n>/.stop-gate-green` | blocked — state files match by basename anywhere, not by fixed path |
| 72 | L0 | multi-line Bash where the real command is on line 2+ (`echo hi\ntouch .commit-approved`) | blocked — newlines are command separators, split outside quotes before tokenizing |
| 73 | L0 | a quoted multi-line commit MESSAGE | allowed — line splitting respects quotes, so the message stays one token |
| 74 | L0 | backslash-newline line continuation before a flag or subcommand (`git commit \\<nl>-n`) | blocked — continuations are joined per POSIX before tokenizing |
| 75 | L0 | `git commit -m'no changes'` (attached value whose text contains 'n') | allowed — attached-value data is cut at the value flag |
| 76 | L0 | `rm -rf .venv` / `mv .venv /tmp` (the Stop gate no-ops without pytest) | blocked — the venv is gate infrastructure, not just its binary |
| 77 | L2 | `git push origin :main` | blocked — pre-push refuses main deletion (now covered by a test) |
| 78 | L1 | critical path touched, PR lacks its label (`schema.py` without `human-reviewed`) | `critical-paths` red; label added in the UI turns it green with NO new commit (automated half: 6 unit tests) |
| 79 | L1 | `critical_paths.txt` deleted or base sha unresolvable | gate fails CLOSED (never scans nothing and passes) |
| 80 | L3 | PR body still holds the `<paste:` placeholder | `evidence` red; a good PR body passes (the drafted `&&` form failed good PRs) |
| 81 | L0 | self-label via `gh issue edit <PR#> --add-label human-reviewed` or `gh api …/issues/N/labels` | blocked — GitHub treats PRs as issues, so gating only `gh pr` left the WT.7 gate self-servable |
| 82 | L1 | critical path inside a ~2,000-file PR | still detected — prefix matching is pure bash (the piped form died of SIGPIPE and failed OPEN on exactly the large PRs that need review most) |
| 83 | L1 | `web/scripts/app.js` (contains, but is not under, `scripts/`) | no label required — matching is anchored prefix, not substring |
| 84 | L3 | first CI run on a pyproject-only repo | `setup-python` pins `cache-dependency-path: pyproject.toml`, so the cache step cannot error before install |
| 85 | L0 | label via `gh api -X PATCH …/issues/N -f 'labels[]=human-reviewed'` or a GraphQL `addLabelsToLabelable` mutation | blocked — any mention of a review label in a gh command is decisive, plus mutating PATCH/POST on `/issues/N` or `/pulls/N` |
| 86 | L0 | `gh issue -R owner/repo edit <PR#> --add-label` (persistent flag between noun and action) | blocked — gh nouns/actions are parsed past flags and their values, not read at fixed positions |
| 87 | L0 | edit `scripts/check_critical_paths.sh` or `critical_paths.txt` (Bash or Edit) | blocked — the WT.7 gate machinery is protected like the other trust scripts; CI runs the PR's own copy, so an unprotected gate could be neutered by the PR it must gate |
| 88 | L0 | `gh api -XPATCH …/issues/N --input body.json`, `gh api graphql -F query=@f` | blocked — method matched textually (attached spellings) and any file-fed body on an issue/pull route counts as mutating |
| 89 | L0 | `gh pr list --label human-reviewed` (read-only) | allowed — reading label state is legitimate; only applying is human-only |
| 90 | L1 | rename a critical file (`git mv src/va/cli.py …`) | caught — the checker diffs `-M --name-status` and considers BOTH rename paths |
| 91 | L0 | `curl`/`wget` POST to `/issues/N/labels`, or `gh auth token` | blocked — speed bump only; see D9, a shared credential makes client-side blocking non-final |
| 92 | L3 | PR body with the EVIDENCE marker but no output under it | `evidence` red — the check requires real counts (`N passed`), not just the marker |
| 93 | L3 | unrelated PR merges to main while this PR is open | no spurious label demand — `critical-paths` checks out the PR HEAD, not the merge ref |
| 94 | L0 | `gh auth status --show-token` / `-t` | blocked — every spelling that PRINTS the credential, not just `gh auth token` |
| 95 | L1/L2 | `git add -A <dir>` sweeps another session's uncommitted work into a commit | caught by review: the committed test imported from an uncommitted module, which would have turned the branch's own required check red on a clean checkout |
| 96 | L1 | critical file with a non-ASCII name (`schéma.py`) or a space in it | caught — `core.quotepath=off` + TAB-delimited parsing (default C-quoting made it fail OPEN) |
| 97 | L0 | `gh alias set m 'pr merge'` then `gh m 14`; httpie's `https` binary | blocked — alias CREATION is refused (aliases are opaque to a static guard) and `https` joins the client list |
| 98 | L0 | `gh auth status --show-token=true` (attached value) | blocked — the attached-spelling class, closed for the third time |
| 99 | L0 | `gh api repos/o/r/issues/N/labels` with no method (a GET) | allowed — a route is not an action; only an explicit mutating method or a GraphQL mutation counts |
| 100 | L0 | `gh pr create --body "<filled PR template>"` (template text names both labels) | allowed — `create` cannot apply a label; blocking it broke a lifecycle-required action |
| 101 | L0 | `gh alias import aliases.yml` | blocked — alias installation from a FILE, sibling of `alias set` |
| 102 | L0 | `gh pr edit N --body "<filled template>"` | allowed — prose naming a label is not an application; the `--add-label`/`--approve` flag rule is what gates it |
| 103 | L0 | `gh api …/labels -f 'labels[]=x'` (no `-X`) | blocked — `gh api` sends POST automatically when fields are present |
| 104 | L0 | `curl …/graphql -d '{"query":"mutation{addLabelsToLabelable…"}'` | blocked — GraphQL mutation names now match on the HTTP-client path too |
| 105 | L0 | `gh pr review N --approve=true` (attached value) | blocked — attached spellings now covered for `--approve` as well as `--add-label` |

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

- **D9 — what the `human-reviewed` label actually proves. RESOLVED 2026-07-28 (user
  decision: option (c)).** Raised by PR 4's backstop: the agent session runs with the
  human's own GitHub credential, and `gh auth token` + `curl -X POST .../issues/N/labels`
  was demonstrated working. Blocking `gh` alone was never sufficient, and enumerating HTTP
  clients is the same losing game as enumerating shell spellings. **Decision: the label is
  a CEREMONY MARKER, not proof.** It records that the human intended to attest, and the
  guards (now covering `gh`, `curl`, `wget`, `http/httpie/xh`, and `gh auth token`) keep an
  agent from applying it *by accident or convenience* — a speed bump, explicitly not a
  guarantee. Docs say so plainly; no gate downstream treats it as cryptographic evidence.
  **Upgrade path if the claim ever needs to be real (option (a)):** issue a fine-grained
  PAT WITHOUT `issues:write` for agent sessions and keep the label-capable credential out
  of the agent environment — the only change that makes "human-only" true rather than
  customary. **Revisit when:** a second person (or an unattended/cron agent) works in this
  repo, or the bounded-review claim starts carrying weight beyond this one owner.
  What does NOT depend on the label: the offline suite, the fresh-context reviewer, branch
  protection, and every git-level gate. Only WT.7's bounded-review *claim* softens.

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
