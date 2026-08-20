# Agent review — approve

date: 2026-08-17T16:39:07.392416
range: origin/main..HEAD
branch: loop/tq1h-planner-aggregation
findings: 4

- **minor** `src/va/cli.py:117` — va aggregate events prints only len(rows) with no untruncated total, and events/histogram print no caveats/provenance — the CLI missed the every-op honesty fix the dispatch path received
  - scenario: a 250-track window with the default --limit 100 prints '100 event(s)' and the user takes 100 as the count; print 'first N of TOTAL' plus the caveat lines on all three subcommands (same selection path as count)
- **minor** `src/va/adapters/reasoner/prompts.py:77` — REASONER_PROMPT's do-not-recount instruction names only deep_scan_count, so aggregate_count items get no such rule
  - scenario: a weak narrator (the observed qwen-7B failure mode) states a different total in its summary, which renders directly under the leading [CODE-COUNTED: ...] line — extend the clause to cover aggregate_count/any CODE-COUNTED modality
- **minor** `src/va/adapters/reasoner/prompts.py:37` — planner guidance 'still set the flag with your best explicit arguments' invites the LLM to invent start/end/tz it could not infer
  - scenario: a question with no explicit window gets a fabricated window/tz from the planner and ask leads with a confidently CODE-COUNTED number for a window the user never asked about; instruct the planner to omit non-inferable arguments so dispatch degrades to the honest 'not run' note
- **minor** `src/va/cli.py:671` — the help example category 'people' cannot match the detector class 'person' under the deliberately content-free plural-strip
  - scenario: a user follows the help text on the shipped security vocab (person, car, truck, ...) and 'va aggregate count people' always returns 0; use 'car'/'person' as examples or note categories must be detector class names until the Role-12 taxonomy

---

## Full review

Review complete. I read the full 8-commit range (contracts → resolve seams → windowed selection → count/events/histogram ops → CLI → planner/retrieval/ask wiring), the covering plan and loop docs' Done-when items, COORDINATION.md, the prior review rounds in `reviews/`, and the eight new test files. A pytest run was already live in another session, so per the repo lesson I did not launch another; the tq1g hook review records a full green suite (812 passed, 2 skipped) and the range since then is covered by the extensive offline tests I read.

**Verdict: approve** — no critical or major findings. The core is sound: the tz-mandatory `TimeWindow` (DST-gap diagnosis, aware-datetime handling, half-open bounds), the strftime-TEXT false-zero regression genuinely constructed against the fixture, one shared selection path so count/events/histogram cannot disagree, the planner prompt rendered from the tool registry with a format-drift guard, honest degrade paths for every malformed-argument shape (including the `limit` TypeError the previous round caught — verifiably fixed at HEAD, along with the README provenance/modality documentation), and an offline stub-planner test through the full `ask()` path plus a correctly-labeled `hand-sql-crosscheck` golden fixture. All shared-surface changes are additive with defaults and logged in COORDINATION.md; CLAUDE.md documents the new CLI and Role-11 integration.

Four minor findings survived verification:

1. **`va aggregate events`/`histogram` ship a number without the method — and the events count can misread as the total** (`src/va/cli.py:117`). The dispatch path was fixed in an earlier round so every op leads with the untruncated total and carries the standing caveats; the CLI got only the `count` subcommand's treatment. `events` prints the capped rows then `"{len(rows)} event(s)"` with no untruncated total (250 in-window tracks with the default `--limit 100` prints "100 event(s)"), and neither `events` nor `histogram` prints the caveats/provenance block that `count` prints. Safe path: print "first N of TOTAL" (the count op is already the same selection path) and the caveat lines on all three subcommands.

2. **The reasoner prompt's do-not-recount rule covers only `deep_scan_count`** (`src/va/adapters/reasoner/prompts.py:77`). An `aggregate_count` item reaches the narrator with no instruction, so a weak model (the observed qwen-7B failure mode) can assert a different number in its summary, which renders directly under the leading `[CODE-COUNTED: …]` line — displayed truth immediately contradicted by prose. Safe path: extend the REASONER_PROMPT clause to any CODE-COUNTED modality (deep_scan_count and aggregate_count).

3. **The planner guidance licenses fabricating window arguments** (`src/va/adapters/reasoner/prompts.py:37`): "if the question gives no explicit window or timezone cannot be inferred, still set the flag with your best explicit arguments or omit them". The degrade path protects against *missing* args, but this wording invites an LLM to invent a start/end/tz it could not infer, producing a confidently CODE-COUNTED number over a window the user never asked about (disclosed only via the span embedded in the content line). Safe path: instruct the planner to omit non-inferable arguments — the dispatch already degrades honestly, which is the design's whole point.

4. **The CLI help example `'people'` structurally cannot match the shipped vocab** (`src/va/cli.py:671`). Plural-strip is deliberately content-free, so "people" resolves to `["people"]` and matches nothing under the security profile's `person` class — the documented example category always returns 0 on the repo's own NVR footage (provenance discloses the classes matched, but the example still steers users into a guaranteed miss). Safe path: use `'car'` / `'person'` as the examples, or note that categories must be detector class names until the Role-12 taxonomy lands.

Suspicions that dissolved on inspection: double dispatch (ask uses `retrieve()` only; nothing calls both it and `assemble()`), evidence-manifest prompt blow-up (`render_evidence` emits only `content`, and round-robin selection guarantees the single aggregate item survives truncation), histogram bucket indexing at the window edge (selection bounds `first_seen_epoch < t1`), and `Catalog.footage_domains()` (pre-existing surface, reused per its convention).

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "src/va/cli.py", "line": 117, "issue": "va aggregate events prints only len(rows) with no untruncated total, and events/histogram print no caveats/provenance — the CLI missed the every-op honesty fix the dispatch path received", "scenario": "a 250-track window with the default --limit 100 prints '100 event(s)' and the user takes 100 as the count; print 'first N of TOTAL' plus the caveat lines on all three subcommands (same selection path as count)"},
{"severity": "minor", "file": "src/va/adapters/reasoner/prompts.py", "line": 77, "issue": "REASONER_PROMPT's do-not-recount instruction names only deep_scan_count, so aggregate_count items get no such rule", "scenario": "a weak narrator (the observed qwen-7B failure mode) states a different total in its summary, which renders directly under the leading [CODE-COUNTED: ...] line — extend the clause to cover aggregate_count/any CODE-COUNTED modality"},
{"severity": "minor", "file": "src/va/adapters/reasoner/prompts.py", "line": 37, "issue": "planner guidance 'still set the flag with your best explicit arguments' invites the LLM to invent start/end/tz it could not infer", "scenario": "a question with no explicit window gets a fabricated window/tz from the planner and ask leads with a confidently CODE-COUNTED number for a window the user never asked about; instruct the planner to omit non-inferable arguments so dispatch degrades to the honest 'not run' note"},
{"severity": "minor", "file": "src/va/cli.py", "line": 671, "issue": "the help example category 'people' cannot match the detector class 'person' under the deliberately content-free plural-strip", "scenario": "a user follows the help text on the shipped security vocab (person, car, truck, ...) and 'va aggregate count people' always returns 0; use 'car'/'person' as examples or note categories must be detector class names until the Role-12 taxonomy"}
]}
```
