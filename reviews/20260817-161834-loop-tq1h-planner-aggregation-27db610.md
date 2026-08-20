# Agent review — approve

date: 2026-08-17T16:23:40.655068
range: origin/main..HEAD
branch: loop/tq1h-planner-aggregation
findings: 4

- **minor** `tests/golden_queries/nvr24h_aggregate.yaml:26` — provenance: human-verified mislabels agent hand-SQL over the same object_tracks table the op queries; README defines human-verified as a human watching the footage
  - scenario: the fixture is read as footage-level ground truth (77 real cars) when it only pins tracker output; a tracker over-count regression would be enshrined as 'human-verified' truth — relabel model-regression or a hand-SQL-crosscheck value
- **minor** `src/va/pipeline/aggregate.py:520` — list_events dispatch leads with a limit-capped row count as the CODE-COUNTED number without disclosing truncation
  - scenario: 150 in-window events with default limit=100 -> the ask answer leads with 'CODE-COUNTED: 100 event(s)' and the narrator/user takes 100 as the count — report the uncapped total and phrase rows as 'first N'
- **minor** `src/va/pipeline/aggregate.py:528` — list_events and timeline_histogram dispatch return empty notes, dropping the standing caveats and the dedup='instance' no-ReID fallback caveat that count_objects surfaces
  - scenario: planner emits {op: list_events, dedup: 'instance'} -> rows are silently raw (not deduped) with no caveat note in the evidence; return ident.caveats/standing caveats as notes for all ops
- **minor** `src/va/pipeline/aggregate.py:474` — cameras = args.get('cameras') or None converts an empty list to 'all cameras' — the falsy-guard slip select_placed's own comment warns inflates a filtered count
  - scenario: planner emits cameras: [] after failing to resolve a user-named camera -> the unfiltered total is presented as the filtered answer; handle [] explicitly (degrade with a note or keep empty-selection semantics) and document

---

## Full review

Review complete. I read the full 8-commit range (contracts → seams → ops → CLI → planner wiring), the covering plan/loop docs, COORDINATION.md, and all eight new test files, and independently re-ran the golden ground-truth SQL against `.va-24h` (it reproduces exactly: nvr-ch2 55 / nvr-ch1 22 / 77 total).

**Verdict: approve.** The core logic is sound — window membership, half-open bounds, tz handling (including the DST-gap diagnosis and the fold fallback), the strftime-TEXT false-0 pin, the empty-camera-subset guard, prompt/registry drift guard, and the degrade-never-guess dispatch are all correct and genuinely tested (tests construct their scenarios; the silent-0 regression test demonstrates the bug form against the same fixture). COORDINATION.md logs every shared-surface change; CLAUDE.md documents the new CLI. No contract breaks (all additive with defaults), no schema changes, no double-dispatch (retrieve() replaces assemble() in the ask path, both covered). What survived verification is four minor honesty/consistency gaps:

1. **Golden provenance mislabel** — `tests/golden_queries/nvr24h_aggregate.yaml:26` claims `provenance: human-verified`, but per `tests/golden_queries/README.md` that means "a human watched the footage and wrote the truth down". This number is agent hand-SQL over the same `object_tracks` table the op queries — a correct deterministic-path regression pin (I reproduced it), but circular w.r.t. real-world truth (it pins tracker output, not actual cars). Given this repo's history of mislabeled golden fixtures, label it `model-regression` (or a new hand-SQL-crosscheck value) and keep the honest comment.

2. **Limit-capped count presented as CODE-COUNTED** — `src/va/pipeline/aggregate.py:520`: the `list_events` dispatch leads with `CODE-COUNTED: {len(rows)} event(s)` where `rows` is already capped at `limit` (default 100). With 150 in-window events the answer leads with "CODE-COUNTED: 100 event(s)" and no truncation disclosure — an understated number in the tier whose whole point is honest numbers. Safe path: report the uncapped entity count and phrase the rows as "first N".

3. **Caveats dropped for events/histogram ops** — `src/va/pipeline/aggregate.py:528,544` return empty notes, so the standing caveats and especially the `dedup="instance"` no-ReID fallback caveat surface only for `count_objects`. A planner emitting `list_events` with `dedup: "instance"` gets rows silently un-deduped with no caveat beyond the terse "raw upper bound" phrase. Safe path: return `ident.caveats` (and the standing set) as notes for all three ops.

4. **`cameras: []` falsy-slip at the dispatch boundary** — `src/va/pipeline/aggregate.py:474`: `args.get("cameras") or None` turns an empty list into "all cameras" — the exact inflate-a-filtered-count slip `select_placed`'s comment (tracks.py) explicitly guards against, reintroduced one layer up. A planner that emits `cameras: []` (e.g. it couldn't resolve the asked-for camera) gets the unfiltered total presented as if filtered. Safe path: handle `[]` explicitly — degrade with a note or pass it through to the empty-selection semantics — and document the choice.

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "tests/golden_queries/nvr24h_aggregate.yaml", "line": 26, "issue": "provenance: human-verified mislabels agent hand-SQL over the same object_tracks table the op queries; README defines human-verified as a human watching the footage", "scenario": "the fixture is read as footage-level ground truth (77 real cars) when it only pins tracker output; a tracker over-count regression would be enshrined as 'human-verified' truth — relabel model-regression or a hand-SQL-crosscheck value"},
{"severity": "minor", "file": "src/va/pipeline/aggregate.py", "line": 520, "issue": "list_events dispatch leads with a limit-capped row count as the CODE-COUNTED number without disclosing truncation", "scenario": "150 in-window events with default limit=100 -> the ask answer leads with 'CODE-COUNTED: 100 event(s)' and the narrator/user takes 100 as the count — report the uncapped total and phrase rows as 'first N'"},
{"severity": "minor", "file": "src/va/pipeline/aggregate.py", "line": 528, "issue": "list_events and timeline_histogram dispatch return empty notes, dropping the standing caveats and the dedup='instance' no-ReID fallback caveat that count_objects surfaces", "scenario": "planner emits {op: list_events, dedup: 'instance'} -> rows are silently raw (not deduped) with no caveat note in the evidence; return ident.caveats/standing caveats as notes for all ops"},
{"severity": "minor", "file": "src/va/pipeline/aggregate.py", "line": 474, "issue": "cameras = args.get('cameras') or None converts an empty list to 'all cameras' — the falsy-guard slip select_placed's own comment warns inflates a filtered count", "scenario": "planner emits cameras: [] after failing to resolve a user-named camera -> the unfiltered total is presented as the filtered answer; handle [] explicitly (degrade with a note or keep empty-selection semantics) and document"}
]}
```
