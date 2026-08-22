# Agent review — approve

date: 2026-08-21T22:04:18.741463
range: origin/main..HEAD
branch: feature/quarantined-ingest-status
findings: 3

- **minor** `src/va/pipeline/ingest.py:241` — The documented un-quarantine path `va reingest` does not re-pull NVR footage — reingest_video parks the preserved media in cache/ and re-runs roles on the same bytes — so it re-admits the exact contaminated clip the quarantine exists to exclude (COORDINATION.md:857 repeats the 'remove + fresh ingest' claim).
  - scenario: Operator runs `va reingest` on one of the four quarantined 352x240 sub-stream clips with VA_NVR_MAIN_STREAM unset; the verifier's stream check is inactive, the parked cut is re-indexed, and the row flips to done. Safe path: recommend `va remove` + fresh `va ingest nvr://…`, or make reingest_video refuse/skip the cache-park for quarantined rows.
- **minor** `src/va/contracts/video.py:30` — The enum contrasts quarantined with done '(indexed and searchable)', but no read path filters on ingest_status — quarantine is enforced only by the out-of-band row/shard purge the repair happened to do.
  - scenario: The named future writer (WS-4 verifier) or any caller does Catalog.set_status(id, quarantined) on an ingested clip (exactly what the new tests do); `va query`/caption/ocr still return its frames while the catalog says it is excluded. Safe path: document quarantine as status + purge and add a manage.quarantine_video() helper (remove_video minus the catalog delete), or gate read paths on status.
- **minor** `src/va/cli.py:21` — A quarantined dedup prints `[already-ingested] … frames=0` (and with --profile a note pointing at `va reingest`), and CLAUDE.md carries no gotcha that a quarantined source no-ops on ingest.
  - scenario: Operator re-ingests a quarantined nvr:// window, reads 'already-ingested', and assumes it is indexed; nothing tells them it is excluded or why. Safe path: print `[quarantined]` from the result and add a one-line gotcha beside `va remove`/`va reingest` in CLAUDE.md.

---

## Full review

I wasn't able to run the test files here (the pytest invocation needs approval in this review context), so the test assessment below is by inspection; the post-commit/CI gates will execute them.

## Review: `0ab3af6 need_agent_review: make quarantined a first-class IngestStatus`

**What the change does:** adds `IngestStatus.quarantined`, makes `va ingest` of a quarantined `source_key` a dedup no-op, makes `plan_reprocess --video <quarantined>` refuse with a specific message, and pins all of it (plus the `Catalog.list()` round-trip that previously raised) with five tests. Logged in COORDINATION.md.

**What checks out:**
- `Catalog._from_row` → `IngestStatus(row)` is the actual crash site; the enum member fixes it and `test_quarantined_status_round_trips` inserts the raw row the way the repair did, so it fails on old code (`ValueError`). `test_reingest_of_quarantined_is_a_noop` would also fail on old code (status ≠ done → roles re-run → `deduped=False`).
- `stale_report` / `plan_reprocess --all-stale` already done-filter, so exclusion falls out; the new tests construct the drift first, so they're not decoration.
- `va watch` treats the dedup as a free replay (watch.py:230) and advances the watermark — correct for a quarantined window. Durable web jobs report `deduped` honestly. `footage_domains` / `window_anchoring` already done-filter, so the aggregate tier ignores quarantined rows.
- Backend-agnostic: no role × backend × profile variation.

**Findings (all minor — none invalidates the change):**

1. **minor — `src/va/pipeline/ingest.py:241` (and COORDINATION.md:857)** — The offered un-quarantine path, `va reingest`, is described as "remove + fresh ingest"/re-pull, but `reingest_video` (manage.py:113-127) for `nvr_recorded` deliberately parks the preserved media in `cache/` and re-runs the roles on the **same bytes** with no re-pull. Scenario: operator runs `va reingest` on one of the four 352x240 wholly-foreign clips (media kept); with `VA_NVR_MAIN_STREAM` unset the verifier's sub-stream check is inactive, the cut is re-indexed and the row flips to `done` — exactly the re-admission the quarantine comment says must not happen. Safe path: word the escape hatch as `va remove` + fresh `va ingest nvr://…` (remove_video rmtree's the video dir, so fetch() genuinely re-pulls), and/or have `reingest_video` refuse to cache-park a quarantined row.

2. **minor — `src/va/contracts/video.py:30`** — The docstring contrasts quarantined with `done` "(indexed and searchable)", but no read path (`query.py`, caption/transcript/ocr/actions, ask's gather) filters on `ingest_status`; quarantine is enforced today only because the repair cleared rows/shards out-of-band. Scenario: the named future writer (the WS-4 verifier) or anyone calling `Catalog.set_status(id, quarantined)` — which is precisely what the new tests do on a fully-ingested clip — leaves the clip fully searchable while the catalog says it's excluded. Safe path: either state in the enum comment that quarantine = status **+** purge of role rows/shards (and give the writer a `quarantine_video()` helper in manage.py mirroring `remove_video` minus the catalog delete), or gate the read paths on status.

3. **minor — `src/va/cli.py:21` (doc parity)** — A quarantined dedup prints `[already-ingested] … frames=0` (and, with `--profile`, a note pointing at `va reingest` — see #1), so the operator cannot tell the clip is excluded rather than indexed; CLAUDE.md has no mention that a quarantined source silently no-ops on ingest. Safe path: surface the status in the ingest result line (`[quarantined]`) and add a one-line gotcha next to `va remove`/`va reingest` in CLAUDE.md.

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "src/va/pipeline/ingest.py", "line": 241, "issue": "The documented un-quarantine path `va reingest` does not re-pull NVR footage — reingest_video parks the preserved media in cache/ and re-runs roles on the same bytes — so it re-admits the exact contaminated clip the quarantine exists to exclude (COORDINATION.md:857 repeats the 'remove + fresh ingest' claim).", "scenario": "Operator runs `va reingest` on one of the four quarantined 352x240 sub-stream clips with VA_NVR_MAIN_STREAM unset; the verifier's stream check is inactive, the parked cut is re-indexed, and the row flips to done. Safe path: recommend `va remove` + fresh `va ingest nvr://…`, or make reingest_video refuse/skip the cache-park for quarantined rows."},
{"severity": "minor", "file": "src/va/contracts/video.py", "line": 30, "issue": "The enum contrasts quarantined with done '(indexed and searchable)', but no read path filters on ingest_status — quarantine is enforced only by the out-of-band row/shard purge the repair happened to do.", "scenario": "The named future writer (WS-4 verifier) or any caller does Catalog.set_status(id, quarantined) on an ingested clip (exactly what the new tests do); `va query`/caption/ocr still return its frames while the catalog says it is excluded. Safe path: document quarantine as status + purge and add a manage.quarantine_video() helper (remove_video minus the catalog delete), or gate read paths on status."},
{"severity": "minor", "file": "src/va/cli.py", "line": 21, "issue": "A quarantined dedup prints `[already-ingested] … frames=0` (and with --profile a note pointing at `va reingest`), and CLAUDE.md carries no gotcha that a quarantined source no-ops on ingest.", "scenario": "Operator re-ingests a quarantined nvr:// window, reads 'already-ingested', and assumes it is indexed; nothing tells them it is excluded or why. Safe path: print `[quarantined]` from the result and add a one-line gotcha beside `va remove`/`va reingest` in CLAUDE.md."}
]}
```
