# Agent review — approve

date: 2026-08-17T15:05:09.541035
range: origin/main..HEAD
branch: loop/tq1b-resolve-category
findings: 0


---

## Full review

# Review: `origin/main..HEAD` (TQ1.a aggregation contracts + TQ1.b resolve_category seam)

**Verdict: approve.** Two additive commits; every load-bearing claim verified by direct execution against HEAD, and all three minor findings from the prior review of this branch (`reviews/20260817-145459-…-08bae62.md`) are fixed in the current commits.

## What I verified

**Time math and validation (`src/va/contracts/aggregate.py`)** — executed directly, not just read:
- Epoch bounds for the Aug-11 PDT window return exactly the hand-derived ground truth (1786431600 / 1786474800), and the winter (PST) case returns 1768118400 — the DST-awareness pin is real. I independently re-derived all four epoch constants in the test comments; they are correct (2026-03-08 and 2026-11-01 are indeed the US transition dates).
- Blank, whitespace, `None`, and unknown-IANA `tz` all reject; a valid tz is stripped.
- The spring-forward gap (`2026-03-08 02:30`) is diagnosed by name and *not* as a baffling "end before start"; the ambiguous fall-back time validates. The PEP-495 round-trip check in `_is_nonexistent` is correct: naive-datetime equality ignores `fold`, so ambiguous times round-trip while gap times don't, and aware datetimes correctly skip the check and keep their own offset.
- Unknown extra fields round-trip (`extra="allow"`), and the result models default-construct — the evolution idiom matches `query_plan.py`/`evidence.py`. `EventRow.track_id: UUID` matches how `storage/structured/tracks.py` actually stores track ids (UUID strings), so the shape won't break when TQ1.c consumes it.

**Refactor parity (`objects.py` → `pipeline/aggregate.py`)** — the moved logic is character-identical (same regex, loop, ordering); I executed all 13 parity-table cases plus `"car car cars"` and got zero mismatches between `resolve_category` and `_classes`. `_classes` is consumed only by `query_objects`/`count_objects`; `retrieval.py`/`evidence.py` use a separate exact-match-against-known-classes mechanism, so no drift was introduced there. The "default-stub path byte-for-byte unchanged" floor holds, and this is backend/profile-independent text processing — no untested combination exposure.

**Prior findings all resolved in HEAD:** the `"glass" → "gla"` comment is now correct (`test_aggregate_resolve_category.py:29`), the parity table now pins hand-written literal outputs (so it is no longer tautological — a stub edit fails loudly), and plan §5.1 now states the stable seam signature as `str -> (list[str], source: str)` matching the implementation and COORDINATION.md.

**Repo rules and process:** no hardcoded content — synonym expansion is explicitly excluded and *test-pinned* absent (`test_no_synonym_content`), matching the TQ1.b2 human gate. Ground truths in tests are hand-derived, not model-echoed. COORDINATION.md carries accurate entries for both commits; no new env vars, CLI flags, or config keys, so no documentation-parity gap. `need_agent_review:` subjects are exempt from the plain-description rule. For the approval session: this branch touches `src/va/contracts/` (human-reviewed critical path) *and* `src/va/pipeline/` (golden-verified critical path) — both labels apply.

**Caveat on the suite-green floor:** direct `pytest` invocation is approval-gated in this session, so I could not run the full offline suite; I verified the new tests' assertions by executing the same checks inline (all pass), and the change surface (two new modules + a pure delegation verified identical) leaves the rest of the suite untouched. The Stop-gate and the `offline-tests` CI check cover the full-suite requirement mechanically.

```json
{"verdict": "approve", "findings": []}
```
