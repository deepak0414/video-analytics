# Agent review — approve

date: 2026-08-11T13:47:09.101726
range: origin/main..HEAD
branch: fix/nvr-lighting-independent-verify
findings: 0


---

## Full review

## Review — `fix/nvr-lighting-independent-verify` (origin/main..627a1ee, round 4)

**Context.** Round 3 (reviews/20260811-133749, verdict approve) left exactly one minor: the production default-refs derivation (`ReferenceLibrary(out_mp4.parent.parent / "nvr_refs")`) had zero coverage because every test injected a library. The delta since that reviewed commit (8c595b2..HEAD) is 23 lines, all in `tests/test_nvr_source.py` — the single new test `test_the_default_library_lives_beside_the_cache_in_the_workdir`, which is the exact safe path that finding named.

**The round-3 minor is genuinely closed.** The new test constructs its scenario for real (the 2026-08-08 "a test that never constructs its scenario is decoration" lesson): it uses the `_pull_harness` only for device stubs, deliberately discards the harness's injected library, and calls `_pull_window(3, t0, t1, out)` in the production 4-arg shape so the default derivation actually runs. The assertion pins `<workdir>/nvr_refs/ch3.json` specifically — if the derivation were `out_mp4.parent / "nvr_refs"` (inside the transient `cache/`), or if a cache-layout refactor added a subdirectory level, the assert fails. That is precisely the silent-relocation mode the finding worried about.

**Independent re-verification on HEAD (not just delta-diffing).** Suspicions I chased that dissolved:

- `out_mp4.parent.parent` = workdir: `ingest.py:265` passes `ws.cache`, `pipeline/paths.py:32` defines it as `<workdir>/cache`. Holds.
- The CLAUDE.md "Known interaction" claim that a refused fail-closed pull *holds* the watcher watermark (rather than advancing past and losing the episode): `pipeline/watch.py:222-229` catches the ingest exception, warns, and `break`s without advancing. The documented self-heal story matches the code.
- Seed/verify mismatch: the identity gate checks the whole-window consensus while the seed comes from the clean run's consensus — but the clean run *is* the majority `longest_clean_run` measures against, so the two hashes coincide on any pull that passes; no admissible input separates them.
- `first_sight = best is None` can't misfire on the new-mode path: `accepts` returns `best=None` only when the library is empty, and the snapshot branch is reachable only when it isn't.
- Refusal paths never touch the library (`refs.add` is after the atomic rename), undecodable frames are `None` (excluded from consensus, forced-dirty via `UNDECODABLE`, not counted toward the ≥3 floor), and `add()`/`load()` both swallow filesystem failure — each pinned by a test that constructs its failure.
- Removed symbols (`chunk_bounds`, `CHUNK_S`, `_frame_hammings`, `_reference_hash`, `_pull_chunk_verified`) have no remaining source/test references (only stale `__pycache__` binaries). All `_pull_window` doubles (test_watch.py:47, test_nvr_source.py:189/282) still match the call shape since `refs` is defaulted and both production call sites pass 4 positional args — the lambda-doubles lesson is honored.
- Determinism-vs-correctness and doc parity were satisfied in rounds 1–3 and are unchanged: ground-truth A/B measurements (10/10 purity 1.000 vs 2 dirty chunks in 300; night 2→34 frames live before/after) are recorded in the module docstring and COORDINATION.md, and `nvr_refs/`, the admission/refusal semantics, first-sight seeding, and the watcher interaction are all documented in this same change.

**Caveat:** I could not execute the suite (pytest denied in this session, same as rounds 2–3), so the new test is assessed by reading; CI's `offline-tests` gate will run it. Its logic traces cleanly against the harness stubs (first-sight on empty ch3, snapshot never needed, trim stub writes the output, `add()` writes the real file).

No new findings. The delta does exactly one thing — close the last open finding with the named safe path — and does it properly.

**Verdict: approve.**

```json
{"verdict": "approve", "findings": []}
```
