# Agent review — approve

date: 2026-08-17T15:59:40.745251
range: origin/main..HEAD
branch: loop/tq1f-events-histogram
findings: 0


---

## Full review

Verification complete. All 67 new tests pass (0.39s, stub path, no GPU). Let me summarize.

## Review — `origin/main..HEAD` (6 commits, typed-query tier TQ1.a–TQ1.f)

I read the full branch diff, the covering plan (`typed-query-tier-plan.md` §3–§6, §9, §11) and loop file, all six new test files, the touched pre-existing surfaces (`Catalog.footage_domains`, `MODALITY_OBJECT_COUNT`/`EvidenceItem`, `TrackStore`, `Workspace`), and the prior per-commit reviews. I ran the six new test files (67 passed; a concurrent full suite was live, so I ran only the targeted files per the repo's test-hygiene lesson).

**What holds up under adversarial reading:**

- **The prior blocking finding is genuinely fixed.** The r1 review of this branch's tip (316364a) found a major IndexError: the integer-ceiling idiom under-allocated histogram buckets on fractional-second spans. The amended commit (902779c) uses `math.ceil((t1 - t0) / width)` and adds `test_fractional_span_allocates_the_true_ceiling_of_buckets`, which constructs the exact failing scenario (10.5 s window, 10 s buckets, entity at t0+10.2) and would fail on the old form — I re-derived the arithmetic: old idiom gives 1 bucket, test asserts 2.
- **Epoch discipline is sound end-to-end.** Bounds are Python-computed numbers; `select_placed` raises `TypeError` on text bounds (bool excluded too); the strftime TEXT false-0 bug is demonstrated live on the fixture (`test_strftime_text_bound_is_the_false_zero_bug`: numeric = 4, strftime form = 0). I independently verified the hand-derived epoch worksheet (1786431600 = Aug-11 2026 00:00 PDT) — it checks out.
- **Boundary semantics all pinned by constructed scenarios:** half-open end (track at exactly W1 excluded), start-inclusive, NULL-epoch A-EV skip, tz as load-bearing (UTC vs local changes the *set*, not just the size), `cameras=[]` → 0 rows vs `cameras=None` → unrestricted, DST spring-forward gap diagnosed by name rather than a misleading "end before start", ambiguous fall-back times accepted.
- **Repo-rule compliance:** no hardcoded content (`vehicles` deliberately resolves to nothing; synonym expansion is the human-gated TQ1.b2), named heuristics (`min_frames=2`, `"1h"` default bucket, 10k bucket cap) flagged in code, docs, and COORDINATION; every count carries the three standing caveats plus instance-fallback and mixed-workdir disclosures; provenance reports what *ran*, never what was requested. Ground truth is hand-derived per the determinism≠correctness rule, with the derivation shown in the test files.
- **No contract breaks:** purely additive — no schema change, no existing signature altered; `_classes` delegation parity is pinned with hand-written literals (not tautological equality) including the pre-existing `"glass"→"gla"` quirk; the deliberate `count_objects` name reuse across modules is documented in both the docstring and COORDINATION.md. All six new surfaces are logged in COORDINATION.md.
- **Cross-op consistency is tested through the one shared selection path:** list rows == count evidence manifest, histogram sums == count total across four bucket widths.

**Suspicions I chased that dissolved:** `footage_domains` counting (profile, source_type) pairs is the established R11.b convention reused deliberately (over-warns safely); `select_placed` not filtering `ingest_status` matches the existing `distinct_counts` semantics exactly; the PEP-495 nonexistent-time check is correct for fold-ambiguous times (naive-datetime equality ignores `fold`); no import cycle from the `objects.py → aggregate.py` delegation; the loop file's missing TQ1.f `[R]` registry row is the documented protocol state mid-review-loop, not a gap; plan §9's "planner tool — build" item is tracked as open loop items TQ1.g/h, not silently dropped. Commit subjects are all provisional `need_agent_review:` and exempt from the clarity rule. No new env vars, CLI flags, or config keys, so no documentation-parity gap.

Nothing I found survives verification as a reportable defect.

```json
{"verdict": "approve", "findings": []}
```
