# Agent review — approve

date: 2026-07-31T12:38:29.503575
range: origin/main..HEAD
branch: feature/batch-reprocess
findings: 2

- **minor** `src/va/storage/vector/numpy_flat.py:27` — swap_shard's two-file replace window lets a concurrent reader load new .json + old .npz, and _load has no payload/vector count check — a torn read can return silently misaligned search hits, not just a transient error as documented.
  - scenario: va serve query races a text_vectors swap after a caption reprocess changed row count: reader stats the old .npz between the two os.replace calls, loads old vectors with new payloads, and one query returns hits whose payload text/timestamps belong to different vectors (or raises IndexError); it self-heals on the next query. Safe path: reject/skip (and don't cache) a shard where len(payloads) != vectors.shape[0].
- **minor** `provenance-reprocess-plan.md:76` — RPRC-1 is declared COMPLETE while its 'give each embedder a model_id property' sub-item is neither implemented nor recorded as deferred (text_index.py:72 still cites it as an RPRC-1 follow-up).
  - scenario: A later session reads 'RPRC-1 COMPLETE', assumes injected embedders carry exact model_id tags, and relies on shard tags that are actually 'unknown' for any injected embedder. Safe path: add a one-line deferral note to the plan status (or add the property).

---

## Full review

Review complete. I read the full diff (4 commits: `va reprocess` plan front-end, executor + text_embedder, visual_embedder, vlm_captioner), the supporting code it calls (`stale.py`, `text_index.py`, `numpy_flat.py`, `sharded.py`, `provenance_store.py`, `manage.py`, `paths.py`, `frames.py`, ingest's captioner/embedder call sites), the new tests, and the plan/COORDINATION updates.

**Summary: this is a clean change — verdict approve, with two minor findings.**

What I verified and found sound:

- **Scope/selection logic** (`plan_reprocess`): the XOR scope check, role validation (delegated to `stale_report`), non-done `--video` error, and ident resolution all behave correctly; each has a test, including the role×video intersection guard.
- **Executor safety invariant**: rows-first-provenance-second holds; a raising reprocessor lands in `failed` with no restamp (tested, including the removed-video race where `backfill_text_index` returning `None` is converted to a failure rather than a 0-row "success"); the pinned-config restamp is tested via monkeypatch, and the mid-batch-edit consequence is correctly a false-stale, never a missed-stale.
- **`caption(kf)` with a list is correct** — `VLMCaptioner.caption` takes `Sequence[Image.Image]`, identical to ingest's call; `keyframes_for_spans` returns exactly one list per span, so the `zip` can't silently drop segments.
- **fps preservation**: visual re-embed refuses an unknown recorded fps (tested); the skip pointer carries `--fps` (tested) and `va reingest --fps` exists.
- **Durability**: temp-build + `swap_shard` is tested for embed failure, persist failure (mocked `np.savez` disk-full), and the zero-frame corrupt-media case; the old shard survives byte-identically in each.
- **Docs/contract**: the new command, flags, and both write-path behavior changes (including the `index_text` change that affects *ingest*, not just reprocess) are logged in CLAUDE.md and COORDINATION.md with web-agent callouts. Commit messages are plain-description-first with plan IDs trailing; the head `need_agent_review:` subject is exempt.

The two findings:

1. **Minor — torn-pair race is "silently wrong", not just "a transient error"** (`src/va/storage/vector/numpy_flat.py:19-28`). Between `swap_shard`'s two `os.replace` calls, a `va serve` reader can load the *new* `.json` with the *old* `.npz`. `_load` has no payload-count/vector-count consistency check, so if the counts differ (a text rebuild after caption changes), `search()` either raises IndexError or — worse — returns hits with misaligned payloads (wrong timestamps/text) for that one query. It does self-heal (cached under the old mtime, invalidated when the `.npz` replace lands), but the docstring's "old pair or the fully-new pair" claim and COORDINATION's "at worst a transient error" undersell the silent-wrong-result variant. Safe path: have `_load` (or `_load_shard`) verify `len(payloads) == vectors.shape[0]` and treat a mismatch as shard-not-ready — skip and don't cache — which converts the race into the already-documented skip behavior.

2. **Minor — RPRC-1 declared COMPLETE with one sub-item silently dropped** (`provenance-reprocess-plan.md:74-77`). RPRC-1's spec includes "give each embedder a `model_id` property so a reprocess shard is tagged with the embedder it actually used"; that wasn't built (the normal config path tags exactly, so this only affects injected embedders, which still tag `unknown`), and `text_index.py:72` still points at it as "a follow-up (RPRC-1)". The RPRC-1c status entry says "RPRC-1 COMPLETE" without recording this as deferred. Safe path: add one line to the plan status recording the deferral (or implement the property) so a later reader doesn't assume it exists.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/storage/vector/numpy_flat.py", "line": 27, "issue": "swap_shard's two-file replace window lets a concurrent reader load new .json + old .npz, and _load has no payload/vector count check — a torn read can return silently misaligned search hits, not just a transient error as documented.", "scenario": "va serve query races a text_vectors swap after a caption reprocess changed row count: reader stats the old .npz between the two os.replace calls, loads old vectors with new payloads, and one query returns hits whose payload text/timestamps belong to different vectors (or raises IndexError); it self-heals on the next query. Safe path: reject/skip (and don't cache) a shard where len(payloads) != vectors.shape[0]."}, {"severity": "minor", "file": "provenance-reprocess-plan.md", "line": 76, "issue": "RPRC-1 is declared COMPLETE while its 'give each embedder a model_id property' sub-item is neither implemented nor recorded as deferred (text_index.py:72 still cites it as an RPRC-1 follow-up).", "scenario": "A later session reads 'RPRC-1 COMPLETE', assumes injected embedders carry exact model_id tags, and relies on shard tags that are actually 'unknown' for any injected embedder. Safe path: add a one-line deferral note to the plan status (or add the property)."}]}
```
