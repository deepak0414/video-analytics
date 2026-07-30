# Agent review — approve

date: 2026-07-30T12:58:08.424963
range: origin/main..HEAD
branch: feature/provenance-reprocess
findings: 0


---

## Full review

Review complete. The range contains two commits: the plan doc (`provenance-reprocess-plan.md`, docs-only, plain subject — permitted) and the TAG-1/TAG-2 implementation under a provisional `need_agent_review:` subject (exempt from the finalized-message rule). The full offline suite is green on this checkout: **444 passed, 2 skipped**, including the five new tests in `tests/test_shard_tagging.py`.

**What I verified and found sound:**

- **Prior finding is fixed.** The earlier review of this branch (`reviews/20260730-124654-...-6e9c037.md`) flagged that `index_text` stamped the config's model id over an injected embedder's vectors. The current HEAD resolves it exactly as recommended: config-derived tag only when `embedder is None`, otherwise the embedder's own `model_id` or an honest `"unknown"` that the future TAG-3 guard will skip (`text_index.py:73-77`), with a regression test covering both injected cases.
- **Format compatibility both directions.** Old shards load with `meta is None` (`numpy_flat.py:39-40`); old code reading new shards indexes only `["vectors"]` and ignores the extra entry, so an older web-agent checkout is unaffected. The `meta` entry is a 0-d unicode array — loads under numpy's default `allow_pickle=False`. This matches the COORDINATION.md entry, which correctly names the change and both agents' action.
- **Tag consistency.** In ingest, both `get_visual_embedder()` and `embedder_id("visual_embedder")` resolve from the same in-process config, so the stamp can't misdescribe the vectors on the write path; `embedder_id`'s KeyError→`"hash"` fallback mirrors `get_text_embedder`'s exactly. `set_meta` is called before the single `persist()` and after the critical embed loop, so a failed ingest never persists a half-stamped shard.
- **No reader regression.** `ShardedVectorStore` and its mtime-keyed cache are untouched; a re-stamped shard gets a new mtime and self-invalidates. `migrate.py` writes legacy-split shards untagged — the honest state TAG-4's dim-only backfill is planned for.
- **Plan/test/docs parity.** The commit matches the plan's TAG-1+2 "done" claim; tests cover round-trip, untagged-legacy, empty-shard (`dim: None`), both ingest stamp points, and the injected-embedder branch, and they would fail on pre-change code (meta would be `None`). No new env vars, CLI flags, or config keys; the CLAUDE.md vector-trap note is explicitly deferred to TAG-3 in the plan (X-1). Combination coverage is fine: the tag is a config-derived string, so the stub-path tests generalize to the siglip/HF-text configs mechanically.

Nothing new survived verification.

```json
{"verdict": "approve", "findings": []}
```
