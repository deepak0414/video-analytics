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
