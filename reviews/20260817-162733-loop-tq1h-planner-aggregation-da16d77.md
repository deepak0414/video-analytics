# Agent review — approve

date: 2026-08-17T16:30:59.391802
range: origin/main..HEAD
branch: loop/tq1h-planner-aggregation
findings: 2

- **minor** `src/va/pipeline/aggregate.py:524` — limit is parsed with int() inside a ValueError-only try, so a non-int-non-string value (JSON null, list) raises TypeError that escapes dispatch_aggregation despite its degrade-never-raise contract
  - scenario: the LLM planner emits {op: 'list_events', ..., limit: null}; int(None) raises TypeError, retrieve() propagates it, and the entire va ask run aborts with a traceback instead of degrading to the honest 'not run' note — validate limit like min_frames (catch TypeError/ValueError and degrade)
- **minor** `tests/golden_queries/README.md:71` — the README's fixture-format spec omits the new 'hand-sql-crosscheck' provenance label and the new optional ask_questions 'modality:' key this change introduced
  - scenario: the next fixture author follows the README, picks one of the three documented provenance labels for a SQL-derived number (re-creating the mislabel the last review caught) and doesn't know aggregate_count questions are assertable — document both keys in the README

---

## Full review

Review complete. I read the full 8-commit range (contracts → resolve seams → windowed selection → count/events/histogram ops → CLI → planner wiring), the covering loop/plan docs' Done-when items, COORDINATION.md, the prior review round, and all eight new test files.

**Verdict: approve.** The core is sound and well-tested: tz-mandatory window contract (DST-gap diagnosis, aware-datetime handling, half-open bounds), the strftime-TEXT false-0 regression genuinely demonstrated against the fixture, the empty-camera-selection guard at both layers, one shared selection path so count/events/histogram cannot disagree, prompt rendered from the tool registry with a format-drift guard, and an offline stub-planner test through the full `ask()` path. All four minors from the previous review round (27db610) are verifiably fixed at HEAD: the golden fixture now carries `provenance: hand-sql-crosscheck` with an honest tracker-not-footage comment, `list_events` leads with the untruncated total, caveats travel with every op, and `cameras: []` degrades explicitly. COORDINATION.md logs every shared-surface change; CLAUDE.md documents the new CLI and the Role-11 integration. Every Done-when item for TQ1.a–h checks out, including the hand-SQL ground-truth cross-check (77 = ch2 55 + ch1 22) and the offline dispatch coverage. No contract breaks (all additive with defaults), no double dispatch (`retrieve()` replaces `assemble()` in the ask path; nothing calls both).

Two minor findings survived verification:

1. **`limit` can crash the dispatch that promises never to raise** — `src/va/pipeline/aggregate.py:524`: `limit = int(args.get("limit", 100))` sits inside a `try` that catches only `ValueError`. A planner emitting JSON `"limit": null` (or a list/object) makes `int(None)` raise `TypeError`, which escapes `dispatch_aggregation`, propagates through `retrieve()`, and aborts the whole `va ask` run with a traceback — exactly the failure mode this dispatcher's contract ("every failure path degrades to a note") exists to prevent. The `min_frames` parse eight lines up (line 492) catches `(TypeError, ValueError)`; `limit` is the one field that missed the guard. Safe path: validate `limit` the same way `min_frames` is validated (or widen the outer handler to include `TypeError`), degrading to the honest "not run" note.

2. **Golden README not updated for the two fixture-format additions** — `tests/golden_queries/README.md:71`: the provenance-label list (`human-verified` / `vision-verified` / `model-regression`) does not include the new `hand-sql-crosscheck` value the nvr24h fixture uses, and the `ask_questions` section (line 58) doesn't mention the new optional per-question `modality:` key (default `deep_scan_count`) the harness now reads. Both are documented in COORDINATION.md and code comments, but the README is the file that specifies fixture keys — the next fixture author works from it. Safe path: add the label definition (what it does and does not attest) and the `modality:` key to the README.

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "src/va/pipeline/aggregate.py", "line": 524, "issue": "limit is parsed with int() inside a ValueError-only try, so a non-int-non-string value (JSON null, list) raises TypeError that escapes dispatch_aggregation despite its degrade-never-raise contract", "scenario": "the LLM planner emits {op: 'list_events', ..., limit: null}; int(None) raises TypeError, retrieve() propagates it, and the entire va ask run aborts with a traceback instead of degrading to the honest 'not run' note — validate limit like min_frames (catch TypeError/ValueError and degrade)"},
{"severity": "minor", "file": "tests/golden_queries/README.md", "line": 71, "issue": "the README's fixture-format spec omits the new 'hand-sql-crosscheck' provenance label and the new optional ask_questions 'modality:' key this change introduced", "scenario": "the next fixture author follows the README, picks one of the three documented provenance labels for a SQL-derived number (re-creating the mislabel the last review caught) and doesn't know aggregate_count questions are assertable — document both keys in the README"}
]}
```
