# Agent review — approve

date: 2026-07-31T13:14:13.623952
range: origin/main..HEAD
branch: feature/batch-reprocess
findings: 0


---

## Full review

Review complete. I traced every load-bearing claim in the branch rather than taking the comments on faith, and everything held up. Summary of what I verified:

**Scope:** four commits building `va reprocess` end-to-end — selection (`plan_reprocess`), the executor (`execute_reprocess`), three wired reprocessors (text/visual/caption), the shard-swap durability work (`swap_shard`, persist ordering, torn-read guard), and the `ObservationStore.purge` propagation.

**Correctness checks that passed:**
- The two minor findings from the previous review (eed3b89) are both **fixed in this range with pinning tests**: `_reprocess_vlm_captioner` now raises when `backfill_text_index` returns `None` (`test_caption_reprocess_fails_when_backfill_reports_removed`), and `reindex_visual` re-checks the catalog immediately before the swap and cleans its temp files (`test_visual_reembed_aborts_swap_if_video_removed_midway`). The residual TOCTOU window between check and swap is microseconds — the recommended safe path was followed.
- The concurrency invariants are real, not just asserted: the sharded cache keys on `.npz` `st_mtime_ns` (`sharded.py:53`), `_load` gates on both files existing, so json-first/npz-last ordering in both `persist` and `swap_shard` gives readers old-pair-or-new-pair; the length-mismatch guard reads a torn pair as empty and self-heals on the mtime bump. The same-count residual is honestly disclosed in COORDINATION.md. Temp shards (`vectors_rebuild.npz`, no dot, distinct filename) can't match the `*/vectors.npz` glob.
- Safety ordering is consistent everywhere: rows/shard first, provenance restamp second; failures route to `failed` without restamping (stays stale, retryable); the pinned batch config degrades to false-stale, the safe direction; unknown visual fps refuses rather than silently changing density; the zero-frames guard refuses to swap in an empty shard.
- Contract parity: `captioner.caption(kf)` matches ingest's list-of-keyframes call shape; `reindex_visual` payload keys (`video_id`/`timestamp`/`source_uri`) match ingest's; `va reingest --fps` exists for the skip pointer (and the pointer test pins the fps carry-through); `ProvenanceStore.record` is an upsert so `prev[0]` is well-defined; ingest's `index_text` call is still inside a best-effort try/except, so the new swap failure mode cannot abort ingest.
- Tests are genuine: the shard-preservation tests fail on the pre-diff in-place-unlink code; the removed-video tests pin the resurrection hazards; the pinned-config test correctly intercepts the late-bound imports. Full offline suite: **521 passed, 2 skipped, 0 failed**.
- Documentation parity is strong: CLAUDE.md command line, five dated COORDINATION.md entries (including explicit ⚠-grade heads-ups for the web agent and a superseded-claim correction), plan-doc as-builts with the deferred `model_id` sub-item recorded rather than silently dropped. The three finalized commit subjects are self-sufficient descriptions with plan IDs trailing; the `need_agent_review:` HEAD is exempt.

Suspicions I chased that dissolved: writer-writer collision on the fixed temp name requires two concurrent mutating runs on the same video, and the realistic path (web reingest during reprocess) is caught by the remove-guards; the empty resurrected directory after an aborted swap is invisible to the shard glob; a zero-segment captioner run restamping with `row_count=0` is correct behavior.

No findings survive verification.

```json
{"verdict": "approve", "findings": []}
```
