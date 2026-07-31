# Agent review — approve

date: 2026-07-31T11:08:22.468287
range: origin/main..HEAD
branch: feature/batch-reprocess
findings: 1

- **minor** `tests/test_reprocess.py:5` — Stale module docstring claims 'the CLI va reprocess can only PLAN today — execution (RPRC-1) is gated off', contradicted by the execution tests added in the same file for RPRC-1a.
  - scenario: A future session reads the test-file header to understand the command's capabilities, concludes execution is unimplemented, and re-plans or re-implements RPRC-1 work that already shipped. Safe path: update the docstring to the RPRC-1a state (text_embedder executes in place; other roles are skipped with a `va reingest` pointer) during the finalize amend.

---

## Full review

Review complete. I was unable to run the test suite (pytest execution was not approved in this session, consistent with the prior review rounds on this branch), so this is static analysis over `origin/main..HEAD` (`fa489c3` finalized RPRC-3a selection front-end + `bce45f6` provisional RPRC-1a executor).

**What I verified, and what dissolved:**

- **Both findings from the previous review round (340ef77) are fixed in HEAD.** `index_text` now embeds *before* unlinking the old shard (`text_index.py:79-88`), with a dedicated regression test (`test_failed_text_rebuild_preserves_the_old_shard`) that injects a raising embedder and asserts the old `.npz` bytes survive; and the execute test now asserts the restamped row preserves fps=2.0 (`test_execute_reprocesses_text_embedder_and_clears_stale`).
- **The safety ordering holds:** rows/shard first, provenance second; a raising reprocessor lands in `failed` with no restamp (stays stale, retryable); `backfill_text_index` returning `None` (video removed between plan and execute) is converted to a raise so a vanished video is never restamped as a 0-row success — all three paths have tests, including the no-resurrection assertion.
- **Config-pinning direction is safe:** the batch pins one `load_config()` for restamps while the reprocessor's embedder resolves via the registry; a mid-batch config edit yields a false stale (needless re-run), never a missed stale. The pinning has a real test (patches `rp.load_config`; works because `execute_reprocess` imports `role_fingerprint` at call time).
- **Scope enforcement is double-guarded** (argparse mutually-exclusive group + `all_stale == bool(video)` in the library), with tests at both layers; the non-done `--video` target correctly raises "needs `va reingest`" instead of reading as "already current".
- **Ingest is unaffected as a best-effort role:** `index_text` is still wrapped in try/except in `ingest.py:344-350`; the embed-before-unlink change only moves *where* inside the try a failure occurs.
- **Contracts/docs parity:** `ProvenanceStore.record/get`, `role_fingerprint(role, cfg)`, `lookup_video`, `stale_report`'s `recorded_fps` all match usage; CLAUDE.md command line, two COORDINATION.md entries (including the promised write-path heads-up for the web agent), and plan-status updates land in the same change. All three real config dirs define `text_embedder`, so the wired role exists in every combination; the config-basis header prints on both plan and execute paths, addressing the gotcha-#2 foot-gun. Nothing reads `role_provenance.run_id` today, so the restamp writing NULL there (no trace run active in the CLI path) has no consumer to break.
- **Test integrity:** no tests deleted or weakened anywhere in the range; the new file covers selection scoping, both scope-validation failure modes, execution success/skip/failure, the removed-video race, config pinning, shard preservation, fps carry-through, and CLI plan/execute/dry-run.

**One minor finding survived:** the module docstring of `tests/test_reprocess.py` (lines 1–6) still says "The CLI `va reprocess` can only PLAN today — execution (RPRC-1) is gated off," which was true for the first commit but is contradicted by the executor tests added lower in the same file. The next session reading the file header will mis-model what the command does. Safe path: update the docstring to the RPRC-1a state (executes `text_embedder`, skips the rest with a reingest pointer) in the finalize amend.

Verdict: **approve** — no critical or major findings.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "tests/test_reprocess.py", "line": 5, "issue": "Stale module docstring claims 'the CLI va reprocess can only PLAN today — execution (RPRC-1) is gated off', contradicted by the execution tests added in the same file for RPRC-1a.", "scenario": "A future session reads the test-file header to understand the command's capabilities, concludes execution is unimplemented, and re-plans or re-implements RPRC-1 work that already shipped. Safe path: update the docstring to the RPRC-1a state (text_embedder executes in place; other roles are skipped with a `va reingest` pointer) during the finalize amend."}]}
```
