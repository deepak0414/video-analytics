# Agent review — approve

date: 2026-07-31T12:09:14.341168
range: origin/main..HEAD
branch: feature/batch-reprocess
findings: 1

- **minor** `src/va/pipeline/text_index.py:85` — index_text still unlinks the old text_vectors shard before writing the new one, so a hard failure (disk-full in np.savez, process kill) between unlink and persist destroys the shard — the visual path's temp+swap pattern was not applied to the text path, and COORDINATION.md's 'failed rebuild leaves the prior shard intact' claim only holds for failures before the unlink.
  - scenario: During `va reprocess --all-stale --yes`, the disk fills (or the process is killed) while np.savez is writing text_vectors.npz after the old pair was unlinked: the video's text search goes silently empty until a retry succeeds; the role stays stale so it is recoverable, but the advertised leave-old-shard-intact guarantee is violated. Fix: build to a temp shard and os.replace-swap json-then-npz, as reindex_visual already does.

---

## Full review

Review complete. The suite is green (513 passed, 2 skipped) and I chased every suspicion to ground. Summary of what I verified and my one surviving finding:

# Review: `origin/main..HEAD` (feature/batch-reprocess, 3 commits)

**Scope:** `va reprocess` selection front-end (RPRC-3a), executor + `text_embedder` wiring (RPRC-1a), `visual_embedder` wiring (RPRC-1b), the `--yes` execution gate, the `.json`-before-`.npz` shard write-ordering change, and tests/docs. Offline suite verified: **513 passed, 2 skipped**.

## Prior-review follow-up

The previous review's major finding (corpus-wide destructive execute with no confirmation) is **fixed in HEAD**: execution now requires `--yes` (`cli.py:279-289`), the refusal is tested (`test_cli_execute_requires_yes` asserts rc=1 and no mutation), and the deferred stub-over-real shard-tag refusal is explicitly recorded in the plan with its rationale — a flagged deferral, not a silent gap.

## Suspicions I chased that dissolved

- `prev[0]["fps"]` is safe: `role_provenance` is an `INSERT OR REPLACE` upsert keyed `(video_id, role)`, so `pv.get(vid, role)` returns at most one row.
- The batch-pinned config restamp is correct in both drift directions, and its test genuinely exercises the pin (the `from va.provenance import role_fingerprint` inside `execute_reprocess` binds at call time, after the monkeypatch).
- The write/swap ordering claims hold against `sharded.py`'s mtime-keyed cache: a torn pair during the visual two-file swap is cached only under the *old* `.npz` mtime, so the `.npz` replace self-heals it — transient, and honestly disclosed to the web agent in COORDINATION.md.
- `reindex_visual` payloads (`video_id`/`timestamp`/`source_uri`) exactly match ingest's (`ingest.py:294-296`); the `vectors_rebuild.npz` temp can't match the reader glob `*/vectors.npz`; the `va reingest … --fps` pointer matches a real flag (`cli.py:520`).
- The failure-path tests are real: `test_failed_text_rebuild_preserves_the_old_shard` and `test_persist_writes_payloads_before_vectors` both fail against the pre-fix code (old order was unlink-then-embed / npz-then-json).
- Plan conformance: RPRC-3's XOR scope, rows-then-provenance ordering, resumability, and the skip→`va reingest` fallback are all implemented; RPRC-1c/RPRC-2 deferrals are recorded, and deferring dependency invalidation is safe since neither wired role is a source in the R1→R4/5/6/7, R5→R6, R8→R9 graph.
- Docs parity: CLAUDE.md command line (incl. `--yes`/`--dry-run`), four COORDINATION.md entries (incl. the concurrency heads-up superseding the RPRC-1a framing), plan status entries. The two finalized commit subjects are plainly descriptive; HEAD is a provisional `need_agent_review:` subject (exempt).

## Finding

**MINOR — `src/va/pipeline/text_index.py:85-93` — the text rebuild's crash window can still destroy the old shard, unlike the visual path's temp+swap.**

`index_text` now embeds before touching the shard (good — an embed *exception* leaves the old shard intact), but it still unlinks `text_vectors.{npz,json}` and then rebuilds in place. A hard failure between the unlink and `persist()` — disk-full during `np.savez`, or the process killed mid-reprocess — destroys the old shard with no replacement. The role stays stale (no restamp), so a retry rebuilds it, but the video's text search is silently gone until that retry; the COORDINATION.md RPRC-1a entry tells the web agent "a rebuild that fails … leaves the prior shard intact," which holds only for failures before the unlink. Safe path: use the same build-to-temp + `os.replace` swap `reindex_visual` already implements (json first, npz last), which closes the window for free and makes the two shard writers consistent.

## Verdict

**approve** — the one finding is a narrow durability gap on an already-resumable path; everything else is careful, well-ordered, and genuinely tested.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/text_index.py", "line": 85, "issue": "index_text still unlinks the old text_vectors shard before writing the new one, so a hard failure (disk-full in np.savez, process kill) between unlink and persist destroys the shard — the visual path's temp+swap pattern was not applied to the text path, and COORDINATION.md's 'failed rebuild leaves the prior shard intact' claim only holds for failures before the unlink.", "scenario": "During `va reprocess --all-stale --yes`, the disk fills (or the process is killed) while np.savez is writing text_vectors.npz after the old pair was unlinked: the video's text search goes silently empty until a retry succeeds; the role stays stale so it is recoverable, but the advertised leave-old-shard-intact guarantee is violated. Fix: build to a temp shard and os.replace-swap json-then-npz, as reindex_visual already does."}]}
```
