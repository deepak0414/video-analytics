# Agent review — approve

date: 2026-08-11T13:41:06.376025
range: origin/main..HEAD
branch: fix/nvr-lighting-independent-verify
findings: 1

- **minor** `src/va/sources/nvr.py:424` — The production default-refs path (ReferenceLibrary(out_mp4.parent.parent / 'nvr_refs')) has zero test coverage — every test injects a library, so nothing pins where the reference library actually lives.
  - scenario: A future change to the cache layout (e.g. per-video cache subdirs) silently relocates nvr_refs; every channel resets to unverified first-sight trust and pulls keep succeeding, so the identity gate weakens with no visible failure. Fix: one test through fetch() or a refs-omitted _pull_window asserting the library lands at <workdir>/nvr_refs/ch<N>.json.

---

## Full review

Static review is complete. I could not execute the test suite (Bash approval denied for pytest in this session, same as round 2), so tests are assessed by reading; CI's `offline-tests` gate will run them.

## Review — `fix/nvr-lighting-independent-verify` (origin/main..8c595b2, round 3)

**Context.** This is round 3 for this branch. Round 2 (reviews/20260811-132934, verdict approve) left two minor findings; the delta since that reviewed commit (d7ac4f7..HEAD) addresses both exactly:

1. **Round-2 minor 1 (unguarded `refs.add()` after the clip landed)** — fixed at `src/va/sources/nvr.py:183-196`: the mkdir/write/replace sequence is wrapped in `try/except OSError` with a warning, matching `load()`'s swallow-corruption philosophy. Pinned by the new `test_an_unwritable_library_never_fails_a_finished_pull` (tests/test_nvr_source.py:629), which really constructs the failure (a file where the library dir should go makes `mkdir` raise `FileExistsError`, an `OSError` subclass) and asserts both no-raise and the channel staying unseeded. `json.dumps`/`np` in the guarded block can't raise anything the except misses on these inputs.

2. **Round-2 minor 2 (undocumented watcher head-of-line blocking on a fail-closed refusal)** — fixed in CLAUDE.md's `va watch` entry (the "Known interaction" block): refused never-seeded-mode backfill wedges the watermark, queues later episodes ≤ ~12 h until lighting rotates, one burned device pull per cycle, loss only past the ~6-day ring. That was the finding's named safe path.

**Round-1/round-2 verifications re-confirmed on HEAD** (spot-checked, not re-litigated): seeding only after verification + atomic rename and from the clean run's consensus; undecodable frames are `None` (excluded from consensus, forced-`UNDECODABLE` in self-distances, not counted toward the ≥3-decodable floor); the night-on-day-seeded-channel regression is pinned through the production `_pull_window` path.

**Suspicions I chased this round that dissolved:**

- `out_mp4.parent.parent / "nvr_refs"` — `ingest.py:265` passes `ws.cache`, and `pipeline/paths.py:32` defines that as `<workdir>/cache`, so the grandparent is the workdir. Holds.
- The widened `_pull_window(..., refs=None)` vs its test doubles — `test_watch.py:43` and both `test_nvr_source.py` doubles take 4 args; both production call sites pass 4 positional. The CLAUDE.md lambda-double lesson is honored.
- Legitimate in-frame motion tripping the self-consistency trim (a close subject flipping >18 dhash bits mid-clip would split the clean run) — same threshold and `longest_clean_run` as the shipped recipe, and self-consensus is strictly less likely to false-trim than the old live-snapshot reference; live before/after numbers (night 2→34 frames, day 45→73) back this. Not a regression of this branch.
- A stray `.json.tmp` left if `write_text` succeeds but `os.replace` fails — same fixed name is overwritten on the next attempt, inside a rebuildable cache dir. Not reporting.
- Determinism-vs-correctness and documentation parity: ground-truth measurements (310-pull A/B, purity numbers, live before/after) are in the module docstring and COORDINATION.md; `nvr_refs/`, admission/refusal semantics, first-sight seeding, and the watcher interaction are all documented in this same change. Satisfied.

**One new minor finding:**

- **minor — `src/va/sources/nvr.py:424`** — the default `refs` derivation (`ReferenceLibrary(out_mp4.parent.parent / "nvr_refs")`) is the path production always takes (`fetch()` never passes `refs`), but no test exercises it: the `_pull_harness` always injects a library. If a future change moves the cache dir (e.g. per-video cache subdirs), `nvr_refs` silently relocates, every channel resets to unverified first-sight trust, and **pulls keep succeeding** — the identity gate weakens with no visible failure, which is precisely the silent mode the "BESIDE the cache" comment worries about. Safe path: one test through `fetch()` (or `_pull_window` with `refs` omitted) asserting the library file lands at `<workdir>/nvr_refs/ch<N>.json`.

Both prior minors are genuinely closed with the named safe paths, the new guard's test constructs its scenario for real, and the docs moved in the same change. The one new finding is a coverage pin, not a defect in current behavior.

**Verdict: approve.**

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "src/va/sources/nvr.py", "line": 424, "issue": "The production default-refs path (ReferenceLibrary(out_mp4.parent.parent / 'nvr_refs')) has zero test coverage — every test injects a library, so nothing pins where the reference library actually lives.", "scenario": "A future change to the cache layout (e.g. per-video cache subdirs) silently relocates nvr_refs; every channel resets to unverified first-sight trust and pulls keep succeeding, so the identity gate weakens with no visible failure. Fix: one test through fetch() or a refs-omitted _pull_window asserting the library lands at <workdir>/nvr_refs/ch<N>.json."}
]}
```
