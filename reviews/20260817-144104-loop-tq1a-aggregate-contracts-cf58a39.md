# Agent review — approve

date: 2026-08-17T14:44:23.033205
range: origin/main..HEAD
branch: loop/tq1a-aggregate-contracts
findings: 2

- **minor** `src/va/contracts/aggregate.py:98` — A TimeWindow starting in the DST spring-forward gap (a nonexistent wall time, e.g. 2026-03-08 02:30 America/Los_Angeles) is rejected by the _ordered validator with a misleading 'end is before start' error instead of a gap-aware message.
  - scenario: The TQ1.h planner fills start=2026-03-08T02:30, end=2026-03-08T03:00, tz=America/Los_Angeles from a user question; fold=0 resolves 02:30 with the pre-gap PST offset (10:30Z) and 03:00 as PDT (10:00Z), so validation fails claiming the forward-ordered window is reversed — fail-closed but undiagnosable for the caller.
- **minor** `typed-query-tier-plan.md:138` — Plan §4 gives the dedup_mode provenance value as "instance-reid" while §3, §5.2, the loop file, and the code's DedupMode Literal all use "instance"; ResolutionProvenance.dedup_mode is an unvalidated str so the divergence would ship silently.
  - scenario: The fresh executor implementing TQ1.d/TQ1.e copies §4's example and writes dedup_mode="instance-reid" while callers and tests written from the Literal check for "instance" — provenance comparisons silently mismatch with no validation error.

---

## Full review

`contracts/__init__.py` is empty, so no registration is needed. I have everything for the verdict.

## Review summary

**Scope:** one commit (`cf58a39`) adding `src/va/contracts/aggregate.py`, its test file, a COORDINATION.md log entry, and two design docs. Purely additive — nothing imports the new module yet.

**What I verified (all clean):**
- The hand-derived epoch ground truth in the tests (1786431600 / 1786474800 / 1768118400) is independently correct — I recomputed it with stdlib `datetime` outside the code under test, satisfying the determinism-≠-correctness rule.
- Live execution of the contract behavior: missing/blank/`None`/unknown tz all reject; naive-in-tz and aware-with-own-offset both produce the correct DST-aware UTC epochs; end-before-start rejects; end==start passes; unknown extra fields round-trip through `model_dump()`; zero-arg construction works on every result model.
- Contract alignment: `EvidenceItem(modality=, content=)` matches `contracts/evidence.py`; `EventRow.track_id/video_id: Optional[UUID]` matches reality (both trackers mint `uuid4()` ids; `videos.id`/`object_tracks.id` are TEXT-serialized UUIDs); `camera` strings match the `nvr-ch<n>` convention; epoch-seconds semantics match `videos.start_epoch`.
- The COORDINATION.md entry is present and accurate; the commit subject is a provisional `need_agent_review:` (exempt from the plain-description rule, and readable anyway). Idiom matches `query_plan.py`/`evidence.py`. No new env vars, CLI flags, or config keys, so no doc-parity gap. Contracts are backend-independent, so no combination-matrix exposure. TQ1.a's "Done when" items are each covered by a test.

**Two minor findings, no blockers:**

1. A window whose `start` falls in the DST spring-forward gap (e.g. 2026-03-08 02:30 America/Los_Angeles — a wall time that doesn't exist) resolves under fold=0 to a later epoch than a real `end` shortly after, and the `_ordered` validator then rejects the forward-ordered window with "end is before start" — fail-closed (good) but misdiagnosed. When the planner starts filling `TimeWindow` from user text (TQ1.h), that message will confuse rather than explain. Safe path: detect the nonexistent-time case (compare fold=0 vs fold=1 offsets) and raise a gap-specific message, or document the behavior where TQ1.h consumes validation errors.

2. The plan doc contradicts itself and the code on the `dedup_mode` provenance string: `typed-query-tier-plan.md:138` says `"instance-reid"` while §3, §5.2, the loop file, and the code's `DedupMode` Literal all say `"instance"`. `ResolutionProvenance.dedup_mode` is an unvalidated `str`, so an executor implementing TQ1.d/e from §4 could ship the divergent string silently. Safe path: fix §4 to `"instance"` (one word in a doc that's part of this same diff).

Verdict: **approve** — both findings are minor.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/contracts/aggregate.py", "line": 98, "issue": "A TimeWindow starting in the DST spring-forward gap (a nonexistent wall time, e.g. 2026-03-08 02:30 America/Los_Angeles) is rejected by the _ordered validator with a misleading 'end is before start' error instead of a gap-aware message.", "scenario": "The TQ1.h planner fills start=2026-03-08T02:30, end=2026-03-08T03:00, tz=America/Los_Angeles from a user question; fold=0 resolves 02:30 with the pre-gap PST offset (10:30Z) and 03:00 as PDT (10:00Z), so validation fails claiming the forward-ordered window is reversed — fail-closed but undiagnosable for the caller."}, {"severity": "minor", "file": "typed-query-tier-plan.md", "line": 138, "issue": "Plan §4 gives the dedup_mode provenance value as \"instance-reid\" while §3, §5.2, the loop file, and the code's DedupMode Literal all use \"instance\"; ResolutionProvenance.dedup_mode is an unvalidated str so the divergence would ship silently.", "scenario": "The fresh executor implementing TQ1.d/TQ1.e copies §4's example and writes dedup_mode=\"instance-reid\" while callers and tests written from the Literal check for \"instance\" — provenance comparisons silently mismatch with no validation error."}]}
```
