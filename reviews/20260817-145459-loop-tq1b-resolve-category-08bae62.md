# Agent review — approve

date: 2026-08-17T14:59:17.354337
range: origin/main..HEAD
branch: loop/tq1b-resolve-category
findings: 3

- **minor** `tests/test_aggregate_resolve_category.py:26` — Comment misstates the stub quirk: rstrip('s') on 'glass' yields 'gla' (all trailing s stripped), not 'glas' as documented.
  - scenario: A TQ1.b2/Role-12 implementer treats the test table as the spec of the stub's tokenization, models 'glas', and builds taxonomy/alias mapping against a token the stub never produces.
- **minor** `tests/test_aggregate_resolve_category.py:33` — The parity assertion is tautological post-refactor (_classes delegates to resolve_category), so the table's edge-case rows pin no actual output.
  - scenario: A future edit to the plural-strip body changes va objects/va count query expansion for inputs like 'glass' or 'channel2 cars'; the parity table stays green and the shared-path behavior change ships silently instead of failing a pinned-output test.
- **minor** `typed-query-tier-plan.md:178` — Plan §5.1 (and the §5 pseudocode) states the stable seam signature is `str -> list[str]`, but the implemented and COORDINATION-logged signature is `str -> (list[str], str)`.
  - scenario: The Role-12 taxonomy implementer codes the replacement body from the plan's contract line, returns a bare list, and breaks every provenance-consuming caller at swap time — the exact doc-vs-code drift class r1 already fixed in §4.

---

## Full review

# Review: `origin/main..HEAD` (TQ1.a contracts + TQ1.b resolve_category seam)

**Verdict: approve.** Both commits are additive and careful; the load-bearing behavior verifies clean. Three minor findings, no majors.

## What I verified (clean)

- **Time math and DST handling** (`src/va/contracts/aggregate.py`): I executed the code directly — epoch bounds for the Aug-11 PDT window return exactly the hand-derived ground truth (1786431600 / 1786474800), blank/None/unknown tz all reject, the spring-forward gap is diagnosed by name (not as a "reversed window"), and the ambiguous fall-back time validates. The PEP-495 round-trip check is correct: naive-datetime equality ignores `fold`, so ambiguous times round-trip while gap times don't; aware datetimes skip the check and keep their own offset.
- **Refactor parity** (`src/va/pipeline/objects.py` → `pipeline/aggregate.py`): the moved logic is character-identical (same regex, same loop, same ordering); I ran a 15-case sweep including edge cases and found zero mismatches. The removed `import re` has no remaining users in objects.py. Behavior of `va objects`/`va count` is unchanged, so the "default-stub path byte-for-byte unchanged" floor holds — this is backend/profile-independent text processing, so no untested combination exposure.
- **No hardcoded content**: the seam ships pure plural-strip; "vehicles" does not expand, and a test pins that. Matches the loop file's TQ1.b2 human-gate exactly.
- **Coordination/contracts**: COORDINATION.md carries accurate entries for both commits; no schema change, no new env vars/CLI flags/config keys (no documentation-parity gap). `EventRow.track_id: UUID` matches `object_tracks` (TEXT UUIDs via `ObjectTrack.id: UUID`) — suspicion dissolved by reading `tracks.py`.
- **Process**: the TQ1.a commit was already reviewed twice (r2: approve, 0 findings); the disputes ledger in `workflow-trust-plan.md` concerns PR 5's shell guards, nothing here. `need_agent_review:` subjects are exempt from the plain-description rule. The loop file's TQ1.b `[R]` flip riding on the next branch matches the established convention. Note for the approval session: this branch touches `src/va/pipeline/`, so per the loop doc it needs the **golden-verified** label in addition to TQ1.a's `human-reviewed` (contracts/) — record that when the registry row is added.
- I did **not** launch a pytest run — a full suite from another session was live (repo lesson: never pile suites), and `/tmp` writes are not permitted here, so I verified the test assertions by executing the same checks inline instead. The committed progress log claims 754 passed / 2 skipped at TQ1.a; the TQ1.b digest should carry its own fresh counts via `/verify` as usual.

## Findings (all minor)

1. **minor — `tests/test_aggregate_resolve_category.py:26`** — the comment documenting the known stub quirk is factually wrong: `"glass".rstrip("s")` yields `"gla"` (all trailing s's stripped), not `"glas"` as written; verified by execution. Scenario: the TQ1.b2/Role-12 implementer reads the test table as the spec of the stub's behavior and builds the taxonomy/migration against the wrong token. Safe path: correct the comment to `-> "gla"`.
2. **minor — `tests/test_aggregate_resolve_category.py:33`** — the parity assertion `categories == _classes(text)` is tautological after the refactor (`_classes` *delegates to* `resolve_category`), so the table's edge rows (`"glass"`, `"s"`, `""`, `"channel2 cars"`, `"person's dog"`) pin no behavior; a future edit to the stub body would silently change `va objects`/`va count` query expansion with the whole table still green. It does still guard against future re-divergence, so it's not decoration — but the done-when's intent (refactor preserved behavior) is only enforced for the few explicitly-pinned inputs. Safe path: pin expected output lists for the table rows so a stub change fails loudly and must be deliberate.
3. **minor — `typed-query-tier-plan.md:178`** — plan §5.1's "contract that makes the swap free" says the stable signature is `str -> list[str]` (and the §5 pseudocode uses `categories = resolve_category(category)`), but the implemented seam — correctly, per the loop doc and COORDINATION.md — returns `(list[str], str)`. Scenario: the Role-12 implementer works from the plan (the rationale doc) and writes the taxonomy body to the wrong signature, exactly the drift class r1 already fixed once in §4. Safe path: update the plan line and pseudocode to the tuple form.

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "tests/test_aggregate_resolve_category.py", "line": 26, "issue": "Comment misstates the stub quirk: rstrip('s') on 'glass' yields 'gla' (all trailing s stripped), not 'glas' as documented.", "scenario": "A TQ1.b2/Role-12 implementer treats the test table as the spec of the stub's tokenization, models 'glas', and builds taxonomy/alias mapping against a token the stub never produces."},
{"severity": "minor", "file": "tests/test_aggregate_resolve_category.py", "line": 33, "issue": "The parity assertion is tautological post-refactor (_classes delegates to resolve_category), so the table's edge-case rows pin no actual output.", "scenario": "A future edit to the plural-strip body changes va objects/va count query expansion for inputs like 'glass' or 'channel2 cars'; the parity table stays green and the shared-path behavior change ships silently instead of failing a pinned-output test."},
{"severity": "minor", "file": "typed-query-tier-plan.md", "line": 178, "issue": "Plan §5.1 (and the §5 pseudocode) states the stable seam signature is `str -> list[str]`, but the implemented and COORDINATION-logged signature is `str -> (list[str], str)`.", "scenario": "The Role-12 taxonomy implementer codes the replacement body from the plan's contract line, returns a bare list, and breaks every provenance-consuming caller at swap time — the exact doc-vs-code drift class r1 already fixed in §4."}
]}
```
