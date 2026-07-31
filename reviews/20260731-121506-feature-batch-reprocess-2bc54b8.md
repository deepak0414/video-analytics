# Agent review — approve

date: 2026-07-31T12:21:20.040188
range: origin/main..HEAD
branch: feature/batch-reprocess
findings: 0


---

## Full review

The full offline suite is green on this branch: **514 passed, 2 skipped**. Review complete — here is my report.

# Review: `origin/main..HEAD` (feature/batch-reprocess, 3 commits)

**Scope:** `va reprocess` selection front-end (RPRC-3a), executor + `text_embedder` wiring (RPRC-1a), `visual_embedder` re-embed (RPRC-1b), the `--yes` mutation gate, the `.json`-before-`.npz` shard write/swap ordering, the `index_text` temp+swap durability fix, plus tests and docs.

## Prior-review follow-up

The previous round's single minor (`index_text` unlinked the old `text_vectors` shard before rebuilding in place, so a disk-full/kill mid-`np.savez` destroyed it) is **fixed in HEAD**: `index_text` now builds to `text_vectors_rebuild` and swaps via the shared `swap_shard` (`text_index.py:84-95`), the fix has a real regression test (`test_text_rebuild_persist_failure_preserves_old_shard` monkeypatches `np.savez` to fail and asserts the old shard is byte-identical — it fails against the pre-fix code), and COORDINATION.md carries a superseding "durability — text shard" entry. No disputes in workflow-trust-plan.md touch this work.

## What I verified (suspicions chased to ground, all dissolved)

- **Safety invariant (rows first, provenance second)** holds on every path: a raising reprocessor lands in `failed` with no restamp; `backfill_text_index` returning `None` (video removed between plan and execute) is converted to a raise, so a purged provenance row can't be resurrected — both tested.
- **XOR scope check** `all_stale == bool(video)` is correct for all four input combinations, and is double-enforced at argparse (`required=True` mutually-exclusive group, tested via `SystemExit`).
- **fps preservation** is real: the visual reprocessor reads fps from the `visual_embedder` provenance row (upsert-keyed, so `prev[0]` is the only row) and refuses when unknown; the restamp preserves the prior row's fps (the test ingests at 1.0, pokes the row to 2.0, and asserts 2.0 survives — proving it reads the row, not the ingest arg); the skip pointer prints `va reingest <id> --fps <recorded>`, and `--fps` is a real reingest flag (`cli.py:520`).
- **Shard-swap concurrency claims hold against `sharded.py`**: the cache keys on `.npz` `st_mtime_ns` and `_load` gates on both files existing, so with `.npz` last a torn pair can only be cached under the *old* mtime and the `.npz` replace invalidates it — transient and self-healing, exactly as disclosed to the web agent in COORDINATION.md. A mid-`_load` failure propagates before the cache insert, so nothing broken is ever cached. Temp names (`vectors_rebuild.npz`, `text_vectors_rebuild.npz`) cannot match the reader globs (`*/vectors.npz`, `*/text_vectors.npz`).
- **Payload parity**: `reindex_visual` writes `{video_id, timestamp, source_uri}` — identical to ingest's frame payloads (`ingest.py:293-297`), so query-path joins are unaffected.
- **Zero-frame guard** is correct asymmetry: an empty *text* rebuild is legitimate (a video may have no text) while zero *frames* means silent decode failure (`sample_frames` always yields ≥1 frame on valid media) — and the guard fails before the swap, so the old shard and staleness both survive (tested).
- **Pinned-config restamp** drifts only in the safe direction (a mid-batch config edit stamps the old fingerprint over new rows → reads stale → re-runs), and its test genuinely exercises the pin (call-time `from va.provenance import role_fingerprint` binds after the monkeypatch).
- **Plan conformance**: RPRC-3's XOR scope + resumability + per-video rows-then-provenance ordering + whole-video `reingest` fallback are all implemented; RPRC-1c (caption + `observations` purge) and RPRC-2 deferrals are recorded in the plan status, and deferring dependency invalidation is safe since neither wired role is a *source* edge in the R1→R4/5/6/7, R5→R6, R8→R9 graph. The stub-over-real tag refusal is an explicitly recorded deferral, not a silent gap.
- **Docs/commit parity**: CLAUDE.md command line covers `--all-stale/--yes/--dry-run`; COORDINATION.md carries five dated entries including the two web-agent concurrency heads-ups; no new env vars. The two finalized subjects are plainly descriptive; HEAD is a provisional `need_agent_review:` subject (exempt).
- **Test integrity**: the earlier `test_cli_execution_is_gated_off` removal was a deliberate behavior replacement (execution shipped) with its live half surviving as `test_cli_dry_run_mutates_nothing`; the new failure-path tests each fail against the pre-change code.

Candidates I chased that fell below the precision bar: rc=0 when every role is skipped (output is explicit and the pointers are the designed remedy); the untested missing-media error branch in `_reprocess_visual_embedder` (defensive branch not implied by the plan's X-2 test list); a hypothetical mid-`swap_shard` failure leaving a persistent torn pair (requires `os.replace` failing within one directory after a sibling replace succeeded — exotic, and the role stays stale so a retry repairs it).

**Verdict: approve** — no findings survived verification.

```json
{"verdict": "approve", "findings": []}
```
