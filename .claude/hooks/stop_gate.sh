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
