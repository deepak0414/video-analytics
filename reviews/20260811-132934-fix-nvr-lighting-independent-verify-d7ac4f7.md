# Agent review — approve

date: 2026-08-11T13:34:35.999175
range: origin/main..HEAD
branch: fix/nvr-lighting-independent-verify
findings: 2

- **minor** `src/va/sources/nvr.py:475` — refs.add() is unguarded after os.replace already landed the clip, so a failed reference-library write aborts an ingest whose verified output is already cached — contradicting the library's own 'a cache must not break a pull' contract that load() honors.
  - scenario: First-sight pull succeeds; mkdir/write_text on <workdir>/nvr_refs fails (read-only subtree or ENOSPC) and leaves a stray .json.tmp; _pull_window raises and the ingest is marked failed; the retry trusts the cached mp4 and succeeds without ever seeding the channel — one spurious failed ingest per occurrence. Fix: wrap the add() in try/except + warning.
- **minor** `src/va/sources/nvr.py:434` — The fail-closed refusal combined with the watcher's hold-watermark-and-retry policy (watch.py:222) creates undocumented head-of-line blocking: a refused never-seeded-mode backfill episode wedges the camera's watermark, queueing all later episodes and re-pulling the refused window every cycle until the live lighting rotates to match.
  - scenario: Watcher catches up last night's episodes at noon on a day-seeded channel (the driving 24h/6-camera backfill use-case): night episodes refuse for up to ~12 h, day episodes queue behind them, one full device pull is burned per cycle, and the operator sees only generic 'ingest failed — watermark held' warnings with no recovery guidance. Self-heals at nightfall; loss only if wedged past the ~6-day ring. Fix: document the interaction in the watch SLA docs and surface the refusal's recovery guidance in the watcher log.

---

## Full review

Static review is complete; I could not execute the test suite (Bash approval denied in this session), so test results are assessed by reading, not running. Here is my review.

## Review — `fix/nvr-lighting-independent-verify` (origin/main..d7ac4f7)

**Context.** This is round 2 for this branch. I re-checked all four round-1 findings against the current code and they are genuinely addressed, each with a test through the production `_pull_window` path rather than hand-built state: (1) the new-lighting-mode seeding path now exists via the live-snapshot admission gate (`nvr.py:416-443`, pinned by `test_the_regression_a_night_pull_on_a_day_seeded_channel_verifies`); (2) that test constructs the scenario through `_pull_window` itself; (3) the all-zeros sentinel is gone — undecodable frames are `None`, excluded from consensus, forced to `UNDECODABLE` in self-distances, and not counted toward the ≥3-decodable floor (pinned by the two undecodable-frame tests); (4) seeding happens only after verification and the atomic rename, and from the clean run's consensus (pinned by `test_a_failed_first_pull_does_not_poison_the_channel` and `test_the_seed_comes_from_the_clean_run_not_the_contaminated_window`).

**Suspicions I chased that dissolved:**
- `out_mp4.parent.parent / "nvr_refs"` workdir assumption — `ingest.py:265` passes `ws.cache` (`<workdir>/cache`), so the grandparent is the workdir. Holds.
- Widening `_pull_window` with the `refs=None` default vs its test doubles — all four doubles (`test_watch.py:43`, `test_nvr_source.py:184/279/184-region`) take 4 positional args, and both production call sites pass 4. The CLAUDE.md lambda-double lesson was explicitly honored.
- `MAX_TRIES` now retries only transport failures, not verification failures (the old per-chunk recipe retried unverifiable chunks). A no-clean-run window now fails without a re-request — but the trim absorbs the measured contamination mode (short lead-in), the 310-pull measurement saw zero whole-window contamination, and the watcher retries the episode next cycle anyway. Not a defect.
- Stale lead-in dragging the whole-window consensus past the library/snapshot gates before trimming — bitwise majority voting makes a minority lead-in unable to move the consensus; a ~50/50 split fails closed, which is the right direction. Not reporting.
- Determinism-vs-correctness: the design change is backed by ground-truth measurement (310 pulls, purity numbers, before/after live counts) recorded in the module docstring and COORDINATION.md. Satisfied.
- Documentation parity: `nvr_refs/`, the admission/refusal semantics, and the first-sight seeding are documented in CLAUDE.md and logged in COORDINATION.md in this same change; no new env vars/flags/config keys. Satisfied.

**Findings (both minor):**

1. **minor — `src/va/sources/nvr.py:475`** — `refs.add()` runs unguarded after the clip has already landed via `os.replace`, so a failed library *write* (read-only/`ENOSPC` on `<workdir>/nvr_refs`, which also leaves a stray `.json.tmp`) raises out of `_pull_window` and fails an ingest whose verified output is already in cache. This contradicts the class's own contract that the library "is a CACHE... must not break a pull" — `load()` swallows corruption but `add()` can abort. Scenario: first-sight pull succeeds, `mkdir`/`write_text` fails, ingest is marked failed; the retry then trusts the cached mp4 and succeeds without ever seeding — one spurious failed ingest and an unseeded channel. Safe path: wrap the `add()` call (or its body) in try/except with a warning, matching `load()`'s philosophy.

2. **minor — `src/va/sources/nvr.py:434` (interaction with `src/va/pipeline/watch.py:222`)** — the new fail-closed refusal interacts with the watcher's hold-the-watermark-and-retry policy to produce hours of head-of-line blocking that is documented nowhere: a backfill episode in a never-seeded lighting mode (e.g. last night's episodes caught up at noon on a day-seeded channel — exactly the user's 24h/6-camera backfill use-case) refuses every cycle, wedging that camera's watermark so all subsequent (day) episodes queue behind it until the live scene rotates back to matching (~up to 12 h), with a full device pull + verification burned per retry per cycle. It self-heals within the ~6-day ring, so footage loss needs a >6-day wedge, but the operator only sees generic "ingest failed — watermark held" warnings. Safe path: document the interaction where the watcher SLA is documented (CLAUDE.md watch entry or the loop plan), and/or have the watcher log the refusal's recovery guidance distinctly rather than a generic retry warning.

Neither finding is major: both are bounded, fail-closed, and recoverable. The core rewrite is correct by my reading, the test additions are real behavior pins, and the round-1 lifecycle worked as intended.

**Verdict: approve.**

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "src/va/sources/nvr.py", "line": 475, "issue": "refs.add() is unguarded after os.replace already landed the clip, so a failed reference-library write aborts an ingest whose verified output is already cached — contradicting the library's own 'a cache must not break a pull' contract that load() honors.", "scenario": "First-sight pull succeeds; mkdir/write_text on <workdir>/nvr_refs fails (read-only subtree or ENOSPC) and leaves a stray .json.tmp; _pull_window raises and the ingest is marked failed; the retry trusts the cached mp4 and succeeds without ever seeding the channel — one spurious failed ingest per occurrence. Fix: wrap the add() in try/except + warning."},
{"severity": "minor", "file": "src/va/sources/nvr.py", "line": 434, "issue": "The fail-closed refusal combined with the watcher's hold-watermark-and-retry policy (watch.py:222) creates undocumented head-of-line blocking: a refused never-seeded-mode backfill episode wedges the camera's watermark, queueing all later episodes and re-pulling the refused window every cycle until the live lighting rotates to match.", "scenario": "Watcher catches up last night's episodes at noon on a day-seeded channel (the driving 24h/6-camera backfill use-case): night episodes refuse for up to ~12 h, day episodes queue behind them, one full device pull is burned per cycle, and the operator sees only generic 'ingest failed — watermark held' warnings with no recovery guidance. Self-heals at nightfall; loss only if wedged past the ~6-day ring. Fix: document the interaction in the watch SLA docs and surface the refusal's recovery guidance in the watcher log."}
]}
```
