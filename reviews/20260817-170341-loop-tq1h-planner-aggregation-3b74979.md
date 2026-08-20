# Agent review — approve

date: 2026-08-17T17:08:10.417131
range: origin/main..HEAD
branch: loop/tq1h-planner-aggregation
findings: 1

- **minor** `typed-query-tier-loop.md:363` — The PHASE 1 COMPLETE log entry says '7 stacked branches' but TQ1.a-TQ1.h span 8 branches, contradicting the branch table directly above it
  - scenario: the human approval session uses this doc as the merge checklist ('merge order = registry order'); an off-by-one branch count invites stopping one branch early and leaving loop/tq1h-planner-aggregation unmerged — correct the count to 8

---

## Full review

The suite is green (834 passed, 2 skipped). Review complete — here's my assessment of origin/main..HEAD.

## Review: origin/main..HEAD (typed-query tier TQ1.a–TQ1.h, 9 commits)

This range builds the deterministic windowed-aggregation tier end to end: contracts (`TimeWindow` with mandatory, validated IANA tz and Python-computed numeric epoch bounds), the two honest resolve-seams (category plural-strip, identity raw-with-fallback), `TrackStore.select_placed` (numeric-bound SQL that raises loudly on text bounds), the count/events/histogram ops on one shared selection path, the `va aggregate` CLI, and Role-11 planner/retrieval/ask wiring via a JSON-schema tool registry the planner prompt renders from. This branch has been through five prior review rounds; the final commit (3b74979) addresses the one surviving r5-era minor — the CODE-COUNTED lead guard now keys on the chosen item's own content line rather than a generic substring — with a regression test (`test_lead_guard_keys_on_the_aggregate_line_not_any_code_counted_text`) that would fail on the old code.

**What I verified this round:**

- **The lead-guard fix is correct and complete.** `ask.py:284-290` orders aggregate before deep-scan and prepends unless the chosen item's verbatim content already appears in the render. I checked the edge cases: aggregate-only, deep-scan-only, both-present-with-narrator-quoting-the-other — all behave correctly, and the new test constructs the exact failure scenario the r6 finding described.
- **No double dispatch.** `ask()` uses only `retrieve()`; `assemble()` is a parallel entry path (kept in parity per its docstring), and `_UNAVAILABLE` doesn't include `needs_aggregation`.
- **Honest degradation holds everywhere**: 11 parameterized malformed-argument shapes each yield zero items plus one "no count computed" note; `cameras=[]` means nothing (never everything); `dedup="instance"` reports `dedup_mode="raw"` plus the no-ReID caveat on every op; the untruncated total leads limit-capped event lists; caveats travel with all three ops on both the CLI and dispatch paths.
- **Prompt-size safety**: the aggregate summary item carries the full `CountResult` (potentially 77+ evidence rows) only in `attributes`; `render_evidence` renders only `content`, and round-robin modality selection guarantees the aggregate item survives the 60-item truncation.
- **Timezone/DST correctness**: epoch constants in the tests and the golden fixture hand-check out (Aug-11 2026 00:00 PDT = 1786431600); spring-forward gaps get a specific diagnosis; the histogram's true-float-ceiling bucket allocation and half-open boundary semantics are each pinned by a test that constructs the scenario.
- **Docs/contracts**: every shared-surface addition is additive-with-defaults and logged in COORDINATION.md (including the web-layer note about the `[CODE-COUNTED: …]` prefix); CLAUDE.md, the golden README (`modality:` key, `hand-sql-crosscheck` provenance class), and the plan docs are updated in-range. All commit subjects are provisional `need_agent_review:` (exempt from the clarity rule).
- **Suite**: 834 passed / 2 skipped, run clean this session.

**One minor finding:**

- **minor — `typed-query-tier-loop.md:363`**: the PHASE 1 COMPLETE entry says "7 stacked branches" but TQ1.a–TQ1.h span **8** branches (the table directly above lists all eight rows, `loop/tq1a…` through `loop/tq1h…`). This doc drives the human approval/merge session ("merge order = registry order"), and an off-by-one branch count is exactly the kind of slip that could make a merge checklist stop one branch early. Safe path: correct the count to 8 (or "TQ1.a–h, one branch each").

No critical or major findings — approve.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "typed-query-tier-loop.md", "line": 363, "issue": "The PHASE 1 COMPLETE log entry says '7 stacked branches' but TQ1.a-TQ1.h span 8 branches, contradicting the branch table directly above it", "scenario": "the human approval session uses this doc as the merge checklist ('merge order = registry order'); an off-by-one branch count invites stopping one branch early and leaving loop/tq1h-planner-aggregation unmerged — correct the count to 8"}]}
```
