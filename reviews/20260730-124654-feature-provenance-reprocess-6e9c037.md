# Agent review — approve

date: 2026-07-30T12:50:39.198658
range: origin/main..HEAD
branch: feature/provenance-reprocess
findings: 1

- **minor** `src/va/pipeline/text_index.py:79` — Shard tag is derived from load_config() rather than from the `embedder` actually used, so a caller passing the injected `embedder` param (e.g. the planned RPRC-1 reprocess/backfill path) writes a tag that misdescribes the vectors.
  - scenario: A future backfill calls index_text(..., embedder=HFTextEmbedder('e5-large')) while roles.yaml still says BAAI/bge-m3; the shard is stamped {embedder: 'BAAI/bge-m3'} over e5-large vectors, and the TAG-3 query-time guard then accepts the shard as current and silently mixes vector spaces — the exact failure the tag exists to prevent. Fix: derive the tag from the embedder instance (model-id attribute on the Protocol), or stamp from config only when embedder is None and document that constraint.

---

## Full review

Review complete. This range contains two commits: a plan doc (`provenance-reprocess-plan.md`, docs-only, plain subject — allowed) and the TAG-1+2 implementation under a provisional `need_agent_review:` subject (exempt from the finalized-message rule).

**What I verified and found sound:**

- **Format compatibility both directions.** New code reading old shards: `_load` guards `"meta" in npz.files` → `meta is None` (`numpy_flat.py:39`), matching the COORDINATION.md claim. Old code reading new shards: the pre-change loader indexes `["vectors"]` and ignores the extra entry, so a web-agent session on an older checkout doesn't break. The `meta` entry is a 0-d unicode array — loads fine under `np.load`'s default `allow_pickle=False`.
- **No reader depends on the tag yet** — `ShardedVectorStore` and its mtime-keyed cache are untouched, so "search is unchanged" is accurate; the mtime cache also self-invalidates when a shard gets re-stamped.
- **Stale-shard mixing via `reingest`** dissolves: `remove_video` deletes the video dir before re-ingest, and `index_text` explicitly unlinks both shard files before rebuilding. The crash-resume corner (leftover visual shard + config switch) fails hard at `add()`'s dim check before a misleading tag could persist, since no two current visual backends share a dim (64 vs 1152).
- **`embedder_id` fallback** matches `get_text_embedder`'s KeyError→hash behavior exactly; for `visual_embedder` the unguarded `get_visual_embedder` at `ingest.py:222` would abort long before tagging, so the paths can't disagree in-process.
- **Empty-shard persist** works: `_vecs` stays `None`, `dim` property returns `None`, tag round-trips as `{"embedder": ..., "dim": null}` — and the test covers it.
- **Docs/coordination parity:** shard-format change logged in COORDINATION.md with the action for both agents; plan status updated; no new env vars, CLI flags, or config keys. CLAUDE.md's vector-space-trap note is explicitly deferred to TAG-3 in the plan (X-1), which is consistent.
- **Tests** (I could not run pytest in this session — the command requires an approval unavailable here — so this is static verification): all helpers the new tests use exist with matching signatures (`Catalog.get`, `Workspace.video_dir`, `write_color_video`), the assertions would fail on pre-change code, and the three unit + two integration tests cover round-trip, untagged-legacy, empty-shard, and both ingest stamp points on the stub path. Since the tag content is a config-derived string, stub-path coverage generalizes to the siglip/bge config combination.

**One finding survives**, a latent contract seam rather than a live bug: `index_text` accepts an injected `embedder` (forwarded by `backfill_text_index`), but the tag at `text_index.py:79` is derived from `load_config()` — not from the embedder that actually produced the vectors. No current caller injects one, but the moment a backfill or reprocess path does (RPRC-1 is exactly such a caller), the shard gets stamped with the *config's* model id over *another model's* vectors — a lying tag that the upcoming TAG-3 guard will trust, recreating the silent space-mix this feature exists to prevent. Safe path: derive the tag from the embedder instance (expose a model-id attribute on the embedder Protocols) or, minimally, stamp from config only when `embedder is None` and document the constraint on the param.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/text_index.py", "line": 79, "issue": "Shard tag is derived from load_config() rather than from the `embedder` actually used, so a caller passing the injected `embedder` param (e.g. the planned RPRC-1 reprocess/backfill path) writes a tag that misdescribes the vectors.", "scenario": "A future backfill calls index_text(..., embedder=HFTextEmbedder('e5-large')) while roles.yaml still says BAAI/bge-m3; the shard is stamped {embedder: 'BAAI/bge-m3'} over e5-large vectors, and the TAG-3 query-time guard then accepts the shard as current and silently mixes vector spaces — the exact failure the tag exists to prevent. Fix: derive the tag from the embedder instance (model-id attribute on the Protocol), or stamp from config only when embedder is None and document that constraint."}]}
```
