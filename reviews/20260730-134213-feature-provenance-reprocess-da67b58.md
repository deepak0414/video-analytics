# Agent review — approve

date: 2026-07-30T13:45:55.223850
range: origin/main..HEAD
branch: feature/provenance-reprocess
findings: 2

- **minor** `src/va/pipeline/retrieval.py:224` — count(expect_embedder=...) checks untagged shards with qdim=None while search checks them against the real query dim, so an untagged dim-mismatched text shard counts as 'usable' but is then skipped at search — the semantic branch is chosen, returns zero hits, and the lexical fallback + evidence note never fire.
  - scenario: A pre-tagging text_vectors.npz built under one config dir (hash-dim) is queried under the other (bge-dim): usable > 0 picks the semantic branch, search_text skips the shard, and the whole text tier silently drops with only a stderr log line — the exact silent-drop this change's comment claims to prevent. Fix: pass the current embedder's dim into count, or run the lexical fallback when semantic search yields nothing while shards were skipped.
- **minor** `src/va/storage/vector/sharded.py:34` — _compatible hard-rejects any tagged shard whose embedder string differs from the current one, but the same range's plan (D4/TAG-4) specifies backfilling legacy shards as {model: unspecified, dim} with dim-only matching — the shipped guard would evict dim-matching backfilled shards from search.
  - scenario: TAG-4 retags a legacy dim-64 shard as {embedder: 'unspecified', dim: 64}; a stub-config query (embedder 'hash', dim 64) that admitted the shard while untagged now skips it — backfill regresses search coverage. Fix: admit 'unspecified' tags on dim match now, or record in provenance-reprocess-plan.md that TAG-4 must extend _compatible.

---

## Full review

Review complete. I could not run pytest in this session (the command requires an approval unavailable here), so the test assessment is static verification — I confirmed every helper the new tests use exists with matching signatures, and that the assertions would fail on pre-change code.

**Scope reviewed:** three commits — the plan doc (docs-only, plain subject, permitted), the finalized TAG-1/TAG-2 tagging commit (`feat(storage): tag vector shards…` — self-sufficient description, fine), and the provisional `need_agent_review:` TAG-3 guard commit (exempt from the finalized-message rule).

**What I verified and found sound:**

- **The guard logic itself** (`sharded.py:_compatible` + `search`): tagged shards must match embedder and dim; untagged legacy shards are admitted only on dim match (the acknowledged D4 gap); empty tagged shards are admitted on embedder match with a regression test preventing a perpetual false "reprocess" warning. `qdim` extraction handles both the 1-D visual query and the (1, D) text query correctly via `atleast_2d`.
- **All read paths are guarded.** Every in-repo `ShardedVectorStore.search` caller now passes `expect_embedder`: `query()` (used by CLI, web `app.py:140`, `evidence.py`, and retrieval's visual tier) and `search_text()` (CLI, retrieval). `bench.py`'s unguarded `count()` is a corpus-size metric, not a search. The `skipped` attribute is per-instance and instances are per-call, so the long-lived web server has no cross-request race.
- **Identity consistency.** `registry.embedder_id`'s `None → "hash"` fallback exactly mirrors `get_text_embedder`; for `visual_embedder`, any config where the two could disagree makes `get_visual_embedder` raise before the tag matters. The cross-role mix-up (both roles resolving to `"hash"` on the stub config) is covered by the forced-distinct-ID spy test — good catch of an offline blind spot.
- **Contract/docs parity.** COORDINATION.md logs both the shard-format change and the query-time enforcement with the web-agent effect; CLAUDE.md's vector-space-trap section was updated in the same change (closing plan item X-1); no new env vars, CLI flags, or config keys. The `search`/`count` signature changes are backward-compatible optional parameters.
- **The prior review's finding** (config tag stamped over an injected embedder's vectors) is fixed as recommended, with the regression test covering both injected cases.

**Two minor findings survived:**

1. **`count`/`search` disagree on untagged dim-mismatched shards** (`retrieval.py:224`). `count(expect_embedder=…)` passes `qdim=None`, so an untagged shard whose dim doesn't match the current embedder counts as *usable*; `search_text` then skips that same shard. Concretely: a pre-tagging `text_vectors.npz` built under one config dir, queried under the other (hash-dim vs bge-dim) — `usable > 0` picks the semantic branch, search skips everything, zero semantic hits, and the lexical fallback and evidence note never fire. That is the exact silent-tier-drop the retrieval comment claims to prevent, surviving on the legacy-untagged path (only the stderr log line remains; before this change it was a crash, so it's still an improvement). Safe path: give `count` the same dim information `search` has (e.g. pass the current embedder's dim), or fall back to lexical when the semantic search returns nothing while shards were skipped; TAG-4's dim backfill narrows but doesn't remove it (see finding 2).

2. **The shipped guard contradicts the plan's TAG-4 design in the same range** (`sharded.py:34`). `_compatible` hard-rejects any tagged shard whose embedder string ≠ current, but this range's own plan (D4/TAG-4) says backfill will stamp legacy shards `{model: unspecified, dim}` so "dim alone still catches 64-vs-1152" — implying dim-only admission. Run TAG-4 against the guard as shipped and every dim-*matching* legacy shard gets evicted from search, a regression versus its current untagged admission. Safe path: add the `"unspecified"` carve-out (admit on dim match) now, or record in provenance-reprocess-plan.md that TAG-4 must extend `_compatible` — so the seam isn't rediscovered by crash.

Both are transitional-path corners, not defects in the tagged happy path, so: approve.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/retrieval.py", "line": 224, "issue": "count(expect_embedder=...) checks untagged shards with qdim=None while search checks them against the real query dim, so an untagged dim-mismatched text shard counts as 'usable' but is then skipped at search — the semantic branch is chosen, returns zero hits, and the lexical fallback + evidence note never fire.", "scenario": "A pre-tagging text_vectors.npz built under one config dir (hash-dim) is queried under the other (bge-dim): usable > 0 picks the semantic branch, search_text skips the shard, and the whole text tier silently drops with only a stderr log line — the exact silent-drop this change's comment claims to prevent. Fix: pass the current embedder's dim into count, or run the lexical fallback when semantic search yields nothing while shards were skipped."}, {"severity": "minor", "file": "src/va/storage/vector/sharded.py", "line": 34, "issue": "_compatible hard-rejects any tagged shard whose embedder string differs from the current one, but the same range's plan (D4/TAG-4) specifies backfilling legacy shards as {model: unspecified, dim} with dim-only matching — the shipped guard would evict dim-matching backfilled shards from search.", "scenario": "TAG-4 retags a legacy dim-64 shard as {embedder: 'unspecified', dim: 64}; a stub-config query (embedder 'hash', dim 64) that admitted the shard while untagged now skips it — backfill regresses search coverage. Fix: admit 'unspecified' tags on dim match now, or record in provenance-reprocess-plan.md that TAG-4 must extend _compatible."}]}
```
