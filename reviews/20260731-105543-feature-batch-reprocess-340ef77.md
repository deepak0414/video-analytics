# Agent review — approve

date: 2026-07-31T10:59:48.963713
range: origin/main..HEAD
branch: feature/batch-reprocess
findings: 2

- **minor** `src/va/pipeline/reprocess.py:78` — A reprocessor failure during embedding destroys the video's existing text_vectors shard (index_text unlinks before embedding), so 'stays stale, safe to retry' understates the degradation — text search returns nothing for that video until a retry succeeds.
  - scenario: During a model switch, `va reprocess --all-stale` under run-siglip hits a CUDA OOM inside embed() on a long video: the old shard was already deleted, provenance is correctly not restamped, but the video's text search is silently empty until the user fixes the config and re-runs. Safe path: embed first (or build to a temp path and rename) so a failed rebuild leaves the prior shard intact.
- **minor** `tests/test_reprocess.py:120` — The executor's stated invariant that the restamp preserves the recorded ingest fps (docstring + COORDINATION.md) has no test — no assertion reads the restamped provenance row's fps.
  - scenario: A refactor simplifies the restamp to `fps=None`; every test stays green (stale_report filters NULL fps), and the recorded frame density the recorded_fps plumbing exists to preserve is silently lost on each reprocessed role. Safe path: in test_execute_reprocesses_text_embedder_and_clears_stale, assert the restamped text_embedder row still carries the fps _make_stale stamped (2.0).

---

## Full review

Review complete — static analysis only (the pytest command was not approved in this session, consistent with the prior three review rounds on this branch).

**Scope reviewed:** two commits — `fa489c3` (finalized: the RPRC-3a dry-run selection front-end, already through three review rounds whose findings are all fixed in it) and `340ef77` (provisional `need_agent_review:`, exempt from the finalized-message rule: the RPRC-1a executor wired for `text_embedder`).

**What I verified, and what dissolved:**

- **Ordering invariant (rows first, provenance second):** `execute_reprocess` restamps only after the reprocessor returns, a raising reprocessor goes to `failed` with no restamp, and the removed-video race is handled — `backfill_text_index` returning `None` is converted to a raise so a vanished video can never be restamped as a 0-row "success" (`reprocess.py:79-84`), with a dedicated test including the no-resurrection assertion.
- **Config pinning direction is safe:** the batch pins one `load_config()` for restamps, while the reprocessor's embedder resolves via the registry. A mid-batch config edit therefore makes the *recorded* fingerprint the old one — a false stale (needless re-run), never a missed stale. The code comment claims exactly this and it holds.
- **All library contracts match:** `backfill_text_index(workdir, ident)` accepts a UUID string and returns `Optional[int]`; `ProvenanceStore.record(..., fps=, run_id=, row_count=)` and `get(vid, role)` signatures; `role_fingerprint(role, cfg)`; `current_run_id()` exists; `PROVENANCE_ROLES` includes `text_embedder`; `stale_report(role=X)` returns `stale_roles` limited to `[X]`, so a role-scoped plan can't leak other roles into the executor.
- **The monkeypatch tests are sound:** `execute_reprocess` imports `role_fingerprint` at call time, so patching the module attribute works; the pinned-config test genuinely proves one snapshot per batch.
- **Docs/coordination parity:** CLAUDE.md command line, two COORDINATION.md entries (including the write-path heads-up for the web agent promised by the earlier read-helper entry), and plan-status updates all land in the same change. All three real config dirs define `text_embedder` (bge-m3), so the wired role exists in every non-stub combination; the config-basis header prints on both plan and execute.
- **Dependency staleness (text index rebuilt from possibly-stale captions) is not silently dropped** — RPRC-2 is explicitly deferred and named as next in both the plan and COORDINATION.md, and the whole-video `va reingest` fallback heals it.

**Two findings survived, both minor:**

1. **`src/va/pipeline/reprocess.py:78`** — the invariant "a crash stays stale (safe to retry)" is weaker than stated: `index_text` deletes the existing `text_vectors` shard *before* embedding (`text_index.py:80-86`), so an inference-time failure (CUDA OOM on a large batch is the realistic case on the real config — model *load* failures happen earlier, before the unlink) leaves the video with **no** text shard, not its old one. Text search for that video silently returns nothing until a retry succeeds; the data is reconstructible from `catalog.db` rows, the role stays stale, and the CLI reports FAILED with rc=1, which is why this is minor. Safe path: embed before unlinking, or write to a temp path and rename, so a failed reprocess leaves the old shard intact.
2. **`tests/test_reprocess.py:120`** — "the restamp preserves the recorded ingest fps" is a stated invariant (docstring + COORDINATION.md entry) with zero coverage: no test reads the restamped `text_embedder` row and asserts its fps survived. A regression that drops it to NULL passes every test; the loss then degrades the fps-preservation remedy the whole `recorded_fps` plumbing exists for (only masked today because `stale_report` filters NULLs). Safe path: one assertion in the existing execute test — `_make_stale` already stamps fps=2.0, so assert the restamped row still carries it.

Nothing rose to major: no contract or schema change beyond what COORDINATION.md logs, no hardcoded content, no weakened tests, and the plan's RPRC-3 "resumable + per-video atomic + whole-video fallback" line is implemented as specified (the RPRC-1 `model_id` sub-item is untriggered here since the reprocess path never injects an embedder, and the plan logs the slice split).

Verdict: **approve**.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/reprocess.py", "line": 78, "issue": "A reprocessor failure during embedding destroys the video's existing text_vectors shard (index_text unlinks before embedding), so 'stays stale, safe to retry' understates the degradation — text search returns nothing for that video until a retry succeeds.", "scenario": "During a model switch, `va reprocess --all-stale` under run-siglip hits a CUDA OOM inside embed() on a long video: the old shard was already deleted, provenance is correctly not restamped, but the video's text search is silently empty until the user fixes the config and re-runs. Safe path: embed first (or build to a temp path and rename) so a failed rebuild leaves the prior shard intact."}, {"severity": "minor", "file": "tests/test_reprocess.py", "line": 120, "issue": "The executor's stated invariant that the restamp preserves the recorded ingest fps (docstring + COORDINATION.md) has no test — no assertion reads the restamped provenance row's fps.", "scenario": "A refactor simplifies the restamp to `fps=None`; every test stays green (stale_report filters NULL fps), and the recorded frame density the recorded_fps plumbing exists to preserve is silently lost on each reprocessed role. Safe path: in test_execute_reprocesses_text_embedder_and_clears_stale, assert the restamped text_embedder row still carries the fps _make_stale stamped (2.0)."}]}
```
