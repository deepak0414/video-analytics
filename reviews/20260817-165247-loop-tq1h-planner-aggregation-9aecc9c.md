# Agent review — approve

date: 2026-08-17T16:56:55.504942
range: origin/main..HEAD
branch: loop/tq1h-planner-aggregation
findings: 1

- **minor** `src/va/pipeline/ask.py:286` — The CODE-COUNTED lead guard uses a generic substring probe, so when both an aggregate_count and a deep_scan_count item exist and the narrator quotes the deep-scan line, the aggregate lead is suppressed and the answer leads with the wrong code-counted number
  - scenario: an LLM planner sets needs_deep_scan and needs_aggregation on one question; the narrator echoes the deep-scan CODE-COUNTED line verbatim; 'CODE-COUNTED' is found in rendered, the typed-count lead is skipped, and the answer leads with the sweep count (scene-cut-prone on A-LSSRVF footage) instead of the typed track count — check ds[0].content not in rendered instead of the substring probe

---

## Full review

## Review: origin/main..HEAD (typed-query tier, TQ1.a–TQ1.h + loop bookkeeping)

Nine commits building the deterministic windowed-aggregation tier: contracts (`TimeWindow` with mandatory validated tz, `CountResult`/`EventRow`/`Bucket`), the category and identity resolve-seams (honest stubs with provenance), `TrackStore.select_placed` (numeric-epoch SQL with a loud TEXT-bound refusal), the three ops, the `va aggregate` CLI, and Role-11 planner/retrieval/ask wiring with a JSON-schema tool registry the planner prompt renders from.

**What I verified independently** (not inherited from the five prior review rounds on this branch):

- **Ground truth reproduces.** I re-derived the Aug-11 00:00–12:00 PDT car count by my own direct SQL over `.va-24h` (`nvr-ch1` 22 / `nvr-ch2` 55 / total 77) and then ran both `count_objects` and `dispatch_aggregation` live — the pipeline reproduces it exactly, tz-correct, with 3 standing caveats and 77 evidence rows. The golden fixture's gating `source_key` really exists as `done` in `.va-24h`, so the fixture cannot silently skip-and-report-green.
- **The false-0 hazard is genuinely pinned**: `test_strftime_text_bound_is_the_false_zero_bug` constructs the broken strftime query and shows 0 vs the numeric truth, and `select_placed` raises `TypeError` on non-numeric bounds.
- **Honesty rules hold**: 11 parameterized malformed-argument shapes each degrade to a note and zero items; `dedup="instance"` reports `dedup_mode="raw"` plus the no-ReID caveat; `cameras=[]` means "nothing", never "everything"; the untruncated total leads every op's CODE-COUNTED line; caveats travel with every op.
- **No double dispatch**: `ask()` uses only `retrieve()`; `assemble()` is a separate entry path; `_UNAVAILABLE` is empty so no duplicate notes. The offline `rule` planner never sets `needs_aggregation` — by design per the plan (LLM planner + CLI are the paths), not a gap.
- **Contracts/docs**: all shared-surface changes are additive with defaults and logged in COORDINATION.md (including the web-layer `[CODE-COUNTED: …]` prefix note); CLAUDE.md, the golden README (`modality:` key, `hand-sql-crosscheck` provenance class), and the plan/loop docs are updated in-range. Commit subjects are all provisional `need_agent_review:` (exempt). I could not run the pytest suite (command approval unavailable this session); every new path I traced has a test that constructs its scenario, and the loop log records 833 passed / 2 skipped.

**One finding survived**, a narrow display-discipline gap:

- **minor — `src/va/pipeline/ask.py:286`**: the prepend guard is `if ds and "CODE-COUNTED" not in rendered`, but `ds` now holds two distinct code-counted modalities (aggregate first, deep-scan second). If a plan fires **both** tiers and the narrator happens to quote the deep-scan CODE-COUNTED line in its prose, the generic substring probe sees "CODE-COUNTED" and suppresses the aggregate lead — so the answer leads with the deep-scan number (the counter known to count scene cuts on security footage) instead of the typed count this very branch promises to lead with. Safe path: test presence of the specific item, e.g. prepend when `ds[0].content not in rendered`.

No critical or major findings — the range is approve.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/ask.py", "line": 286, "issue": "The CODE-COUNTED lead guard uses a generic substring probe, so when both an aggregate_count and a deep_scan_count item exist and the narrator quotes the deep-scan line, the aggregate lead is suppressed and the answer leads with the wrong code-counted number", "scenario": "an LLM planner sets needs_deep_scan and needs_aggregation on one question; the narrator echoes the deep-scan CODE-COUNTED line verbatim; 'CODE-COUNTED' is found in rendered, the typed-count lead is skipped, and the answer leads with the sweep count (scene-cut-prone on A-LSSRVF footage) instead of the typed track count — check ds[0].content not in rendered instead of the substring probe"}]}
```
