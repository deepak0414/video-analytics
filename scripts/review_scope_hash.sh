#!/usr/bin/env bash
# One canonical definition of "the content the reviewer approved": everything not
# yet on origin/main — committed-but-unpushed + uncommitted tracked changes +
# untracked files. Changes iff the reviewable content changes.
# reviews/ is EXCLUDED: ledgers are artifacts of the review itself, so they ride
# inside the finalize amend without invalidating the approval hash.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
{ git diff origin/main -- . ':(exclude)reviews' 2>/dev/null
  git ls-files -z --others --exclude-standard -- . ':(exclude)reviews' \
    | sort -z | xargs -0 -r sha256sum 2>/dev/null
} | sha256sum | cut -d' ' -f1
