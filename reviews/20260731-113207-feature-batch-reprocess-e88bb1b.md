# Agent review — approve

date: 2026-07-31T11:36:28.862700
range: origin/main..HEAD
branch: feature/batch-reprocess
findings: 2

- **minor** `src/va/cli.py:448` — The `va reprocess --help` text (and the module docstrings at src/va/pipeline/reprocess.py:6 and tests/test_reprocess.py:5) still say only text_embedder is wired in place, contradicting the visual_embedder reprocessor added in this same commit.
  - scenario: A user with a stale visual_embedder reads `va reprocess --help`, concludes visual re-embed is unsupported, and runs a needless whole-video `va reingest` (or is surprised when --all-stale rewrites the vectors shard they expected skipped); a future session reading either docstring re-plans RPRC-1b work that already shipped. Safe path: update all three descriptions to 'text+visual embedders wired' in the finalize amend.
- **minor** `src/va/pipeline/reprocess.py:143` — reindex_visual swaps the rebuilt shard in even when the decode yielded zero frames, replacing a good vectors shard with an empty one which execute_reprocess then restamps as current (row_count=0).
  - scenario: The media file at local_path is later truncated/replaced by a readable but zero-frame container (imageio opens it but iterates nothing, so no exception fires); reprocess swaps in an empty shard, provenance is stamped current, and the video silently vanishes from visual search while `va stale` reports clean. Safe path: raise when n == 0 before the os.replace swap so the run lands in `failed`, the prior shard survives, and the role stays stale for retry.

---

## Full review

Review complete — static analysis over `origin/main..HEAD` (pytest execution was not approved in this session, consistent with the four prior review rounds recorded in `reviews/`). Scope: two finalized commits (RPRC-3a selection, RPRC-1a text executor — both already reviewed and approved in earlier rounds) plus the provisional HEAD `e88bb1b`, which wires the `visual_embedder` in-place re-embed (RPRC-1b). I focused on the new commit while re-checking the whole range.

**What I verified on the visual path, and what dissolved:**

- **The safety invariant holds end-to-end.** `reindex_visual` builds to `vectors_rebuild.{npz,json}` and `os.replace`-swaps only after full success; a raising embedder leaves the prior shard byte-identical and no temp behind (tested). The executor restamps provenance only after the reprocessor returns, so every failure path stays stale and retryable.
- **The fps-density refusal is correct and tested**: an unknown recorded fps raises (→ `failed`, no restamp) rather than silently re-embedding at a different density; the skip/failure pointers reference `va reingest --fps`, and that flag really exists (`cli.py:172`).
- **Payload parity with ingest**: the rebuilt shard's payloads (`video_id`, `timestamp`, `source_uri`) and meta tag (`{"embedder": embedder_id(...)}`) match what `ingest.py:288-312` writes, so query-side joins and the TAG-3 embedder guard behave identically on a reprocessed shard.
- **Concurrent `va serve` is genuinely safe**: shard discovery globs the exact filename `*/vectors.npz` (so a crash-leftover `vectors_rebuild.npz` is never searched, and it's cleaned on the next rebuild), and the process-level shard cache is keyed by mtime-ns, which `os.replace` changes — the COORDINATION heads-up to the web agent is accurate. The two-file swap window can expose new `.npz` + old `.json`, but a same-fps re-embed produces identical payloads, so the window is benign.
- **Pinned-config direction** remains the safe one for the new role too: `get_visual_embedder()` loads fresh config while the restamp fingerprints the batch-pinned one, so a mid-batch config edit degrades to a false stale, never a missed one.
- Chased and dissolved: `video_dir` resolution for a renamed title (glob is key-prefix-only), `prev[0]` ordering on `ProvenanceStore.get` (upsert-keyed, single row per role), removed-video race for the visual role (raises before any write).

**Two minor findings survived:**

1. **Stale self-description in the same commit** — the user-facing `va reprocess --help` text (`src/va/cli.py:448`), the module docstring (`src/va/pipeline/reprocess.py:6`), and the test-file docstring (`tests/test_reprocess.py:5`) all still say only `text_embedder` is wired, contradicting the `visual_embedder` reprocessor this commit adds. This is the same stale-docstring class the previous round flagged for RPRC-1a, reintroduced for RPRC-1b. CLAUDE.md and COORDINATION.md got the update; these three spots didn't. Safe path: update all three in the finalize amend (the help string is the one users actually read).
2. **Zero-frame decode defeats the keep-the-old-shard invariant** — if `sample_frames` yields nothing (a readable but zero-frame container; corrupt files raise and are handled), `reindex_visual` swaps an *empty* shard over the good one and the executor restamps provenance current with `row_count=0`, silently removing that video from visual search while `va stale` reports clean. A done video had frames at ingest, so zero frames on re-decode is always wrong — unlike the text index, where empty is a legitimate state. Safe path: raise when `n == 0` before the swap, routing it to `failed` so the prior shard and staleness survive.

Both are minor; commit-message clarity for the two finalized commits is satisfied (self-sufficient descriptions, plan IDs trailing), HEAD is provisional and exempt. Verdict: **approve**.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/cli.py", "line": 448, "issue": "The `va reprocess --help` text (and the module docstrings at src/va/pipeline/reprocess.py:6 and tests/test_reprocess.py:5) still say only text_embedder is wired in place, contradicting the visual_embedder reprocessor added in this same commit.", "scenario": "A user with a stale visual_embedder reads `va reprocess --help`, concludes visual re-embed is unsupported, and runs a needless whole-video `va reingest` (or is surprised when --all-stale rewrites the vectors shard they expected skipped); a future session reading either docstring re-plans RPRC-1b work that already shipped. Safe path: update all three descriptions to 'text+visual embedders wired' in the finalize amend."}, {"severity": "minor", "file": "src/va/pipeline/reprocess.py", "line": 143, "issue": "reindex_visual swaps the rebuilt shard in even when the decode yielded zero frames, replacing a good vectors shard with an empty one which execute_reprocess then restamps as current (row_count=0).", "scenario": "The media file at local_path is later truncated/replaced by a readable but zero-frame container (imageio opens it but iterates nothing, so no exception fires); reprocess swaps in an empty shard, provenance is stamped current, and the video silently vanishes from visual search while `va stale` reports clean. Safe path: raise when n == 0 before the os.replace swap so the run lands in `failed`, the prior shard survives, and the role stays stale for retry."}]}
```
