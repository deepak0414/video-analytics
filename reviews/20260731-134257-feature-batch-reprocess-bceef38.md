# Agent review — approve

date: 2026-07-31T13:50:13.197747
range: origin/main..HEAD
branch: feature/batch-reprocess
findings: 0


---

## Full review

Review complete. I read the full `origin/main..HEAD` diff (five commits: the dry-run planner, the text/visual/caption in-place reprocessors, the shard-swap durability work, and the RPRC-2 dependency dedup), verified every suspicion against the surrounding source, and ran the offline suite: **525 passed, 2 skipped, 0 failed**.

**Verdict: approve.** Nothing critical, major, or minor survived verification. What I chased and where each suspicion dissolved:

**Correctness paths that check out:**
- The RPRC-2 dedup (`_SATISFIES` / `_dependency_ordered`) is sound: `_reprocess_vlm_captioner` rebuilds the *whole* text index via `backfill_text_index` using the current config's embedder, so restamping `text_embedder` without a second rebuild is valid. A captioner failure never populates `satisfied` (the `|=` is inside the success branch), so a failed provider correctly falls back to a direct `text_embedder` rebuild. A `--role`-scoped plan degrades to redundant-but-correct rebuilds, never a missed one. The stable sort keys providers first, and `satisfied` resets per video.
- The safety invariants hold on inspection: rows-first/provenance-second everywhere; a raising reprocessor lands in `failed` with no restamp (role stays stale, batch continues); the pinned-config fingerprint degrades a mid-batch config edit to a false-stale, never a missed-stale; unknown recorded fps refuses rather than guessing; zero-frame decode refuses rather than swapping in an empty shard; the removed-mid-rebuild recheck now exists on **both** the visual and text paths (`verify_exists`), closing the prior review's asymmetry finding.
- `captioner.caption(kf)` receives a list of keyframes — this matches the `VLMCaptioner` protocol (`Sequence[Image.Image]`) and ingest's identical call at `ingest.py:191`. `reindex_visual`'s payloads (`video_id`/`timestamp`/`source_uri`) and meta tag are byte-for-byte the ingest shapes, so the query join is unaffected.
- The temp shard `vectors_rebuild.npz` can't leak into search: `ShardedVectorStore._shards()` globs the exact filename `*/vectors.npz` (resp. `*/text_vectors.npz`).
- The swap/persist ordering claims hold against `sharded.py`: the cache keys on `.npz` mtime, `_load` requires both files and now empties on a count mismatch, so a torn read self-heals when the `.npz` replace bumps the mtime. The new json-first order is strictly better than the old order, which could serve silently misaligned vector/payload pairs. The one residual (same-count content mismatch in the microsecond swap window) is honestly disclosed in COORDINATION.md.
- The test monkeypatch targets all bind correctly because the production imports are function-local (fetched at call time), so the failure-path tests genuinely exercise the code they claim to.

**Prior-review follow-through:** the last review's three minors are all addressed in this range — the text-path removal recheck (`index_text(..., verify_exists=True)` + `test_text_rebuild_aborts_swap_if_video_removed_midway`), the ingest-parity assertion (`test_visual_reembed_preserves_frame_count_and_timestamps`), and the real-config gap, which is now recorded in `provenance-reprocess-plan.md` as a VALIDATION GAP with a concrete mandate: the pillar-B PR's `golden-verified` evidence must include a real-model reprocess smoke, not just the query golden run. That last item is the one open risk, but it is self-flagged with a named gate, so I'm not re-reporting it as a finding — just noting for the human that **the PR must actually carry that smoke** before `va reprocess` touches `.va-shots`.

**Docs/lifecycle parity:** the new command is in CLAUDE.md, every write-path change has a COORDINATION.md entry (including the web-agent-facing concurrency notes), the plan doc tracks each RPRC item plus the deferred `model_id` sub-item, the four finalized commit messages are self-sufficient descriptions with plan IDs only as trailing references, and HEAD is a provisional `need_agent_review:` subject (exempt). No test was deleted or weakened; the new suite of 36 reprocess tests covers the failure paths, not just the happy path.

```json
{"verdict": "approve", "findings": []}
```
