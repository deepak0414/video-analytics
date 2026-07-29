#!/usr/bin/env bash
# Fresh-context adversarial review via headless Claude (workflow-trust-plan.md WT.4).
# Usage: scripts/agent-review.sh [--print-prompt] <git-range>|--worktree
#   <git-range>     pre-push backstop mode
#   --worktree      everything vs origin/main
#   --print-prompt  emit the assembled prompt and exit (no claude call) — the
#                   drift test for the single-sourced rubric (matrix row 106)
# Exit 0 = approved (or human-waived); 1 = changes requested / review failed
# (fail-closed). Findings + full review text land in a reviews/ ledger entry.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
print_only=0
if [ "${1:-}" = "--print-prompt" ]; then print_only=1; shift; fi
mode="${1:?usage: agent-review.sh [--print-prompt] <git-range>|--worktree}"
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

# Human-only waiver — checked AFTER --print-prompt below, so print mode never
# writes a spurious WAIVED ledger when the env var lingers in a shell (review
# finding, WT.11 round 1); every real-run use is recorded in the audit trail.
if [ "$print_only" = 0 ] && [ "${AGENT_REVIEW:-}" = "skip" ]; then
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

# The rubric is SINGLE-SOURCED from the reviewer agent file (WT.11): the awk
# strips the frontmatter (everything through the second `---`). Fail CLOSED if
# the file moves or the extraction breaks — an empty rubric must never silently
# produce a lenient review.
rubric=$(awk 'f{print} /^---$/{c++; if (c==2) f=1}' .claude/agents/code-reviewer.md)
if [ -z "$rubric" ]; then
  echo "agent-review: rubric extraction from .claude/agents/code-reviewer.md came back EMPTY — refusing to review with no rubric (fail-closed)." >&2
  exit 1
fi

prompt="${rubric}

For THIS review, override the default scope. Review ONLY ${scope}

End your reply with EXACTLY one fenced json block:
\`\`\`json
{\"verdict\": \"approve|request_changes\", \"findings\": [{\"severity\": \"...\",
\"file\": \"...\", \"line\": 0, \"issue\": \"...\", \"scenario\": \"...\"}]}
\`\`\`"

if [ "$print_only" = 1 ]; then
  printf '%s\n' "$prompt"
  exit 0
fi

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
