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
