# Agent review — approve

date: 2026-08-17T16:52:06.969669
range: origin/main..HEAD
branch: loop/tq1h-planner-aggregation
findings: 0


---

## Full review

The only delta since the last approved review round (commit 3670c27) is a rewrite of plan §7's fallback bullet — which is precisely the fix for that round's single surviving minor finding. I still performed my own full-range pass rather than inheriting the verdict; here is what I checked and how each suspicion resolved.

## What I verified

**Correctness of the time math (the tier's whole point).** `TimeWindow.epoch_bounds()` converts wall-clock → epoch in Python; the SQL in `TrackStore.select_placed` (src/va/storage/structured/tracks.py:1139) compares number-to-number, refuses TEXT bounds loudly, and the strftime false-0 failure mode is pinned by a test that actually constructs the broken query and shows 0 vs the numeric truth of 4 (`test_strftime_text_bound_is_the_false_zero_bug`). DST handling is real: the spring-forward gap check uses a PEP-495 round-trip, ambiguous fall-back times are accepted, and tests hand-derive epochs independently of zoneinfo (leap-day arithmetic in comments). Boundary semantics (start-inclusive/end-exclusive, NULL-epoch A-EV skip, `cameras=[]` ≠ "all cameras") are each constructed in fixtures, not asserted tautologically.

**Suspicions that dissolved on inspection:**
- *Double dispatch* — both `retrieve()` and `assemble()` call `dispatch_aggregation`, but `ask()` (src/va/pipeline/ask.py:222) uses only `retrieve()`; `assemble()` is a separate legacy/entry path, and no caller chains both. One `aggregate_count` item per ask, as the end-to-end test asserts (`len(agg) == 1`).
- *Rule deep-scan floor co-firing on aggregation questions* — the closed `_DEEP_SCAN` regex (rule_inproc.py:35) requires "how many **times**"/"how often" or the visit/appear/come alternation; "how many cars were seen between midnight and noon" matches none of them, so a typed-count ask does not also drag in a Tier-5b sweep.
- *Histogram indexing* — selection guarantees `first_seen_epoch ∈ [t0, t1)`, `math.ceil` allocation covers fractional spans, and the regression test for the old integer-ceiling under-allocation genuinely constructs a 10.5 s window with an entity at t0+10.2 (would IndexError pre-fix).
- *Brace-escaping in the registry-rendered planner prompt* — pinned by a `.format(query="probe")` probe test; current tool descriptions contain no braces, and the section is escaped defensively anyway.
- *Import-time cycle from prompts → pipeline.aggregate* — the aggregate module's import chain (contracts, storage, paths) never reaches back into the reasoner adapters.

**Honesty rules.** Every degrade path (11 malformed-argument shapes, parameterized) yields a note and zero items — never a number; `dedup="instance"` reports `dedup_mode="raw"` plus the no-ReID caveat; the mixed-workdir hazard surfaces as a caveat; the untruncated total leads every op's CODE-COUNTED line. The `min_frames=2`, `"1h"` bucket, and 10k-bucket cap heuristics are named, flagged in COORDINATION/plan, and overridable. The golden fixture's provenance honestly says it pins tracker output against independent hand SQL (77 = ch2 55 + ch1 22, re-verified by the prior round against `.va-24h`), not footage truth — the determinism-≠-correctness rule respected.

**Contracts & docs.** All shared-surface changes are additive with defaults (`needs_aggregation=False`, new modality string, new modules) and logged in COORDINATION.md, including the web-layer note about the `[CODE-COUNTED: …]` answer prefix. CLAUDE.md documents the new `va aggregate` surface and the Role-11 wiring in this same range; the golden README documents the new `modality:` key and `hand-sql-crosscheck` provenance class. No new env vars or config keys. Commit subjects are all provisional `need_agent_review:` and exempt from the plain-description rule.

**Plan conformance.** TQ1.a–g Done-when items all map to landed code + tests; TQ1.h's Done-when (CountResult-backed tz-correct answer through `ask`, offline stub-planner dispatch test, golden ask fixture for the NVR path) is satisfied — the offline `rule` planner never sets `needs_aggregation`, but the plan assigns aggregation triggering to the LLM planner with the CLI as the deterministic path, so that is by design, not a gap. The §7 doc-drift fix in this final commit accurately describes the as-built degrade-to-note behavior and closes the last carried finding.

I could not execute the suite in this session (command approval), but each new code path I traced has a test I read that constructs its scenario, and the range's per-item hook reviews record green suites through 812 passed.

**Verdict: approve** — no findings survived verification.

```json
{"verdict": "approve", "findings": []}
```
