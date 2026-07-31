# Agent review — approve

date: 2026-07-31T11:43:34.666671
range: origin/main..HEAD
branch: feature/batch-reprocess
findings: 1

- **minor** `src/va/pipeline/text_index.py:84` — Shard rebuild (unlink both files, write .npz then .json) combined with the npz-mtime-keyed shard cache in sharded.py can cache a permanently EMPTY text_vectors store for a concurrent va serve reader; reindex_visual's npz-before-json replace order has the same torn-pair caching hazard, and the COORDINATION note understates the window as transient.
  - scenario: va reprocess rebuilds a video's text index while va serve is running; a query loads the shard after the new .npz is written but before the .json exists; _load silently yields an empty store which is cached under the final mtime, so that video vanishes from text search until the next rebuild or server restart. Safe path: use the temp+os.replace pattern in index_text, swap .json before .npz in both writers so a torn read self-heals on the npz mtime bump, and/or refuse to cache a shard whose vector/payload lengths mismatch; amend the COORDINATION entry.

---

## Full review

Review complete. I traced the full diff (three commits: the `va reprocess` dry-run planner, the executor wired for `text_embedder`, and the `visual_embedder` re-embed) against `stale.py`, `provenance_store.py`, `text_index.py`, `numpy_flat.py`, `sharded.py`, `ingest.py`, the CLI, and the plan doc. (Note: I could not execute `pytest` — the sandbox denied the command — so test claims below are from reading, not running.)

**What I checked and found sound:**

- **Scope logic** (`plan_reprocess`): the `all_stale == bool(video)` XOR guard, the non-done-video refusal, and the role/video intersection are correct; each has a test, including one that guards against filter-order refactors.
- **Restamp ordering invariant** (rows first, provenance second) holds in `execute_reprocess`; a raising reprocessor lands in `failed` with no restamp, and `backfill_text_index`'s `None` (removed video) is converted to a raise so a purged video's provenance is never resurrected — all tested, including the pinned-config test which correctly monkeypatches around the late `from va.provenance import role_fingerprint`.
- **Mid-batch config drift**: the executor pins one config for fingerprints while the reprocessors load fresh configs internally; I traced the mismatch cases and they all degrade to *false-stale* (safe retry), never falsely-current, as the comment claims.
- **fps preservation**: `stale_report.recorded_fps` → CLI `--fps` pointer (and `va reingest` does take `--fps`, default 1.0, so the pointer matters); the visual reprocessor refuses an unknown fps rather than guessing — tested.
- **Temp shard**: `vectors_rebuild.npz` cannot be picked up mid-build by search — `ShardedVectorStore` globs the exact name `*/vectors.npz`.
- **Payload/tag parity**: `reindex_visual`'s payloads (`video_id`, `timestamp`, `source_uri`) and meta tag match ingest's exactly; the zero-frames guard and the failed-embed shard-preservation both have tests that would fail on the old code (the `text_index` reorder test asserts byte-identical shard survival, which the pre-change unlink-first code cannot pass — satisfies the 2026-07-28 regression-test lesson).
- **Docs/contract/commit hygiene**: CLAUDE.md command line added, plan log updated per increment with RPRC-1c/RPRC-2 explicitly deferred as "Next", two COORDINATION heads-up entries for the web agent, and the two finalized commit subjects are plain descriptions with IDs trailing. All in order.

**One finding survived verification** — a torn-read interaction between the shard rewrite paths in this diff and the pre-existing process-level shard cache (`sharded.py:48-58`), which is keyed **only on the `.npz` mtime**:

- In `index_text` (src/va/pipeline/text_index.py:84-93) the rebuild still unlinks both files, then `persist()` writes `.npz` before `.json`. A `va serve` query that loads the shard between those two writes finds the `.npz` but no `.json`, so `_load` silently constructs an **empty** store — and caches it under the final mtime. That video's text search then stays blank in the server process until the next rebuild or restart. The COORDINATION entry assures the web agent "the replace window is just the local `.npz` write," which understates this: the window is small, but its effect is persistent, not transient.
- `reindex_visual` (src/va/pipeline/reprocess.py:148-151) has the milder variant: it replaces `.npz` before `.json`, so a torn read caches new vectors + old payloads permanently. For a same-fps re-embed the old payloads are content-identical, so this is benign today, but it stops being benign the moment frame counts differ.

Safe path: adopt the temp+`os.replace` pattern in `index_text` too, and in both places replace `.json` **before** `.npz` — since the cache keys on the npz mtime, a torn read then self-heals when the npz lands. Belt-and-braces: have `_load` refuse to load (or `_load_shard` refuse to cache) a pair whose vector/payload lengths mismatch. Rated minor: it needs a query landing in a millisecond window during a manual reprocess, but it deserves a fix (or at least an amended COORDINATION note) because the current note tells the other agent it's safer than it is.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/text_index.py", "line": 84, "issue": "Shard rebuild (unlink both files, write .npz then .json) combined with the npz-mtime-keyed shard cache in sharded.py can cache a permanently EMPTY text_vectors store for a concurrent va serve reader; reindex_visual's npz-before-json replace order has the same torn-pair caching hazard, and the COORDINATION note understates the window as transient.", "scenario": "va reprocess rebuilds a video's text index while va serve is running; a query loads the shard after the new .npz is written but before the .json exists; _load silently yields an empty store which is cached under the final mtime, so that video vanishes from text search until the next rebuild or server restart. Safe path: use the temp+os.replace pattern in index_text, swap .json before .npz in both writers so a torn read self-heals on the npz mtime bump, and/or refuse to cache a shard whose vector/payload lengths mismatch; amend the COORDINATION entry."}]}
```
