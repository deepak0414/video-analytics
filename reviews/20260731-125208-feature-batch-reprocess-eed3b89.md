# Agent review — approve

date: 2026-07-31T12:59:16.010724
range: origin/main..HEAD
branch: feature/batch-reprocess
findings: 2

- **minor** `src/va/pipeline/reprocess.py:227` — _reprocess_vlm_captioner ignores backfill_text_index returning None (video removed mid-run), unlike _reprocess_text_embedder which raises for exactly this, so the role reports success and execute_reprocess restamps provenance for a purged video.
  - scenario: A `va remove` from the concurrent web UI lands during a minutes-long real-VLM re-caption: set_caption no-ops onto purged segment rows, backfill returns None, the role is restamped current — resurrecting a role_provenance row for a video that no longer exists (the exact hazard the text path's None-check and its test guard against). Safe path: raise on None like _reprocess_text_embedder so it routes to failed.
- **minor** `src/va/pipeline/reprocess.py:146` — reindex_visual swaps the rebuilt shard in without re-checking the video still exists; persist() mkdirs the deleted video dir back into existence, resurrecting a shard for a removed video.
  - scenario: `va remove` deletes the video dir while an hours-long real-model re-embed is running; persist() recreates the dir, swap_shard installs vectors.npz, the sharded glob rejoins it, and the removed video reappears in visual search via the payload source_uri fallback (query.py:40) with a dead catalog link — and execute_reprocess restamps its provenance. Safe path: verify the catalog row (or video dir) still exists immediately before swap/restamp and record the role as failed otherwise.

---

## Full review

Review complete. The branch (`origin/main..HEAD`, four commits building `va reprocess`: selection front-end, executor, text/visual/caption reprocessors, and the shard-swap durability work) is solid. I verified the load-bearing claims rather than taking them on faith:

- **Contracts hold.** `captioner.caption(kf)` matches ingest's per-segment list-of-keyframes call; `reindex_visual`'s payload keys match ingest's (`video_id`/`timestamp`/`source_uri`); `stale_report` really does carry `recorded_fps`; the sharded cache keys on `.npz` `st_mtime_ns` (sharded.py:53), so the "swap `.json` first, `.npz` last" invariant and the torn-read length guard in `_load` behave exactly as the comments and COORDINATION.md claim, including the acknowledged same-count residual window.
- **Safety ordering is right.** Rows/shard before provenance restamp everywhere; failures route to `failed` without restamping; the pinned batch config degrades to false-stale (safe direction); `--yes` gating and the mkdir-free temp naming (`vectors_rebuild`, no dot) check out; temp shards don't match the `*/vectors.npz` glob.
- **Tests are real.** The persist-failure and boom-embedder tests would fail on the pre-diff code (which unlinked the live shard first). Full offline suite: **519 passed, 2 skipped**. Docs parity is good (CLAUDE.md one-liner, five COORDINATION.md entries, plan-doc as-builts with deferred items explicitly recorded). Finalized commit messages are self-sufficient with plan IDs trailing; the provisional `need_agent_review` subject is exempt.

Two minor findings survived verification, both in the same family: a `va remove` racing a long real-model reprocess (a live scenario — COORDINATION.md advertises concurrent `va serve` on the shared workdir, and the commit message itself cites hours-long re-embeds). The plan→execute removal race is guarded at each reprocessor's *start* (and tested), but mid-run removal is not:

1. `_reprocess_vlm_captioner` ignores `backfill_text_index` returning `None` (reprocess.py:227) — the text reprocessor treats that same `None` as a hard failure precisely so a removed video is never restamped, and `test_execute_on_removed_video_fails_without_resurrecting_provenance` pins that. The captioner path silently proceeds and restamps a purged `role_provenance` row. Safe path: raise on `None` exactly like `_reprocess_text_embedder`.
2. `reindex_visual` swaps the rebuilt shard in (reprocess.py:146) without re-checking the video still exists; `persist()` mkdirs the deleted video dir back, the glob rejoins the shard, and the removed video reappears in search via the payload `source_uri` fallback (query.py:40), with its provenance restamped. Safe path: re-verify the catalog row just before swap/restamp and route to `failed` if gone.

Neither is a merge-blocker: both are race-conditioned, degrade to stale artifacts rather than crashes or misjoined vectors, and the partial guard + test show the hazard was understood.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/reprocess.py", "line": 227, "issue": "_reprocess_vlm_captioner ignores backfill_text_index returning None (video removed mid-run), unlike _reprocess_text_embedder which raises for exactly this, so the role reports success and execute_reprocess restamps provenance for a purged video.", "scenario": "A `va remove` from the concurrent web UI lands during a minutes-long real-VLM re-caption: set_caption no-ops onto purged segment rows, backfill returns None, the role is restamped current — resurrecting a role_provenance row for a video that no longer exists (the exact hazard the text path's None-check and its test guard against). Safe path: raise on None like _reprocess_text_embedder so it routes to failed."}, {"severity": "minor", "file": "src/va/pipeline/reprocess.py", "line": 146, "issue": "reindex_visual swaps the rebuilt shard in without re-checking the video still exists; persist() mkdirs the deleted video dir back into existence, resurrecting a shard for a removed video.", "scenario": "`va remove` deletes the video dir while an hours-long real-model re-embed is running; persist() recreates the dir, swap_shard installs vectors.npz, the sharded glob rejoins it, and the removed video reappears in visual search via the payload source_uri fallback (query.py:40) with a dead catalog link — and execute_reprocess restamps its provenance. Safe path: verify the catalog row (or video dir) still exists immediately before swap/restamp and record the role as failed otherwise."}]}
```
