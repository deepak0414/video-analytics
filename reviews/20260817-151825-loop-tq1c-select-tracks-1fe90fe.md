# Agent review — approve

date: 2026-08-17T15:21:57.770645
range: origin/main..HEAD
branch: loop/tq1c-select-tracks
findings: 1

- **minor** `src/va/storage/structured/tracks.py:122` — select_placed treats cameras=[] as 'all cameras' (falsy guard) while classes=[] means 'no rows' — an empty computed camera subset silently returns the unfiltered count instead of 0.
  - scenario: A TQ1.h planner (or any caller) filters the camera set by some predicate, gets [], and passes it through: count_objects reports the full per-window total across every camera rather than 0, a silently-inflated number in the exact anti-hallucination path this tier exists to protect. Fix: `if cameras is not None and not cameras: return []`, keep None = all, add a pinning test.

---

## Full review

## Review: origin/main..HEAD (TQ1.a–TQ1.c, typed-query tier)

Three stacked `need_agent_review:` commits: aggregation contracts (`contracts/aggregate.py`), the `resolve_category` seam promotion, and windowed track selection (`TrackStore.select_placed` + `pipeline.aggregate.select_tracks`). I verified the substantive claims independently rather than trusting the docstrings:

**Verified sound:**
- **Epoch ground truth** — recomputed W0=1786431600, W1=1786474800, and the Jan-11 PST bound (1768118400) with zoneinfo; all match the hand-derived worksheet values pinned in the tests, and the worksheet's arithmetic (leap days, day counts) is itself correct.
- **DST logic** — the PEP-495 roundtrip check correctly detects the 2026-03-08 02:30 spring-forward gap and correctly accepts the ambiguous 2026-11-01 01:30 fall-back time (both confirmed by execution).
- **The strftime false-0 claim** — confirmed in SQLite: `strftime('%s',...)` is `typeof` text, and `numeric >= text` is always 0. The regression test constructs the broken form on the same fixture where the numeric form finds 4 rows — it genuinely demonstrates the bug it pins, satisfying the "fails on the old code" lesson.
- **Parity** — `resolve_category` output matches `_classes` on every table row I re-ran (including the `"glass"→"gla"` quirk, which is pre-existing behavior, not new); `_classes` delegation means `va objects`/`va count` behavior is unchanged. The parity table pins literals by hand, avoiding the tautology the TQ1.b r1 review flagged.
- **Storage/contract fit** — `videos.start_epoch` is REAL (schema v5), so the inlined `start_epoch + first_seen` SQL compares number-to-number; `NULL`-epoch A-EV skip, half-open-end, camera filter, and tz-changes-the-set assertions all check out against the hand-derived fixture (I recomputed the UTC-window membership set independently: {e1, a1, a2, c1} is correct).
- **Process** — COORDINATION.md logs all three interface additions; `contracts/`-is-human-reviewed and `pipeline/`-is-golden-verified label needs are recorded in the branch registry; changes are additive with no schema migration needed.

I considered and dropped two suspicions: the "reuse `timeline.py`" plan-item deviation is explicitly documented with a valid rationale in the `select_tracks` docstring (and `wallclock_to_chunks` is closed at t1, so reusing it would actually have broken the half-open end semantics the tests pin); the fixture's `Catalog.upsert` path round-trips `camera_id`/`start_epoch` correctly.

**One finding survives:**

The empty-`cameras` edge in `select_placed` (src/va/storage/structured/tracks.py:122): `classes=[]` returns no rows (line 108), but `cameras=[]` falls through `if cameras:` and silently means **all** cameras. A future caller (the TQ1.h planner is the obvious one) that computes a camera subset and gets an empty list will receive the full-corpus window count instead of 0 — a silently-inflated number, which is precisely the failure family this tier exists to prevent. Safe path: treat `cameras == []` as an empty selection (return `[]`) mirroring the classes handling, keep `None` = all, and pin it with one test. Minor because no current caller can produce it, but it should be fixed before TQ1.e/TQ1.h compose on top.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/storage/structured/tracks.py", "line": 122, "issue": "select_placed treats cameras=[] as 'all cameras' (falsy guard) while classes=[] means 'no rows' — an empty computed camera subset silently returns the unfiltered count instead of 0.", "scenario": "A TQ1.h planner (or any caller) filters the camera set by some predicate, gets [], and passes it through: count_objects reports the full per-window total across every camera rather than 0, a silently-inflated number in the exact anti-hallucination path this tier exists to protect. Fix: `if cameras is not None and not cameras: return []`, keep None = all, add a pinning test."}]}
```
