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
