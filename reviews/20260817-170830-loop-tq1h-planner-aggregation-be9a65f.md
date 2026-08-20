# Agent review — approve

date: 2026-08-17T17:13:40.359997
range: origin/main..HEAD
branch: loop/tq1h-planner-aggregation
findings: 0


---

## Full review

Review complete. This range (TQ1.a–TQ1.h, 9 commits) has been through seven prior review rounds; my job this round was to independently re-verify the whole range at HEAD, including the final commit (`be9a65f`) that addressed the last two open minors.

## What I verified

**The final commit's fix is correct and tested.** The CODE-COUNTED lead guard in `src/va/pipeline/ask.py:279-290` now keys on the *chosen item's own content line* (`ds[0].content not in rendered`) rather than a generic `"CODE-COUNTED"` substring, so a narrator quoting a *different* code-counted line (e.g. a deep-scan line when both tiers ran) can no longer suppress the aggregate lead. The new regression test (`test_lead_guard_keys_on_the_aggregate_line_not_any_code_counted_text`) constructs exactly that scenario — a reasoner that prepends a foreign `[CODE-COUNTED: 9-12 changes...]` line — and would fail on the old substring guard. The aggregate-before-deep-scan ordering is deliberate and commented; only one lead line is emitted, which matches the design rationale (the typed count is the exact answer to the question that triggered it). The prior round's remaining minor (the "7 stacked branches" count in `typed-query-tier-loop.md:363`) is also fixed — it now reads 8.

**Correctness, independently re-checked across the range:**
- Window semantics: half-open `[start, end)` on absolute track START, tz conversion done once in Python (`TimeWindow.epoch_bounds`), DST spring-forward gaps get a specific diagnosis, NULL-epoch (A-EV) videos skipped by construction — each pinned by a test that constructs the scenario, including the motivating `strftime('%s')` false-zero regression (`test_strftime_text_bound_is_the_false_zero_bug` demonstrates the broken form returns 0 rows; the store raises `TypeError` on text bounds).
- Degradation discipline: 11 parameterized malformed-argument shapes each yield zero items plus one honest note; `cameras=[]` means *nothing*, never *everything*; `dedup="instance"` reports `dedup_mode="raw"` plus the no-ReID caveat on every op; limit-capped event lists lead with the untruncated total.
- No double dispatch: `ask()` uses only `retrieve()`; `assemble()` is a parallel entry path, and `_UNAVAILABLE` is empty, so `needs_aggregation` never generates a bogus unavailable note.
- The histogram's float-ceiling bucket allocation, partial-last-bucket emission, and sum-equals-count cross-check are all tested.

**Repo rules and contracts:** all shared-surface additions are additive-with-defaults and logged in COORDINATION.md (including the web-layer `[CODE-COUNTED: …]` prefix note); the named heuristics (`min_frames=2`, `1h` default bucket, 10k-bucket cap) are flagged, not silent; no synonym/domain content in `resolve_category` (pinned by `test_no_synonym_content`); ground truth is hand-derived in the offline fixtures, and the `.va-24h` golden fixture honestly labels itself `hand-sql-crosscheck` — pinning tracker output, explicitly *not* human footage truth. Documentation parity holds (CLAUDE.md commands block, golden README `modality:` key + provenance class, plan docs committed in-range; no new env vars). All commit subjects are provisional `need_agent_review:`, exempt from the clarity rule. TQ1.h's Done-when items (offline stub-planner dispatch test, golden NVR ask fixture, CODE-COUNTED lead) are all satisfied.

**Suite:** 834 passed / 2 skipped, run clean this session (after draining a concurrent run, per the repo lesson).

I chased several suspicions — dual dispatch via `retrieve()`+`assemble()`, the both-tiers-present lead behavior, brace-escaping in the registry-rendered planner prompt, `min_frames` default divergence from `distinct_counts` — and each dissolved on reading the code and tests. No findings survive verification.

```json
{"verdict": "approve", "findings": []}
```
