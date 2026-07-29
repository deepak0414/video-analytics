# Agent review — approve

date: 2026-07-29T14:53:06.174371
range: origin/main..HEAD
branch: trust/role-instructions
findings: 0


---

## Full review

I was unable to execute the test suite (command approval not granted in this session), but I verified the change statically end-to-end — and notably, the prompt this very review is running under is the live output of the new single-sourced assembly (rubric items 1–8 + scope override + JSON appendix), which is direct evidence the extraction path works.

## Review of `origin/main..HEAD` (WT.11 role instructions, 2 commits)

**What the change does:** moves the reviewer rubric to a single source (`.claude/agents/code-reviewer.md`), makes `scripts/agent-review.sh` assemble its headless prompt from that file with a fail-closed guard and a `--print-prompt` drift-test mode, adds the `/task-commit` committer procedure, slims CLAUDE.md's lifecycle section to pointers, logs the change in COORDINATION.md, and adds two sandbox tests plus the WT.11 plan section.

**What I checked and confirmed clean:**

- **Correctness of the extraction** (`scripts/agent-review.sh:51`): the awk prints only lines after the second `---`; the agent file's frontmatter is exactly two delimiter lines; an empty result (missing/moved/frontmatter-only file) exits 1 before any review runs. Fail direction is closed, not lenient — including the CRLF edge case, which would fail closed rather than produce a lenient review. Verified against the actual file content.
- **Waiver ordering fix** (`scripts/agent-review.sh:34`): print mode returns before the `AGENT_REVIEW=skip` branch, so a lingering waiver env var can no longer write a spurious WAIVED ledger — and the new drift test explicitly regresses this (sets `AGENT_REVIEW=skip`, asserts no new ledger), so it's real coverage of the round-1 finding, not decoration.
- **Test integrity:** no tests deleted or weakened. The fixture change (`tests/test_trust_hooks.py:146`) copies the agent file into the sandbox for parity; `mkdir(parents=True)` without `exist_ok` is safe because the sandbox is a fresh clone where `.claude` cannot pre-exist. `test_missing_rubric_fails_closed` writes a frontmatter-only file, which correctly yields an empty rubric (counter reaches 1, never 2) and asserts the "fail-closed" stderr message that the script actually emits.
- **Documentation parity:** the new `--print-prompt` flag is documented in the script header and the RI.1 plan card; `/task-commit` is documented in CLAUDE.md and the COORDINATION.md log entry; the rubric relocation is recorded in all three places. I grepped for stale references to the old rubric-in-script design and found none — `workflow-trust-plan.md:1548-1550` ("convenience twin / identical rubric") is now *more* accurate than before, not stale.
- **Contract handling:** the cross-agent procedural change (both agents must now use `/task-commit`; rubric moved) is logged in COORDINATION.md's append-only log, as required.
- **Plan conformance:** RI.1's "Done when" (drift test, row 106) is met by `test_reviewer_rubric_is_single_sourced_from_agent_file`. RI.3's size constraint holds (CLAUDE.md is 322 lines on both main and this branch), with the deferred full consolidation honestly recorded as-built. Rows 107/108 and RI.6 are marked PENDING by design — they can only be observed live, and this branch's own commit is mid-lifecycle (provisional subject), which is the RI.6 demonstration in progress. Not gaps.
- **Commit messages:** HEAD's `need_agent_review:` subject is exempt (provisional); `2780ec2` is docs-only (touches only `workflow-trust-plan.md`), so its plain subject is permitted, and its description is self-sufficient.
- **Combination coverage:** the change touches only the trust layer — no role, backend, config-dir, or profile behavior — so the offline trust-hook tests are the complete affected surface.

**Suspicions that dissolved:** print mode still runs `mkdir -p reviews` (no-op, no file written); the rubric body passing through double-quoted shell expansion (variable expansion, never re-evaluated — backticks in the markdown are inert); pipefail interaction with the `git diff --stat` substitutions (no `-e`, assignment failure doesn't abort).

No findings.

```json
{"verdict": "approve", "findings": []}
```
