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
