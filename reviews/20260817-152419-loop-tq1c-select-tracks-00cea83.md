# Agent review — approve

date: 2026-08-17T15:28:15.793937
range: origin/main..HEAD
branch: loop/tq1c-select-tracks
findings: 1

- **minor** `src/va/storage/structured/tracks.py:122` — Window membership is by track start only (deliberate, documented), but neither the plan's canonical caveat list (§11) nor any planned CountResult caveat discloses that tracks already underway at window start are excluded — an incomplete method disclosure under the repo's caveats-travel-with-the-number rule.
  - scenario: An NVR chunk straddles local midnight; a car's track starts 23:58:30 and persists to 00:00:20. 'How many cars 00:00–12:00' excludes it. When TQ1.e composes CountResult from select_placed, the caveats field says parked/crossed/no-ReID but not the boundary-exclusion semantic, so the count ships with an incomplete method disclosure. Fix: add an 'arrivals-within-window; tracks underway at window start excluded' caveat string to the TQ1.e caveat set (or plan §11) so the disclosure travels with the number.

---

## Full review

All verification is complete. Here is my review.

## Review: origin/main..HEAD (TQ1.a–TQ1.c, typed-query tier — r2 for TQ1.c)

Three stacked `need_agent_review:` commits: aggregation contracts (`contracts/aggregate.py`), the `resolve_category` seam promotion, and windowed track selection (`TrackStore.select_placed` + `pipeline.aggregate.select_tracks`). This branch was already reviewed once at `1fe90fe` (r1, approve + 1 minor); the only delta since is the fix for that finding, so I focused on independently re-verifying the load-bearing claims and on the fix itself.

**The r1 finding is properly fixed.** `1fe90fe..HEAD` is exactly the recommended safe path: `cameras=[]` now returns no rows (mirroring `classes=[]`), `None` keeps meaning "no restriction", and `test_empty_camera_subset_means_no_rows_not_all_cameras` pins both halves. That test fails on the pre-fix code by construction (the pre-fix guard was the falsy `if cameras:` fall-through), satisfying the fails-first lesson.

**Independently verified sound (not taken on trust from r1 or the docstrings):**

- **Epoch worksheet arithmetic** — recomputed by hand: 2020-01-01=1577836800 + 2192 days → 2026-01-01T00:00Z=1767225600; +222 days → Aug-11=1786406400; PDT −7 → W0=1786431600, W1=1786474800; Jan-11 PST → 1768118400. All match the test constants.
- **UTC-window membership set** in `test_tz_changes_the_answer` — recomputed each track's absolute epoch against [1786406400, 1786449600): {e1, a1, a2, c1} is exactly right (b1 at 1786474790 is out; c1 at 1786428050 is in). The test genuinely proves tz changes the *set*, not just the size.
- **DST gap detection** — the PEP-495 roundtrip in `_is_nonexistent` is logically correct: a fold-0 gap time normalizes through UTC to a different wall time (detected), an ambiguous fall-back time roundtrips exactly (accepted, pinned by test), and the gap check deliberately runs before the ordering check so the caller gets the real diagnosis.
- **The strftime false-0 pin** — `videos.start_epoch` is REAL (schema v5, confirmed in `schema.py`), so the inlined `start_epoch + first_seen` compares number-to-number; the regression test demonstrates the broken TEXT form returning 0 on the same fixture where the numeric truth is 4, and `select_placed`'s TypeError guard (bool-excluding, correctly ordered before the empty-selection returns) makes the failure loud through the API.
- **Parity** — `resolve_category` is byte-identical logic to the old `_classes` (including the `"glass"→"gla"` pre-existing quirk), the table pins outputs by hand rather than tautologically, and `objects.py`'s delegation introduces no import cycle (`objects → aggregate → storage/contracts`, no back-edge).
- **The "reuse timeline.py" plan deviation dissolves on inspection** — `timeline.py` contains no wall-clock→tz conversion at all (`wallclock_to_chunks` takes UTC epochs), and its range is closed at t1 (`if v.start_epoch > t1: continue` admits a chunk starting exactly at t1), so reusing it would have broken the half-open end semantics the tests pin. The deviation is documented in the `select_tracks` docstring with a valid rationale.
- **Fixture APIs** — `Workspace.catalog_db`, `CameraStore.get_or_create`, `Catalog.upsert` (round-trips `camera_id`/`start_epoch` via `_to_row`), `replace_tracks`, and the `EvidenceItem(modality=, content=)` fields all exist as used.
- **Process** — COORDINATION.md logs all three interface additions; the branch registry records the `human-reviewed` (contracts/) and `golden-verified` (pipeline/) label needs for the stacked bases; commit subjects are provisional `need_agent_review:` (exempt from the clarity rule); everything is additive, no schema migration needed.

**Caveat on my own verification:** pytest execution required approval this session and was declined, so I did not re-run the suite; all of the above is static verification plus hand recomputation. The three new test files are internally consistent against the code as written.

**One minor finding survives** — a forward-looking honesty gap, not a bug in this diff: window membership is by track *start* only, so a car whose track began before the window but is still present inside it (e.g. a track starting 23:58:30 on an NVR chunk straddling local midnight, persisting to 00:00:20) is excluded from "how many cars 00:00–12:00". That is a deliberate, documented partition semantic — but the plan's canonical caveat string (§11: parked / crossed / no-ReID) nowhere discloses it, and the repo's own rule is that "caveats must always disclose what the count did NOT do." If TQ1.e composes `CountResult` from this primitive without adding a "counts arrivals within the window; tracks already underway at window start are excluded" caveat, the disclosed method will be silently incomplete. Safe path: add that string to the TQ1.e caveat set (or note it in the plan §11 list now) so the disclosure travels with the number.

Verdict: **approve** — the one finding is minor and actionable at the TQ1.e composition point.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/storage/structured/tracks.py", "line": 122, "issue": "Window membership is by track start only (deliberate, documented), but neither the plan's canonical caveat list (§11) nor any planned CountResult caveat discloses that tracks already underway at window start are excluded — an incomplete method disclosure under the repo's caveats-travel-with-the-number rule.", "scenario": "An NVR chunk straddles local midnight; a car's track starts 23:58:30 and persists to 00:00:20. 'How many cars 00:00–12:00' excludes it. When TQ1.e composes CountResult from select_placed, the caveats field says parked/crossed/no-ReID but not the boundary-exclusion semantic, so the count ships with an incomplete method disclosure. Fix: add an 'arrivals-within-window; tracks underway at window start excluded' caveat string to the TQ1.e caveat set (or plan §11) so the disclosure travels with the number."}]}
```
