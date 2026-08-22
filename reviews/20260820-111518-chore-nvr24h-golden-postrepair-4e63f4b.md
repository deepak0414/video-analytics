# Agent review — approve

date: 2026-08-20T11:17:44.534776
range: origin/main..HEAD
branch: chore/nvr24h-golden-postrepair
findings: 2

- **minor** `va-24h-data-integrity-investigation.md:4` — Header claims the session was read-only and no workdir file was modified, but the appended 'Repair as executed (2026-08-19)' section in the same doc records in-place edits to .va-24h (4 quarantines, 39 tracks dropped, 5 start_epochs rewritten); scope the header to the investigation phase.
  - scenario: A reader stops at the header, concludes .va-24h is untouched forensic evidence, and treats the 72-vs-77 discrepancy as a count-tier regression rather than the documented repair.
- **minor** `va-24h-data-integrity-investigation.md:473` — The repaired golden target workdir carries ingest_status='quarantined' (4 rows), outside contracts.IngestStatus, so Catalog.list() raises on .va-24h; the doc's follow-up note lists web /api/videos and va migrate-layout but omits va stale / va reprocess --all-stale (stale.py:70 calls cat.list()) — re-mark the rows failed+ingest_error or add and handle the enum member.
  - scenario: `va --workdir .va-24h stale` (or `reprocess --all-stale`) → ValueError("'quarantined' is not a valid IngestStatus") instead of a stale report; `va serve --workdir .va-24h` GET /api/videos → 500. The fixture's own path (count_objects → footage_domains done-only SQL) is unaffected, verified total=72.

---

## Full review

Review complete. The pinned value holds up independently: my own hand SQL over the repaired `.va-24h` gives ch1 21 / ch2 51 = 72, and the real `count_objects` code path returns `total=72, per_camera {nvr-ch1: 21, nvr-ch2: 51}` for the fixture's window. The gating `source_key` chunk is still `done`, the fixture parses, no offline test consumes it, and the tracked doc carries no host/credential. Two of the prior review's three findings (loop-doc note, "repair as executed" section) are addressed in this HEAD; the third (the `quarantined` status) is documented rather than fixed. Commit subject is a provisional `need_agent_review:` (rule 8 exempt).

**Findings (all minor)**

1. `va-24h-data-integrity-investigation.md:3-5` — the header still asserts "Read-only … No workdir file … was modified," which the newly appended "Repair as executed (2026-08-19)" section (lines 464–489) contradicts: the same doc now records in-place data edits (4 quarantines, 39 tracks dropped, 5 `start_epoch`s rewritten). Safe path: scope the header to the investigation phase ("§1–§5 were read-only; the in-place repair is recorded at the end").
2. `va-24h-data-integrity-investigation.md:473` (carried forward, acknowledged by the author) — the golden target workdir now holds `ingest_status='quarantined'` on 4 rows, outside `contracts.IngestStatus`, so `Catalog.list()` raises `ValueError` on `.va-24h`; the doc names web `/api/videos` and `va migrate-layout` but `va stale` (`stale.py:70`) and therefore `va reprocess --all-stale` also hit `cat.list()`. The fixture's ask path avoids `list()` (verified: `count_objects` → `footage_domains()` is a done-only SQL query; ask's deep-scan `list(limit=2)` fallback isn't reached when evidence carries video_ids). Safe path: re-mark the 4 rows `failed` + `ingest_error='quarantined: foreign sub-stream'` (one UPDATE, reversible) or add the enum member with handling, before anyone serves/stales/migrates this workdir.

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "va-24h-data-integrity-investigation.md", "line": 4, "issue": "Header claims the session was read-only and no workdir file was modified, but the appended 'Repair as executed (2026-08-19)' section in the same doc records in-place edits to .va-24h (4 quarantines, 39 tracks dropped, 5 start_epochs rewritten); scope the header to the investigation phase.", "scenario": "A reader stops at the header, concludes .va-24h is untouched forensic evidence, and treats the 72-vs-77 discrepancy as a count-tier regression rather than the documented repair."},
{"severity": "minor", "file": "va-24h-data-integrity-investigation.md", "line": 473, "issue": "The repaired golden target workdir carries ingest_status='quarantined' (4 rows), outside contracts.IngestStatus, so Catalog.list() raises on .va-24h; the doc's follow-up note lists web /api/videos and va migrate-layout but omits va stale / va reprocess --all-stale (stale.py:70 calls cat.list()) — re-mark the rows failed+ingest_error or add and handle the enum member.", "scenario": "`va --workdir .va-24h stale` (or `reprocess --all-stale`) → ValueError(\"'quarantined' is not a valid IngestStatus\") instead of a stale report; `va serve --workdir .va-24h` GET /api/videos → 500. The fixture's own path (count_objects → footage_domains done-only SQL) is unaffected, verified total=72."}
]}
```
