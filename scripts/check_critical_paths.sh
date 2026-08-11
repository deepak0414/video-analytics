#!/usr/bin/env bash
# Bounded human review (workflow-trust-plan.md WT.7).
# NB WT.7 embeds a byte-identical MIRROR of this file. Editing here without
# re-copying into that block fails test_wt7_embedded_mirror_matches_the_shipped_checker.
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
# git's OWN stderr goes to a file, never into $raw via 2>&1: a warning on the
# SUCCESS path (e.g. "inexact rename detection was skipped due to too many
# files") would otherwise be parsed as a changed path and could mask a real one.
# It is REPLAYED either way, because discarding git's own words is what turned
# three CI failures into guesswork. Report what git said; presume nothing.
# On success the replay goes to stderr purely so it reaches the log WITHOUT
# entering $raw: a warning line has no TAB, so `cut -f2-` would pass it through
# whole and it would be prefix-matched as if it were a path — which can only ADD
# a spurious match, never hide a real one. (NB a skipped rename detection is not
# itself a gate hole: --name-status still reports the undetected rename as
# D <old> + A <new>, so the old path is scanned either way. -M only merges the
# pair into one R record.)
# mktemp's status is checked: unchecked, a full or read-only /tmp makes the
# redirect below fail before git ever runs, and the || branch would then blame
# the base sha for a disk problem — "git said:" followed by nothing. That is the
# misattribution this whole change exists to remove; do not reintroduce it here.
_cp_err=$(mktemp) || {
  echo "FAIL: cannot create a temp file for git's stderr (is /tmp full or read-only?)"
  exit 1; }
trap 'rm -f "$_cp_err"' EXIT
raw=$(git -c core.quotepath=off diff -M --name-status "$base"...HEAD 2>"$_cp_err") || {
  rc=$?      # FIRST statement — any command here would overwrite it.
  echo "FAIL: cannot diff against '$base' — git exited $rc"
  if [ -s "$_cp_err" ]; then
    echo "       git said:"
    sed 's/^/       /' "$_cp_err"
  else
    # A silent death is itself the diagnosis — but only a status >128 means a
    # signal. Decoding one unconditionally would print "KILLED by signal 0" for,
    # say, a disk-full exit 128: a fabricated cause under an empty message, the
    # very thing this change removes.
    if [ "$rc" -gt 128 ]; then
      echo "       git printed nothing and was KILLED by signal $((rc - 128))"
      echo "       (137 = the OOM killer, 141 = SIGPIPE)."
    else
      echo "       git printed nothing and exited $rc."
    fi
  fi
  # The base-sha explanation is printed ONLY when git's own words implicate the
  # revision. Printing it unconditionally is how this gate misattributed three
  # failures: a hypothesis under a silent or unrelated error reads as a finding.
  if grep -qE "unknown revision|bad object|Invalid symmetric difference|ambiguous argument|no merge base" \
       "$_cp_err" 2>/dev/null; then
    echo "       (the checker needs '$base' to exist in this checkout: a shallow"
    echo "        clone or a force-pushed base branch removes it)"
  fi
  exit 1; }
# if-form, not `[ -s … ] && …`: the && form yields exit 1 when the file is empty,
# which would become the script's status if this ever ended up as the last command.
if [ -s "$_cp_err" ]; then sed 's/^/git: /' "$_cp_err" >&2; fi
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
  echo "in the GitHub UI after reading the diff. Re-runs on 'labeled' — no new"
  echo "commit needed. NB the gh/curl guards that stop an agent applying it are a"
  echo "speed bump against ACCIDENT, not a guarantee (D9): agent sessions share"
  echo "the human's credential, so this label is worth only the reading behind it."
fi
exit "$missing"
