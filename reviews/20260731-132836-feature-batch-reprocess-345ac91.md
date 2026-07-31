# Agent review — approve

date: 2026-07-31T13:34:42.237343
range: origin/main..HEAD
branch: feature/batch-reprocess
findings: 3

- **minor** `src/va/pipeline/text_index.py:95` — Text-index rebuild lacks the removed-during-rebuild catalog recheck that reindex_visual performs before its swap, so persist()+swap_shard can resurrect a shard for a just-removed video.
  - scenario: `va remove` lands while a real text embedder is mid-rebuild (seconds on a long transcript): index_text re-creates the deleted video dir and swaps in text_vectors; `va transcript`/`va caption` then return ghost hits for a video with no catalog row, and `va remove` can no longer find it to clean up.
- **minor** `tests/test_reprocess.py:338` — No test asserts the re-embedded visual shard matches the ingest-produced shard (frame count / payload timestamps), which is the feature's core 'preserve the recorded density' claim.
  - scenario: A later change to sample_frames stride rounding or fps plumbing shifts every re-embedded timestamp; all current tests still pass, the role is restamped current, and search results point at wrong moments until real footage exposes it.
- **minor** `provenance-reprocess-plan.md:219` — The in-place reprocess write path is validated only on the stub combination; no real-backend (run-siglip/.va-shots golden workdir) check or attestation is named anywhere in the plan or COORDINATION entries.
  - scenario: The first real use — reprocessing a SigLIP/Qwen workdir after a model switch — exercises ModelManager loading, real embed batching, and golden-query relevance for the first time in production, where a silent breakage costs hours of GPU to recover.

---

## Full review

Review complete. I read the full diff for `origin/main..HEAD` (the five `reprocess` commits: dry-run planner, text/visual/caption in-place reprocessors, shard-swap durability, and RPRC-2 dependency dedup) and verified every suspicious point against the surrounding source rather than the diff alone.

**What I checked and what dissolved:**

- The dedup restamp (`_SATISFIES`) is sound: `_reprocess_vlm_captioner` rebuilds the *entire* text index via `backfill_text_index` with the current config's embedder, so restamping `text_embedder` without a second rebuild is valid; a captioner failure never populates `satisfied`, and a `--role`-filtered plan degrades to redundant-but-correct rebuilds.
- The rows-first/provenance-second ordering, the pinned-config fingerprinting, and the failure paths (backfill returning `None`, zero-frame decode, unknown fps refusal, removed-video pre-swap recheck in the visual path) all behave as documented and are each covered by a test that would fail on the old behavior.
- The shard-swap ordering claims hold against the actual `sharded.py` cache: `_load_shard` stats the `.npz` mtime before loading, `_load` requires both files and now empties on a count mismatch, so the torn-read race genuinely self-heals on the next query. The same-count residual window is honestly disclosed in COORDINATION.md.
- `captioner.caption(kf)` with a keyframe list matches ingest's call and the `VLMCaptioner` protocol exactly; `reindex_visual`'s payloads and meta tag are byte-for-byte the ingest shapes; `stale_report` already returns `recorded_fps`; `va reingest --fps` exists for the skip pointer; the monkeypatch targets in the tests bind correctly because the imports are function-local.
- Docs parity is good (CLAUDE.md one-liner, COORDINATION.md entries per write-path change, plan doc updated with the deferred `model_id` sub-item), best-effort semantics are preserved (the `index_text` change stays inside ingest's try/except), and the four finalized commit subjects are self-sufficient descriptions.

**What survived — three minor findings, no blockers:**

1. **Removal-race asymmetry in the text path** (`text_index.py:95`). `reindex_visual` re-checks the catalog right before its swap because `persist()` re-creates a directory `va remove` just deleted — but `index_text`/`backfill_text_index` has the identical race with no recheck. A `va remove` landing during a slow real-embedder text rebuild resurrects an orphan `text_vectors` shard whose hits surface in `va transcript`/`va caption` with no catalog row, and `va remove` can't find the video again to clean it. Safe path: mirror the visual path's pre-swap catalog recheck (drop the temp and raise), since the diff itself established that guard for the same race class.
2. **No ingest-parity assertion for the visual re-embed** (`tests/test_reprocess.py:338`). The core claim — re-sampling at the recorded fps reproduces the ingest frame set — is never asserted; the test checks re-tag and stale-clear only. A future stride/fps regression would restamp the role current while silently shifting every timestamp. Safe path: assert the rebuilt shard's count and payload timestamps equal the ingest-produced shard's (cheap with the stub).
3. **Real-config combination unvalidated** (`provenance-reprocess-plan.md:219`). The write path's entire purpose is model switches on real workdirs (run-siglip / `.va-shots`), yet all coverage is stub-path and no golden-workdir smoke or attestation is named. Safe path: name the real-backend check in the pillar-B PR (e.g. reprocess one role in a golden workdir, re-run the golden queries) or state explicitly why the stub tests are sufficient.

Verdict: **approve** — nothing critical or major; the three minors are hardening/coverage gaps, not wrong-result paths on realistic single-writer inputs.

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "src/va/pipeline/text_index.py", "line": 95, "issue": "Text-index rebuild lacks the removed-during-rebuild catalog recheck that reindex_visual performs before its swap, so persist()+swap_shard can resurrect a shard for a just-removed video.", "scenario": "`va remove` lands while a real text embedder is mid-rebuild (seconds on a long transcript): index_text re-creates the deleted video dir and swaps in text_vectors; `va transcript`/`va caption` then return ghost hits for a video with no catalog row, and `va remove` can no longer find it to clean up."},
{"severity": "minor", "file": "tests/test_reprocess.py", "line": 338, "issue": "No test asserts the re-embedded visual shard matches the ingest-produced shard (frame count / payload timestamps), which is the feature's core 'preserve the recorded density' claim.", "scenario": "A later change to sample_frames stride rounding or fps plumbing shifts every re-embedded timestamp; all current tests still pass, the role is restamped current, and search results point at wrong moments until real footage exposes it."},
{"severity": "minor", "file": "provenance-reprocess-plan.md", "line": 219, "issue": "The in-place reprocess write path is validated only on the stub combination; no real-backend (run-siglip/.va-shots golden workdir) check or attestation is named anywhere in the plan or COORDINATION entries.", "scenario": "The first real use — reprocessing a SigLIP/Qwen workdir after a model switch — exercises ModelManager loading, real embed batching, and golden-query relevance for the first time in production, where a silent breakage costs hours of GPU to recover."}
]}
```
