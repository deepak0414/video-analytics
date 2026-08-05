# Agent review — approve

date: 2026-08-05T12:09:03.721973
range: origin/main..HEAD
branch: loop/ws4d-appearance-store
findings: 0


---

## Full review

The tests assert on stored state, not call counts, so the batching change doesn't invalidate them. The delta since the approved review touched only `_capture_appearance` in `ingest.py` — nothing else in the range moved.

## Review: `origin/main..HEAD` (WS4.b + WS4.c + WS4.d, round 3 at `0cbb6e9`)

WS4.b (`f811d2b`) and WS4.c (`9f8fae0`) were approved at these exact SHAs in prior reviews, and the full WS4.d range was deep-reviewed twice (request_changes at `e6bd406`, approve-with-one-minor at `cd416be`). The only change since the approved round is the fix for that one remaining minor finding, so I focused on verifying that fix rather than re-plowing verified ground.

**The batching fix is correct and complete.** `_capture_appearance` now iterates `_batched(todo, _BATCH)` (the same 32-image batch size as the frame path), opens crops per batch instead of all at once, and calls `store.add()` per chunk (`src/va/pipeline/ingest.py:127-129`). I verified against `numpy_flat.py` that this is sound:

- `add()` accumulates in memory (`np.vstack` + `payloads.extend`) with a per-call length check, so vectors and payloads stay row-aligned across multiple calls; `persist()` writes once at the end — identical on-disk result to the single-call version.
- A mid-batch failure (the OOM case the finding was about) now costs only that video's refs, consistently: the exception propagates before `persist()`, so no partial `appearance.npz` lands on disk, the best-effort catch in the Role-6 block keeps the tracks (with NULL `appearance_ref`), and ingest continues. No dangling refs are possible because `ref_by_track` is only applied on the success path.
- The trace line was correctly updated from `len(crops)` to `len(todo)`.

**Re-confirmed from the prior rounds (spot checks, not re-derived):** the bytetrack `det_idx` passthrough and its regression test (`test_tracker_passthrough.py`) are unchanged; the crop spill-to-disk and `rmtree` cleanup are unchanged; schema v6 migration assert holds; the `test_appearance_store.py` assertions (refs resolvable, frame store untouched, disabled-tracker purge) still describe the batched code accurately. No disputes in `workflow-trust-plan.md` touch this range. A pytest run was live (PID 1634545), so per the repo lesson I did not launch another; the delta is small enough that reading the tests against the change suffices.

All three round-1 findings and the round-2 minor are now resolved. No new findings.

Verdict: **approve**.

```json
{"verdict": "approve", "findings": []}
```
