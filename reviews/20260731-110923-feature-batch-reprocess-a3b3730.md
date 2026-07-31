# Agent review — approve

date: 2026-07-31T11:13:11.629739
range: origin/main..HEAD
branch: feature/batch-reprocess
findings: 0


---

## Full review

Review complete — static-only (the pytest command was not approved in this session, same as the three prior rounds recorded in `reviews/`).

**Scope reviewed:** two commits. `fa489c3` (finalized, plain subject — a self-sufficient description, so it satisfies the commit-clarity rule) added the RPRC-3a dry-run selection front-end; `a3b3730` (provisional `need_agent_review:` — exempt) wires the RPRC-1a executor for `text_embedder`, plus the `index_text` embed-before-unlink reorder, CLI execution path, 6 new tests, and doc entries in CLAUDE.md, COORDINATION.md, and the plan.

**What I verified, and what dissolved:**

- **The safety invariant (rows first, provenance second)** holds on every path: a reprocessor that raises is routed to `failed` with no restamp (tested); an exception in the restamp itself would abort the batch loudly but still leaves the role stale — safe to retry, consistent with how other CLI commands surface store errors.
- **The removed-video race** is genuinely closed: `backfill_text_index` returns `None` on an unresolvable ident, and `_reprocess_text_embedder` converts that to a raise rather than a 0-row "success" — so a purged provenance row cannot be resurrected as current. The test covers exactly this.
- **The `index_text` reorder is a real regression test**, not decoration: on the old code order (unlink before embed) the `_BoomEmbedder` failure would leave `shard.exists()` false, so `test_failed_text_rebuild_preserves_the_old_shard` fails against the pre-change code — it satisfies the repo's "must reproduce the original failure" lesson. Success-path behavior is byte-identical, so no combination (stub/siglip, ingest/reingest/backfill) changes behavior on the happy path.
- **Pinned-config restamp**: the batch pins one `load_config()` for fingerprints while `get_text_embedder()` inside the reprocessor does its own fresh load — a mid-batch config edit therefore stamps the *old* fingerprint over rows built by the *new* embedder, which reads as stale and re-runs: the safe false-stale direction, matching the comment's claim. The test's monkeypatch topology (module-global `rp.load_config`, call-time `from va.provenance import role_fingerprint`) is correct, so it actually tests what it claims.
- **fps preservation** is proven, not incidental: the test ingests at fps=1.0 but pokes the stale row to fps=2.0 and asserts 2.0 survives the restamp — so it demonstrably reads the prior row, not the ingest arg. For a never-stamped stale role `prev` is empty and fps records NULL; harmless for `text_embedder` (its output is fps-independent) and it cannot corrupt `stale_report`'s consistency check (NULLs are filtered).
- **API contracts** all match: `ProvenanceStore.record/get` (upsert keyed `(video_id, role)`; `_COLS` includes `fps`), `current_run_id()` (None-safe outside a run), `lookup_video`, `remove_video`, `Workspace.video_dir(create=True)`, `role_fingerprint(role, cfg)`. `connect()` uses WAL + a generous busy timeout, so the COORDINATION-flagged concurrent-`va serve` scenario degrades to waiting, not corruption.
- **Test integrity:** `test_cli_execution_is_gated_off` was removed, but the behavior it guarded was deliberately replaced by RPRC-1a execution per the plan, and its live half (dry-run mutates nothing) survives as `test_cli_dry_run_mutates_nothing`. No weakening.
- **Plan conformance & docs:** the D5 scope cap (only `text_embedder` wired) is recorded in the plan status with RPRC-1b/c named next; the D6 `observations` purge is explicitly deferred to the caption reprocessor and is genuinely irrelevant to `text_embedder` (the cache keys fold captioner + reasoner fingerprints only). CLAUDE.md's command line, COORDINATION.md's write-path entry (including the web-agent heads-up about in-place shard rebuilds), and the plan all landed in the same change. No new env vars or undocumented flags.

Candidates I chased that dissolved: the skip-pointer omitting `--fps` when fps is unknown (the plan listing already prints `fps=unknown`, and an unknown fps cannot be preserved anyway); rc=2 vs rc=1 semantics (consistent: 2 = bad invocation, 1 = execution failures); the `"ocr: skipped"` output assertion (not satisfiable by any other line).

Nothing rose to a finding. Verdict: **approve**.

```json
{"verdict": "approve", "findings": []}
```
