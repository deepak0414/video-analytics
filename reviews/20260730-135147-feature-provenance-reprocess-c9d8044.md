# Agent review — approve

date: 2026-07-30T13:56:54.066065
range: origin/main..HEAD
branch: feature/provenance-reprocess
findings: 0


---

## Full review

Review complete. The range is three commits: the plan doc (docs-only, plain subject — permitted), the finalized TAG-1/TAG-2 shard-tagging commit, and the provisional TAG-3 query-time guard under a `need_agent_review:` subject (exempt from the finalized-message rule). The full offline suite is green on this checkout: **452 passed, 2 skipped**, including the 7 new tagging tests and 8 new guard tests.

**What I probed and found sound:**

- **Guard logic (`sharded.py:_compatible`).** Tagged shards must match embedder AND dim; untagged legacy shards are admitted only on a dim match (the documented D4 gap, TAG-4 pending); an empty tagged shard (`dim: None`) is admitted on an embedder match, which the tests confirm prevents a perpetual false "reprocess" warning. The `qdim` computation handles both the 1-d visual query vector and the 2-d `(1, D)` text query matrix correctly via `atleast_2d`.
- **Coverage of every search path.** `query()` and `search_text()` pass their own role's id (and a test forces distinct ids to catch a cross-role mix-up the stub config would mask); the `va ask` visual tier routes through the guarded `query()`; `retrieval._gather`'s usability check passes the same `(embedder, dim)` pair the search-time guard uses, so count and search agree even on untagged shards — and both new fallback tests (tagged-stale and untagged-dim-mismatch) verify the lexical fallback plus a surfaced note. The note's "different embedder" wording is accurate in the dim-mismatch case too, since a different dim implies a different embedder.
- **Consistency of the stamp.** `embedder_id`'s KeyError→`"hash"` fallback mirrors `get_text_embedder` exactly, and `Config.role()` raises only KeyError; for `visual_embedder` a missing role crashes `get_visual_embedder` before any stamp or guard, so the paths can't disagree. The visual shard is persisted exactly once, with `set_meta` immediately before it — a crashed ingest can't leave a half-stamped shard.
- **Suspicions that dissolved on inspection:** the injected-embedder→`"unknown"` tag excluding a legitimately-current backfill shard has no current caller (`backfill_text_index` is never called with an embedder anywhere in the tree) and is explicitly recorded in the plan (RPRC-1) as the honest interim; `migrate.py` writing untagged shards is the TAG-4 target state; the pre-tagging golden workdir (`.va-shots`) keeps working because its untagged shards dim-match the real embedders; the mtime-keyed shard cache self-invalidates on re-stamp; the CLI user does see the skip warning (logging's lastResort handler emits WARNING to stderr).
- **Contract/docs parity.** Both COORDINATION.md entries accurately describe the shipped behavior for the web agent; CLAUDE.md's vector-space-trap section was updated in the same change (plan item X-1); no new env vars, CLI flags, or config keys exist in the diff. The finalized commit message is self-sufficient with plan IDs trailing, per the clarity rule. Plan status (TAG-1+2 and TAG-3 done, TAG-4 next) matches the code.

Nothing survived verification.

```json
{"verdict": "approve", "findings": []}
```
