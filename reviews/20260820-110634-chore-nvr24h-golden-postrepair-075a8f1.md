# Agent review — approve

date: 2026-08-20T11:10:22.671733
range: origin/main..HEAD
branch: chore/nvr24h-golden-postrepair
findings: 3

- **minor** `tests/golden_queries/nvr24h_aggregate.yaml:12` — The repaired .va-24h this fixture now pins carries ingest_status='quarantined' on 4 rows, a value outside contracts.IngestStatus, so Catalog.list() (web /api/videos, va migrate-layout, deep-scan catalog.list(limit=2) fallback) raises ValueError on this workdir; the fixture's ask path avoids list() so the pin still runs, but the golden target is partially unreadable — record quarantine as status 'failed' + ingest_error (or add and handle the enum member) before anyone serves/migrates this workdir.
  - scenario: `va serve --workdir .va-24h` then GET /api/videos → ValueError("'quarantined' is not a valid IngestStatus") → 500; verified: Catalog('.va-24h/catalog.db').list() raises today.
- **minor** `typed-query-tier-loop.md:237` — Plan doc still records 77 (ch2 55 / ch1 22) as the .va-24h Aug-11-morning ground truth after this commit moved the golden to 72 (51/21); add a one-line note that the repaired workdir reads 72 and the fixture pins that.
  - scenario: Next session reconciles the loop doc's 77 against the fixture's 72, assumes the count tier regressed by 5, and wastes a debugging pass.
- **minor** `va-24h-data-integrity-investigation.md:354` — The yaml header cites this doc for the repair, but the doc only recommends one (§4) and states no workdir file was modified; the as-executed repair (4 quarantined, 92 clips head-trimmed, 39 tracks dropped / 13 adjusted, 5 start_epochs re-derived by 6–19 s, 117 embeddings dropped) exists only in the untracked .va-24h/repair_log.json — append a short 'repair as executed 2026-08-19' section summarizing it so the repo records what the golden value rests on.
  - scenario: A reader following the fixture's pointer finds only a recommendation that differs from what was done (trim-adjust vs drop, selective re-derive) and cannot reconstruct why 77 became 72 without the workdir's repair_log.json.

---

## Full review

Review complete. Summary of what I verified and found:

**Verified (dissolved suspicions)**
- The new pin **72 (ch1 21 / ch2 51)** reproduces both by independent hand SQL over the repaired `.va-24h` and through the real code path (`count_objects("car", TimeWindow(...), ".va-24h")` → 72, same per-camera split).
- The yaml's causal story ("5 spurious track starts removed") checks out against `.va-24h/repair_log.json`: 4 in-window ch2 car tracks (frame_count 3/3/2/2) dropped by the head trim + 1 ch1 track from the quarantined `ed09f4ba`; the 5 re-derived `start_epoch`s (6–19 s) didn't move anything across the window edge.
- The fixture's gating `source_key` is still `done`; the yaml parses; no offline test consumes the fixture value, so the offline suite is unaffected; the tracked doc contains no host/credential.
- Commit subject is a provisional `need_agent_review:` (exempt from rule 8).

**Findings (all minor)**
1. The golden target workdir now carries `ingest_status='quarantined'` (4 rows), which is not an `IngestStatus` member — `Catalog.list()` raises `ValueError("'quarantined' is not a valid IngestStatus")` on `.va-24h` (hits web `GET /api/videos`, `va migrate-layout`, and the deep-scan `catalog.list(limit=2)` fallback). The fixture's own ask path avoids it, so the pin is runnable, but `va serve --workdir .va-24h` will 500. This is workdir state from the (untracked) repair script rather than the diff, flagged because the fixture header explicitly rests on that quarantine.
2. `typed-query-tier-loop.md:237` still records 77 (ch2 55 / ch1 22) as the `.va-24h` ground truth (also the log/table rows 283/361/369, acceptable as history).
3. The yaml points readers to the investigation doc for the repair, but that doc only *recommends* a repair (§4) and says nothing was modified; the as-executed repair (4 quarantined, 92 clips head-trimmed, 39 tracks dropped / 13 adjusted, 5 `start_epoch`s re-derived, 117 embeddings dropped) lives only in the untracked `.va-24h/repair_log.json`.

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "tests/golden_queries/nvr24h_aggregate.yaml", "line": 12, "issue": "The repaired .va-24h this fixture now pins carries ingest_status='quarantined' on 4 rows, a value outside contracts.IngestStatus, so Catalog.list() (web /api/videos, va migrate-layout, deep-scan catalog.list(limit=2) fallback) raises ValueError on this workdir; the fixture's ask path avoids list() so the pin still runs, but the golden target is partially unreadable — record quarantine as status 'failed' + ingest_error (or add and handle the enum member) before anyone serves/migrates this workdir.", "scenario": "`va serve --workdir .va-24h` then GET /api/videos → ValueError(\"'quarantined' is not a valid IngestStatus\") → 500; verified: Catalog('.va-24h/catalog.db').list() raises today."},
{"severity": "minor", "file": "typed-query-tier-loop.md", "line": 237, "issue": "Plan doc still records 77 (ch2 55 / ch1 22) as the .va-24h Aug-11-morning ground truth after this commit moved the golden to 72 (51/21); add a one-line note that the repaired workdir reads 72 and the fixture pins that.", "scenario": "Next session reconciles the loop doc's 77 against the fixture's 72, assumes the count tier regressed by 5, and wastes a debugging pass."},
{"severity": "minor", "file": "va-24h-data-integrity-investigation.md", "line": 354, "issue": "The yaml header cites this doc for the repair, but the doc only recommends one (§4) and states no workdir file was modified; the as-executed repair (4 quarantined, 92 clips head-trimmed, 39 tracks dropped / 13 adjusted, 5 start_epochs re-derived by 6–19 s, 117 embeddings dropped) exists only in the untracked .va-24h/repair_log.json — append a short 'repair as executed 2026-08-19' section summarizing it so the repo records what the golden value rests on.", "scenario": "A reader following the fixture's pointer finds only a recommendation that differs from what was done (trim-adjust vs drop, selective re-derive) and cannot reconstruct why 77 became 72 without the workdir's repair_log.json."}
]}
```
