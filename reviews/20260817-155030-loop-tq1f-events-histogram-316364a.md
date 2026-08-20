# Agent review — request_changes

date: 2026-08-17T15:53:27.284022
range: origin/main..HEAD
branch: loop/tq1f-events-histogram
findings: 1

- **major** `src/va/pipeline/aggregate.py:611` — timeline_histogram sizes the bucket list with the integer ceiling idiom `int((t1 - t0 + width - 1.0) // width)`, which under-allocates for fractional-second window spans, so a valid in-window entity indexes past the list and raises IndexError.
  - scenario: TimeWindow 2026-08-11T00:00:00 to 00:00:10.500000 (tz=UTC) with bucket='10s' yields n_buckets=1 (true ceiling 2); an entity starting at t0+10.2 — inside the half-open window — computes bucket index 1 and crashes with IndexError (reproduced against the branch). Fix: n_buckets = math.ceil((t1 - t0) / width), plus a fractional-span test that fails on the old form.

---

## Full review

Review complete. I read the full branch diff (6 commits: contracts, resolve_category seam, select_placed/select_tracks, resolve_identities, windowed count_objects, list_events + timeline_histogram), verified the referenced pre-existing surfaces (`Catalog.footage_domains`, `MODALITY_OBJECT_COUNT`, `timeline.py` primitives) exist, and checked the arithmetic, boundary semantics, caveat plumbing, and plan/COORDINATION conformance.

## What holds up

- The epoch discipline is solid end-to-end: Python-computed numeric bounds, the TypeError guard in `select_placed`, and a regression test that demonstrates the `strftime` TEXT false-0 on the very fixture (`test_strftime_text_bound_is_the_false_zero_bug`).
- Ground truth is genuinely hand-derived (epoch worksheets independent of zoneinfo), boundary semantics (half-open, tz-load-bearing, NULL-epoch skip, `cameras=[]` vs `None`) are each pinned by a test that constructs its scenario.
- The seams are content-free as the plan demands; provenance reports what ran, not what was requested; caveats travel with every count; COORDINATION.md logs every new surface; parity between `_classes` and `resolve_category` is pinned with hand-written literals rather than tautological equality.
- Cross-op consistency (list rows == count evidence, histogram sums == count) is tested through the single shared selection path.

## The one defect (confirmed by reproduction)

**`timeline_histogram` crashes with IndexError on a window whose span is not a whole number of seconds** (`src/va/pipeline/aggregate.py:611`). The bucket-count ceiling uses the integer idiom `(span + width - 1) // width`, which is only a ceiling for integer operands — `TimeWindow` start/end are datetimes that can carry microseconds (an LLM-filled ISO timestamp with fractional seconds, or a "now"-derived end). I reproduced it: window `00:00:00 → 00:00:10.5`, bucket `"10s"` → `n_buckets=1` (true ceiling 2), and an in-window entity at t0+10.2 indexes `counts[1]` → `IndexError: list index out of range`. The same family hits `span=0.5s, bucket="1s"` → `n_buckets=0` with an in-window entity. All existing tests use whole-second spans, so the suite can't see it. Safe path: `n_buckets = math.ceil((t1 - t0) / width)` (with the existing `t1 > t0` guard), plus a test with a fractional-second window that fails on the old form. Note `int((e.first_seen_epoch - t0) // width)` then needs no change — with a true ceiling the max index is always in range.

Everything else I chased dissolved on inspection: the PEP-495 gap check is correct for ambiguous fall-back times, `footage_domains` is reused per its done-only convention, the `count_objects` name collision is deliberate and logged, the `"glass"→"gla"` rstrip quirk is pre-existing behavior explicitly pinned as such, and the flagged heuristics (`min_frames=2`, `"1h"`, 10k cap) are named and disclosed per the CLAUDE.md rule.

Verdict: request_changes — one major correctness bug, small fix.

```json
{"verdict": "request_changes", "findings": [{"severity": "major", "file": "src/va/pipeline/aggregate.py", "line": 611, "issue": "timeline_histogram sizes the bucket list with the integer ceiling idiom `int((t1 - t0 + width - 1.0) // width)`, which under-allocates for fractional-second window spans, so a valid in-window entity indexes past the list and raises IndexError.", "scenario": "TimeWindow 2026-08-11T00:00:00 to 00:00:10.500000 (tz=UTC) with bucket='10s' yields n_buckets=1 (true ceiling 2); an entity starting at t0+10.2 — inside the half-open window — computes bucket index 1 and crashes with IndexError (reproduced against the branch). Fix: n_buckets = math.ceil((t1 - t0) / width), plus a fractional-span test that fails on the old form."}]}
```
