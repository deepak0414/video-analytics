# Agent review — approve

date: 2026-07-30T14:27:56.259778
range: origin/main..HEAD
branch: feature/provenance-reprocess
findings: 0


---

## Full review

Review complete. I ran the full offline suite (453 passed, 2 skipped) and traced every changed path plus its callers.

## Review — origin/main..HEAD (TAG-1/2 shard tagging + TAG-3 query-time guard + visual stale-note)

**Scope:** three commits — the plan doc (docs-only, plain subject, permitted), the finalized tagging commit (self-sufficient message), and the provisional `need_agent_review:` TAG-3 commit (exempt from the finalized-message rule). HEAD adds one delta beyond the last-reviewed commit (44733a7): the visual-tier stale-index note in `retrieval.py:187-198` plus its regression test.

**The prior round's surviving minor finding is genuinely fixed at HEAD.** `_gather` now distinguishes a stale visual index from a true no-match: when `visual_query` returns nothing, it re-derives usability with the same `(embedder, dim)` pair the search guard uses and appends an `ev.notes` entry, so `va ask`'s reasoner (and the web layer reading Evidence) sees "visual index unusable — run `va reingest`" instead of a silent zero. `test_retrieval_notes_a_stale_visual_index` covers it. The short-circuit is correct (`vtotal and not vstore.count(...)` — the model probe never runs on an empty workdir), and "usable == 0 ⟺ empty hits" holds because search has no score threshold: any compatible non-empty shard yields hits.

**What else I verified and found sound:**

- *Correctness of the guard:* `_compatible` handles all nine (tagged/untagged/empty × dim-match/mismatch/unknown) cells correctly; the empty-tagged-shard case is admitted on embedder match (with the perpetual-false-warning test); `qdim` extraction handles both the 1-D visual and (1, D) text query shapes.
- *Write-path integrity:* `reingest` does `rmtree` on the video dir before re-ingesting, so a shard is never appended to under a new embedder and then mis-stamped; ingest stamps `set_meta` before the single `persist()`, so an aborted embed never persists a stamped shard; `index_text` deletes both shard files first, so the tag always describes the vectors beside it. The injected-embedder path tags honestly (`model_id` or `"unknown"`, which TAG-3 then skips).
- *Identity consistency:* `embedder_id` and `get_text_embedder` agree on the KeyError/None → `"hash"` fallback; ingest-stamp and query-guard use the same function on the same config, so tags are consistent per role. (The theoretical divergence for `visual_embedder` with `model: null` is unreachable — `get_visual_embedder` raises before any search happens.)
- *Format compatibility both directions:* old shards load `meta=None`; the meta entry is a 0-d unicode array, safe under `allow_pickle=False`; old readers ignore the extra `.npz` entry; `count()` gained only defaulted kwargs.
- *Remaining unguarded call sites are legitimately unguarded:* `query.py:21` / `text_search.py:38` are emptiness early-exits, `bench.py` is a size metric, `migrate.py` is a layout-idempotency check; the `ocr/actions/transcript` `.search` calls are SQLite stores, not vector.
- *Partial staleness* (some shards skipped while others return hits) surfaces only as a logged warning — this is decision D3, locked in the plan with a revisit trigger and stated plainly in the COORDINATION.md entry, so it is a documented design choice, not a gap.
- *Combination coverage:* the both-roles-resolve-to-"hash" blind spot on the stub config is closed by the forced-distinct-ID spy test; the golden workdir's pre-tagging shards remain admitted via the untagged dim-match path, so the golden gates don't silently regress.
- *Docs parity:* CLAUDE.md trap #2 updated, two COORDINATION.md entries with the web-agent effect, plan status current (TAG-4 dissolved with sound reasoning); no new env vars, CLI flags, or config keys.
- *Test integrity:* no tests deleted or weakened; suite is green (453 passed, 2 skipped).

No findings survive verification.

```json
{"verdict": "approve", "findings": []}
```
