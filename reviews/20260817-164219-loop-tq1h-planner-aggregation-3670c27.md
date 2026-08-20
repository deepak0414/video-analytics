# Agent review — approve

date: 2026-08-17T16:48:05.032309
range: origin/main..HEAD
branch: loop/tq1h-planner-aggregation
findings: 1

- **minor** `typed-query-tier-plan.md:244` — Plan §7's fallback line ('degrade to the existing whole-corpus count_objects with a caveat') contradicts the as-built dispatch, which degrades to an honest 'not run' note with no count — the same doc-drift class this branch fixed as-built for §5.2/§6/§11
  - scenario: a future session implementing Role-12 or extending the dispatcher reads §7 as the contract and 'fixes' dispatch_aggregation to return the whole-corpus total for a malformed windowed question, presenting a corpus-wide number as the windowed answer — rewrite the §7 bullet to the as-built degrade-to-note behavior (loop item TQ1.h's wording)

---

## Full review

Review complete. I read the full 8-commit range (contracts → resolve seams → windowed selection → count/events/histogram ops → CLI → planner/retrieval/ask wiring), the covering plan and loop docs' Done-when items, COORDINATION.md, all eight new test files, the prior review rounds in `reviews/`, and independently re-derived the ground truth.

**Verdict: approve** — no critical or major findings; one minor doc-drift finding.

**What I verified independently:**

- **Ground truth re-derived, not trusted.** I ran the independent SQL against `.va-24h` myself: nvr-ch2 = 55, nvr-ch1 = 22, total = 77 for cars with `frame_count >= 2` starting in the Aug-11 00:00–12:00 PDT epoch window — exactly what the golden fixture, COORDINATION.md, and the TQ1.g digest claim. The fixture's gating `source_key` (`nvr:ch1:1786434359-1786434396`) exists in `.va-24h` with status `done` and matches the NVR source's real key format, so the golden test will actually run rather than silently skip.
- **All four minor findings from the previous review round (2724ce2) are fixed at HEAD:** the CLI now prints "first N of TOTAL" plus caveats/provenance on all three subcommands (pinned by `test_events_truncation_discloses_the_untruncated_total`); REASONER_PROMPT gained the aggregate_count do-not-recount clause; the planner guidance now says "NEVER invent a window or timezone… OMIT any argument you cannot ground"; and the help example uses `'car'/'person'` with an explicit no-synonym note.
- **Suspicions that dissolved on inspection:** double dispatch (ask() uses `retrieve()` only; `assemble()` is exercised separately by tests/legacy callers, never both in one ask); the rule-planner deep-scan floor co-firing on the golden question ("how many cars were seen…" matches neither the times/change nor visit/appear alternations of the closed `_DEEP_SCAN` regex); histogram edge indexing (selection guarantees `first_seen_epoch < t1`, and the `math.ceil` fix for fractional spans has a genuinely-constructed regression test); brace-escaping in the registry-rendered prompt section (pinned by a `.format()` probe test); the strftime false-0 regression test really constructs the broken form against the fixture and asserts the numeric truth of 4 alongside.
- **Test integrity is strong:** expected outputs are hand-pinned (not tautological parity), boundary semantics (start-inclusive/end-exclusive, tz-changes-the-set, NULL-epoch skip, cameras=[] vs None, DST gap vs ambiguous fall-back) are each constructed, and degrade paths are parameterized over eleven malformed-argument shapes. I could not run the suite here (execution requires approval this session lacks), but the range's per-item hook reviews record green suites through 812 passed and the delta since the last review is the four targeted fixes above, each covered by a test I read.
- **Contract/doc parity:** all shared-surface changes are additive with defaults and logged in COORDINATION.md (including the web-layer answer/notes shape note); CLAUDE.md documents the new CLI and the Role-11 integration; the golden README documents the new `modality:` key and `hand-sql-crosscheck` provenance honestly (pins tracker output, not footage truth — the determinism-≠-correctness rule respected, with the real hand-SQL cross-check done). Commit subjects are all provisional `need_agent_review:` and exempt from the plain-description rule.

**The one surviving finding** — `typed-query-tier-plan.md` §7 still says invalid/missing planner args "degrade to the existing whole-corpus `count_objects` with a caveat", but the as-built `dispatch_aggregation` degrades to an honest "not run" note with no count at all (the loop item TQ1.h's wording, which the implementation matches; the whole-corpus counts still arrive via `needs_object_query` when the planner sets it, as the prompt example does). The implementation's choice is the safer one — presenting a corpus-wide total as the answer to a windowed question would be exactly the misleading number this tier exists to prevent — but the plan doc is the rationale document future sessions read, and this branch's own convention has been to rewrite drifted plan sections as-built (§5.2, §6, §11 all got this treatment). Safe path: rewrite the §7 fallback bullet to describe the as-built degrade-to-note behavior.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "typed-query-tier-plan.md", "line": 244, "issue": "Plan §7's fallback line ('degrade to the existing whole-corpus count_objects with a caveat') contradicts the as-built dispatch, which degrades to an honest 'not run' note with no count — the same doc-drift class this branch fixed as-built for §5.2/§6/§11", "scenario": "a future session implementing Role-12 or extending the dispatcher reads §7 as the contract and 'fixes' dispatch_aggregation to return the whole-corpus total for a malformed windowed question, presenting a corpus-wide number as the windowed answer — rewrite the §7 bullet to the as-built degrade-to-note behavior (loop item TQ1.h's wording)"}]}
```
