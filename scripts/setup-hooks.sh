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
