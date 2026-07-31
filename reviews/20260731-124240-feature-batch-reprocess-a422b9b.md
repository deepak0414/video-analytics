# Agent review — approve

date: 2026-07-31T12:48:02.417670
range: origin/main..HEAD
branch: feature/batch-reprocess
findings: 2

- **minor** `src/va/storage/vector/numpy_flat.py:61` — _load silently reads a vector/payload count mismatch as an empty shard with no logging, so a genuinely corrupt shard permanently drops its video from search with zero operator signal.
  - scenario: A shard's .json is truncated by disk corruption; every query thereafter loads the pair, hits the length check, and returns no hits for that video while va stale reads clean and nothing is logged — unlike the embedder-mismatch skip path in sharded.py which warns with a remedy. Safe path: logger.warning the shard path and both counts on mismatch.
- **minor** `src/va/pipeline/reprocess.py:294` — execute_reprocess restamps provenance with run_id=None because it runs outside any traced_run, unlike ingest which records the active run id on every provenance stamp.
  - scenario: After a reprocess, an operator inspecting role_provenance to find which run rebuilt a shard gets NULL run_id and cannot correlate the restamp to a trace, breaking the ingest-side attribution the traceability feature established. Safe path: wrap the batch in traced_run (or record the deferral in provenance-reprocess-plan.md).

---

## Full review

The prior review round's two findings (the torn-read count check and the RPRC-1 sub-item deferral note) are both addressed in the current HEAD — the `_load` length guard and the plan's "Sub-item DEFERRED" note exist now. My remaining verification (captioner signature, `keyframes_for_spans` clamping, payload-schema parity with ingest, provenance upsert semantics, `role_fingerprint(role, cfg)` signature, swap/cache mtime ordering) all dissolved as non-issues.

## Review summary

**Range:** `fa489c3..a422b9b` — `va reprocess` selection front-end, executor, and three wired role reprocessors (text_embedder, visual_embedder, vlm_captioner), plus shard-durability hardening (`swap_shard`, `.json`-before-`.npz` ordering, torn-pair length check).

**What I verified as sound:**

- **Selection** (`plan_reprocess`): the XOR scope guard, role validation, non-done `--video` refusal, and role×video intersection are all correct and each has a targeted test.
- **Executor invariant**: rows-first / provenance-second holds everywhere; a raising reprocessor is recorded `failed` with no restamp (tested, including the removed-video race where `backfill_text_index`'s `None` is converted to a failure rather than a 0-row restamp); the config is pinned once per batch and that is pinned-tested via monkeypatch.
- **Captioner reprocess**: `caption(kf)` with a list matches the `Sequence[Image.Image]` protocol exactly as ingest calls it, and `frames_at` clamps indices so `keyframes_for_spans` always returns one entry per span — the `zip` cannot silently drop segments. Caption-all-first durability is tested. Both dependents (text-index rebuild + `observations` purge) are propagated and tested; the acknowledged double-rebuild redundancy when both caption and text_embedder are stale is recorded in the plan and is correct in either iteration order.
- **Visual re-embed**: payload keys (`video_id`/`timestamp`/`source_uri`) match ingest's exactly; unknown-fps refusal, zero-frame corrupt-media refusal, and temp-build+swap durability are each tested with the old shard verified byte-identical.
- **Concurrency ordering**: `.json`-then-`.npz` write/swap order plus the `_load` length check genuinely closes the persistent-empty-shard cache race (`_load_shard` keys on `.npz` mtime_ns; `os.replace` is atomic; the residual same-count window is honestly documented in COORDINATION.md).
- **Docs/contracts**: the new command and flags are in CLAUDE.md; four COORDINATION.md entries cover every cross-layer behavior change including the `index_text` durability change that affects ingest, with web-agent callouts. Finalized commit subjects are plain descriptions with plan IDs trailing; the `need_agent_review:` HEAD is lifecycle-exempt.

**Findings (both minor):**

1. **`src/va/storage/vector/numpy_flat.py:61`** — the `_load` mismatch guard silently empties the shard with no log. For the transient swap race that's fine (self-heals on the mtime bump), but for a genuinely corrupt shard the video *permanently* vanishes from search with zero operator signal — the analogous embedder-guard path in `sharded.py` logs a warning with a remedy pointer. Safe path: `logger.warning` with the shard path and both counts so a persistent mismatch is diagnosable.

2. **`src/va/pipeline/reprocess.py:294`** — `execute_reprocess` calls `current_run_id()` outside any `traced_run`, so every restamp records `run_id=NULL`, unlike ingest's stamps (`ingest.py:355`). The delivered traceability feature loses attribution for reprocessed rows: an operator asking "which run rebuilt this shard?" gets NULL and cannot correlate to a trace. Safe path: wrap the batch in `traced_run` (or record the deferral in the plan doc).

**Verdict: approve** — no critical or major findings.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/storage/vector/numpy_flat.py", "line": 61, "issue": "_load silently reads a vector/payload count mismatch as an empty shard with no logging, so a genuinely corrupt shard permanently drops its video from search with zero operator signal.", "scenario": "A shard's .json is truncated by disk corruption; every query thereafter loads the pair, hits the length check, and returns no hits for that video while va stale reads clean and nothing is logged — unlike the embedder-mismatch skip path in sharded.py which warns with a remedy. Safe path: logger.warning the shard path and both counts on mismatch."}, {"severity": "minor", "file": "src/va/pipeline/reprocess.py", "line": 294, "issue": "execute_reprocess restamps provenance with run_id=None because it runs outside any traced_run, unlike ingest which records the active run id on every provenance stamp.", "scenario": "After a reprocess, an operator inspecting role_provenance to find which run rebuilt a shard gets NULL run_id and cannot correlate the restamp to a trace, breaking the ingest-side attribution the traceability feature established. Safe path: wrap the batch in traced_run (or record the deferral in provenance-reprocess-plan.md)."}]}
```
