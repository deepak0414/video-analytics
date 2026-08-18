# Agent review — approve

date: 2026-08-17T17:48:07.814597
range: origin/main..HEAD
branch: feature/typed-query-tier
findings: 0


---

## Full review

Review complete. I read the full diff (one squashed commit `7a57f75` carrying the TQ1.a–TQ1.h stack plus the batch-review fix), the plan/loop docs, the prior review at `a20319d`, and the surrounding code (`ask.py`, `retrieval.py`, `evidence.py`, `nvr.py`, `catalog_sqlite.py`, all eight new test files). I could not execute pytest in this session (invocation not permitted), so I verified behavior by reading the tests and by independent read-only SQL against `.va-24h`.

## What I verified and did not report

- **The prior review's major finding is genuinely fixed.** The `a20319d` review flagged the silent A-EV false-zero. HEAD adds `TrackStore.window_anchoring`, the NOT-APPLICABLE caveat leading the caveat list, the partial-exclusion caveat plus the NB appended to the CODE-COUNTED line itself, planner-path degrade (no `[CODE-COUNTED: 0]` on un-windowable workdirs), CLI-help and CLAUDE.md disclosure, and five tests that construct the scenario (A-EV-only dispatch, A-EV-only `ask`, mixed placed/unplaced, CLI NOT-APPLICABLE). The fix covers dispatch, CLI, and `ask` surfaces, and is logged in COORDINATION.md.
- **Ground truth independently reproduced.** I re-derived the epoch bounds by hand (Aug-11 2026 00:00/12:00 PDT → 1786431600/1786474800) and ran my own SQL against `.va-24h`: nvr-ch1 22 / nvr-ch2 55 / total 77 — exactly what the golden fixture and the TQ1.g digest claim. The fixture's gating `source_key` (`nvr:ch1:1786434359-1786434396`) exists as a `done` row, so the golden test will not silently always-skip. The `hand-sql-crosscheck` provenance label honestly states it pins tracker output, not footage truth — that satisfies the determinism≠correctness rule rather than violating it.
- **Time math**: half-open [start, end) membership, PEP-495 DST-gap diagnosis, aware-datetime offset handling, the `math.ceil` bucket allocation (fractional-span test constructs the failing idiom), and the strftime-TEXT trap are each pinned by a test that constructs its scenario, including a regression test that runs the broken strftime SQL form and proves it returns 0 where the numeric truth is 4.
- **No contract breaks**: everything is additive (new module, new `QueryPlan` field with default, new modality string, new `TrackStore` methods, new CLI subcommand group); no DB schema change, so no migration needed; each piece is logged in COORDINATION.md with ⚠ markers on the shared-file entries.
- **No hidden hardcoded content**: `resolve_category` is the pre-existing plural-strip promoted verbatim (parity table test); synonym expansion is explicitly deferred to the human-gated TQ1.b2/Role-12 decision; the named heuristics (min_frames=2, 1h bucket, 10k-bucket cap, "(no camera)" key) are flagged as structure in comments and COORDINATION.md.
- **Degrade discipline**: 11 parametrized bad-argument shapes (including `cameras=[]`, null/list `limit`, unknown tz/op, bad bucket) all produce one honest note and zero items — never a number. Best-effort semantics preserved; nothing new can abort ingest (this tier is read-only).
- **Prompt/registry drift**: `PLANNER_PROMPT` renders from `AGGREGATION_TOOLS` at import, brace-escaped; the drift test actually calls `.format()`. No circular import (`aggregate` → contracts/storage only). The `ask()` lead guard keys on the specific aggregate item's content, with a quoting-narrator regression test.
- **Docs parity**: no new env vars; new CLI flags in `--help` + CLAUDE.md commands block; the golden `modality:` key and `hand-sql-crosscheck` provenance documented in the golden README; Role-11 prose updated.
- Suspicions I chased that dissolved: double dispatch (only `retrieve()` runs in `ask`; `assemble()` is the legacy alternative, both covered by tests), `window_anchoring`'s all-time unplaced count (its caveat wording stays accurate — unanchored tracks are excluded from *any* window), `"glass"→"gla"` from `rstrip("s")` (pre-existing, extra candidate matches nothing), `trace(level=, items=)` kwargs (established pattern in the same file), the `total 0` line printed on A-EV-only CLI output (never *bare* — the NOT-APPLICABLE caveat prints with it, test-pinned).

The commit subject is a provisional `need_agent_review:` and therefore exempt from the plain-description rule; the eventual finalize digest should note the branch needs both attestation labels (`human-reviewed` for `contracts/query_plan.py` + `cli.py`, `golden-verified` for `pipeline/ask.py`/`retrieval.py`), which the loop file already records.

No findings survived verification.

```json
{"verdict": "approve", "findings": []}
```
