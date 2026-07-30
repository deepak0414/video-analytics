# Agent review — approve

date: 2026-07-30T14:17:44.448366
range: origin/main..HEAD
branch: feature/provenance-reprocess
findings: 1

- **minor** `src/va/pipeline/retrieval.py:186` — The stale-index surfacing added for the text tier is absent for the visual tier: query() discards store.skipped, so a fully-stale visual index yields an Evidence bundle with zero visual items and no ev.notes entry, and no programmatic caller (retrieval, web) can reach the skipped count COORDINATION.md says is exposed.
  - scenario: Ingest with the default stub config, then run `va ask "show me the red car"` under VA_CONFIG_DIR=run-siglip/config without reingesting: every visual shard is skipped ('hash' != 'siglip'), retrieve() gathers 0 visual hits with no note, and the reasoner answers that the footage contains no such scene — indistinguishable from a true no-match; the only witness is a stderr log line the web UI never shows. Fix: expose the skipped count through query() (stats return or out-param) and append an ev.notes entry in _gather when visual shards were skipped, mirroring the text tier's note.

---

## Full review

Verification complete. I could not run pytest in this session (the command requires an approval unavailable here), so test assessment is static — I confirmed every helper the new tests use exists with matching signatures (`clear_shard_cache`, `Catalog.get`, `Workspace.video_dir`, `schema.connect`, `write_color_video`), and that each new test would fail on pre-change code.

## Review — origin/main..HEAD (TAG-1/2 shard tagging + TAG-3 query-time guard)

**Scope:** three commits — the plan doc (docs-only, plain subject, permitted), the finalized tagging commit (self-sufficient description, fine), and the provisional `need_agent_review:` TAG-3 commit (exempt from the finalized-message rule).

**All four findings from the two prior reviews of this range are genuinely fixed at HEAD, each with a regression test:**

- The fully-stale text index now routes to the lexical fallback with a surfaced `ev.notes` entry: `retrieval.py:225-229` scores usability with the same `(embedder, dim)` pair the search-time guard uses, including passing the real query dim so `count()` and `search()` agree on untagged legacy shards (`test_retrieval_falls_back_to_lexical_on_stale_text_index`, `test_retrieval_falls_back_when_untagged_text_shard_dim_mismatches`).
- The empty-tagged-shard false skip is fixed: `_compatible` admits `dim: None` on an embedder match (`sharded.py:36-38`, with the perpetual-false-warning test).
- The cross-role wiring blind spot (both roles = `"hash"` on the stub config) is covered by the forced-distinct-ID spy test.
- The TAG-4/`unspecified` contradiction was resolved by dissolving TAG-4 in the plan with sound reasoning (a dim-only backfill is a no-op given the untagged dim-guard; the same-dim foreign-model legacy gap is documented as inherent in D4).

**What else I verified and found sound:** format compatibility both directions (old shards load `meta=None`; old readers ignore the extra `.npz` entry; the meta entry is a 0-d unicode array, safe under `allow_pickle=False`); `embedder_id`'s KeyError/None→`"hash"` fallback exactly mirrors `get_text_embedder` (`registry.py:49-56`, `configuration.py:40-42`); the ingest stamp uses the same in-process config as the embedder that produced the vectors, placed before the single `persist()` so an aborted embed never persists a stamped shard (`ingest.py:222-263`); the injected-embedder path tags honestly (`model_id` or `"unknown"`); every in-repo `search()` caller passes its own role's identity (`query.py:27`, `text_search.py:43`), and `bench.py`'s unguarded `count()` is a size metric, not a search; the golden workdir's pre-tagging shards stay admitted (dims match their producing config), so the golden gates don't silently regress; `qdim` extraction handles both the 1-D visual and (1,D) text query shapes; no logging config exists, so the guard's `logger.warning` reaches stderr via Python's last-resort handler on the CLI. Docs parity holds: CLAUDE.md trap #2, two COORDINATION.md entries with the web-agent effect, plan status — all in-range; no new env vars, CLI flags, or config keys.

**One minor finding survives:**

1. **minor — `src/va/pipeline/retrieval.py:186`** — The text tier's stale-index treatment was not extended to the visual tier: `query()` discards its `ShardedVectorStore` (and with it `store.skipped`), so when every visual shard is stale (stub-ingested workdir queried under `run-siglip`), `retrieve()` builds an Evidence bundle with zero visual items and **no note**, and `va ask`'s reasoner cannot distinguish "the corpus has no visual match" from "the visual index was unusable" — it will answer as if the footage truly lacked the content, with only a stderr log line as witness (invisible to a web user; COORDINATION.md tells the web agent the count is "exposed on `store.skipped`", but the `query()` API gives callers no path to it). Ask's deep-scan self-escalation partially mitigates (it decodes frames directly), which is why this is minor, not major. Safe path: expose the skipped count through `query()` (e.g. return stats or accept an optional store/stats out-param) and append an `ev.notes` entry in `_gather` when visual shards were skipped, mirroring the text tier's note.

No critical or major findings, so: **approve**.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/retrieval.py", "line": 186, "issue": "The stale-index surfacing added for the text tier is absent for the visual tier: query() discards store.skipped, so a fully-stale visual index yields an Evidence bundle with zero visual items and no ev.notes entry, and no programmatic caller (retrieval, web) can reach the skipped count COORDINATION.md says is exposed.", "scenario": "Ingest with the default stub config, then run `va ask \"show me the red car\"` under VA_CONFIG_DIR=run-siglip/config without reingesting: every visual shard is skipped ('hash' != 'siglip'), retrieve() gathers 0 visual hits with no note, and the reasoner answers that the footage contains no such scene — indistinguishable from a true no-match; the only witness is a stderr log line the web UI never shows. Fix: expose the skipped count through query() (stats return or out-param) and append an ev.notes entry in _gather when visual shards were skipped, mirroring the text tier's note."}]}
```
