# Agent review — approve

date: 2026-08-17T15:46:09.352039
range: origin/main..HEAD
branch: loop/tq1e-count-objects
findings: 1

- **minor** `typed-query-tier-plan.md:218` — Plan §6 (and the loop file's operating-context note) still mandates routing the window through pipeline/timeline.py::wallclock_to_chunks ('No new time math'), but the as-built TQ1.c/TQ1.e path deliberately inlines start_epoch + first_seen in select_placed's SQL — the plan was not updated to as-built the way §5.1/§5.2 were.
  - scenario: A fresh loop executor implementing TQ1.f (timeline_histogram) follows §6 literally and builds bucket membership via wallclock_to_chunks range-mapping, producing a second time-placement path that can diverge from select_placed's per-track rule (e.g. on unknown-duration chunks, where ChunkRange caps rel_end at the range end) — two ops in the same tier disagreeing on which tracks a window contains.

---

## Full review

# Review: origin/main..HEAD (typed-query tier TQ1.a–TQ1.e)

Five stacked `need_agent_review:` commits delivering the aggregation contracts, the two resolve-seams, windowed track selection, and the composed `count_objects` op. I read the full diff, the plan and loop docs, the storage/contract surfaces the new code calls, and workflow-trust-plan.md's dispute records (none apply to this range).

**What I verified and it held up:**

- **Correctness of the core math.** The `TimeWindow` epoch worksheet in the tests is independently derivable and correct (Aug-11-2026 PDT midnight = 1786431600; Jan-11 PST pin = 1768118400). The PEP-495 round-trip check in `_is_nonexistent` correctly distinguishes spring-forward gaps (round-trip lands on a different wall time) from ambiguous fall-back times (round-trip exact), and field validation failing on `tz` correctly skips the after-validator. The SQL in `select_placed` (half-open `[start, end)` on `v.start_epoch + t.first_seen`, NULL-epoch skip, lowercased class match, deterministic ordering) matches the hand-derived fixture truths, including the tz-swap test where the *set* changes, not just the size.
- **The silent-0 regression pin is real.** `test_strftime_text_bound_is_the_false_zero_bug` demonstrates the broken strftime-TEXT form returning 0 against a numeric truth of 4 on the same fixture, and `select_placed` refuses non-numeric bounds loudly (bools included). This is the failure mode the tier exists for, properly constructed.
- **Parity claims are pinned, not tautological.** The `resolve_category`/`_classes` parity table pins hand-written literals (avoiding the delegation tautology, including the pre-existing `"glass"→"gla"` quirk), and `resolve_identities` raw mode is cross-checked against `distinct_counts` on a real DB fixture (car=2/person=2, hand-derived). `cameras=[]` vs `cameras=None` semantics are guarded and tested.
- **Repo-rule compliance.** No synonym content snuck into the stub (pinned by `test_no_synonym_content`); `min_frames=2` and `NO_CAMERA_KEY` are flagged as named structure; every count carries the three standing caveats plus the instance-fallback and mixed-workdir disclosures; ground truths are hand-counted per the determinism-≠-correctness rule.
- **Carried minors from earlier rounds landed as promised**: the plan §11 start-membership bullet + `CAVEAT_START_MEMBERSHIP` (TQ1.c r2), and the §5.1/§5.2 as-built signature updates (TQ1.b r1, TQ1.d r1).
- **Contract/coordination hygiene.** All five items logged in COORDINATION.md, including the deliberate same-name `count_objects` collision (different module, documented). The change is purely additive; the tier is backend-independent SQL, so stub-only testing covers the combination matrix here. No env vars, CLI flags, or config keys were added, so no CLAUDE.md parity gap. Commit subjects are provisional `need_agent_review:` (exempt from the clarity rule).

**One thing I could not do:** run the test files — pytest execution was denied in this session. My verification of test behavior is static (plus the loop log's recorded green suites at each stacked commit); the offline-tests CI gate and the Stop-gate remain the executable check.

**The one finding (minor, doc drift):** typed-query-tier-plan.md §6 still says the window is "handed to the **existing** `wallclock_to_chunks` … **No new time math** — reuse the WS-3/4 primitive," and the loop file's operating context repeats "Reuse it — no new time math." The as-built TQ1.c/TQ1.e path deliberately does NOT go through `wallclock_to_chunks` — it inlines `v.start_epoch + t.first_seen` in `select_placed`'s SQL (justified in the docstring and COORDINATION.md: membership is per-track, not range→chunk). The deviation itself is fine and was reviewed on the TQ1.c branch; the problem is the plan text was never updated to as-built the way §5.1/§5.2 were, and this loop's fresh executors are instructed to trust the plan. A future item (TQ1.f `timeline_histogram` is the immediate risk) could follow §6 literally and build a second, divergent time path. Safe path: update §6 to the as-built statement ("epoch bounds via `TimeWindow.epoch_bounds()`; per-track placement inlined in `select_placed` per the `timeline.py` translation rule; `wallclock_to_chunks` remains the range→chunk primitive") — same treatment §5.1/§5.2 already got.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "typed-query-tier-plan.md", "line": 218, "issue": "Plan §6 (and the loop file's operating-context note) still mandates routing the window through pipeline/timeline.py::wallclock_to_chunks ('No new time math'), but the as-built TQ1.c/TQ1.e path deliberately inlines start_epoch + first_seen in select_placed's SQL — the plan was not updated to as-built the way §5.1/§5.2 were.", "scenario": "A fresh loop executor implementing TQ1.f (timeline_histogram) follows §6 literally and builds bucket membership via wallclock_to_chunks range-mapping, producing a second time-placement path that can diverge from select_placed's per-track rule (e.g. on unknown-duration chunks, where ChunkRange caps rel_end at the range end) — two ops in the same tier disagreeing on which tracks a window contains."}]}
```
