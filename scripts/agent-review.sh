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

# Human-only waiver (bash_guard blocks agents from setting this; every use is
# recorded so waived pushes stay visible in the audit trail).
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
raw=$(timeout 480 claude -p "$prompt" \
  --allowedTools "Read,Grep,Glob,Bash(git diff *),Bash(git log *),Bash(git show *),Bash(git blame *),Bash(git status *)" \
  --output-format json --max-turns 40 2>>"$ledger.err") || {
    echo "agent-review: headless run failed/timed out — treating as BLOCK (fail-closed). See $ledger.err" >&2
    exit 1
  }
rm -f "$ledger.err"

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
