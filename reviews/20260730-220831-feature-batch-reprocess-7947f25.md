# Agent review — approve

date: 2026-07-30T22:11:28.525886
range: origin/main..HEAD
branch: feature/batch-reprocess
findings: 1

- **minor** `src/va/cli.py:264` — A --video target whose ingest never completed (non-done status) yields an empty plan and the CLI reports it as 'already current' with rc=0, when it actually needs re-ingest.
  - scenario: An ingest crashes mid-way leaving status=pending; during a model switch the user runs `va reprocess --video <id> --dry-run`, reads 'video <id> is already current', and skips the `va reingest` the video actually requires — stale/absent role rows persist silently.

---

## Full review

Everything checks out statically. `Workspace` is pure path math, the XOR scope logic is correct on all four input combinations, `stale_report`/`lookup_video`/`ProvenanceStore.record` are used consistently with their signatures, documentation parity is met (CLAUDE.md command line, COORDINATION.md log entry, plan status update in the same change), and the plan's X-2 test items for this slice (`--dry-run` lists exactly the stale set, execution gated off, scope enforcement at both argparse and library level) all have tests. I could not run the test suite (the pytest command was not approved), so my review is static-only.

One issue survived verification:

**Minor — `src/va/cli.py:264` (the `if not plan:` branch of `_cmd_reprocess`):** when `--video` names a video whose ingest never completed (status `pending`/`error` after a crashed ingest), `stale_report` deliberately skips it as non-done, so the plan is empty and the CLI prints "video X **is already current**" with rc=0. The message is affirmatively wrong for that input: the video is not current, it needs a re-ingest — exactly the remedy `stale.py:47-50` says non-done videos require — and a user mid-model-switch would conclude the video is fine. `plan_reprocess`'s own docstring makes the same "empty ⇒ already current" equation. Safe path: when the `--video` scope resolves, check `target.ingest_status`; if it isn't `done`, say so ("not fully ingested — run `va reingest`") instead of "already current". The library already fetches `target`, so this is a two-line branch, plus a test with a non-done catalog row.

Things I checked that dissolved: the `"ocr" in cap.out` assertion in `test_cli_execution_is_gated_off` is not gamed by earlier output (library `ingest()` has no prints); the `_active_config_line` refactor of `va stale` is behavior-preserving and that header was untested before this change too; the `all_stale == bool(video)` XOR handles all four combinations correctly; the refusal path can't be a silent no-op (plan printed to stdout, refusal to stderr, rc=1); the provisional `need_agent_review:` subject is exempt from the commit-message-clarity rule.

Verdict: **approve** — the single finding is minor (misleading message on an edge input, no data risk).

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/cli.py", "line": 264, "issue": "A --video target whose ingest never completed (non-done status) yields an empty plan and the CLI reports it as 'already current' with rc=0, when it actually needs re-ingest.", "scenario": "An ingest crashes mid-way leaving status=pending; during a model switch the user runs `va reprocess --video <id> --dry-run`, reads 'video <id> is already current', and skips the `va reingest` the video actually requires — stale/absent role rows persist silently."}]}
```
