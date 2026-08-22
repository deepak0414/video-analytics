# Agent review — approve

date: 2026-08-21T22:06:46.651036
range: origin/main..HEAD
branch: feature/quarantined-ingest-status
findings: 3

- **minor** `src/va/pipeline/ingest.py:241` — The advertised un-quarantine path `va reingest` does not re-pull NVR footage — reingest_video (manage.py:113-127) parks the preserved media in cache/ and re-runs roles on the same bytes — so it re-admits the exact contaminated clip; COORDINATION.md:857 and the cli.py:23 --profile note repeat the claim.
  - scenario: Operator runs `va reingest` on one of the four quarantined 352x240 sub-stream clips with VA_NVR_MAIN_STREAM unset; the verifier's stream check is inactive, the parked cut is re-indexed, and the row flips to done. Safe path: word the escape hatch as `va remove` + fresh `va ingest nvr://…` (as reprocess.py:77 already does), or make reingest_video refuse/skip the cache-park for quarantined rows.
- **minor** `src/va/contracts/video.py:30` — The enum contrasts quarantined with done '(indexed and searchable)', but no read path filters on ingest_status (ShardedVectorStore globs */vectors.npz; caption/transcript/ocr/actions stores unfiltered) — quarantine is enforced only by the out-of-band row/shard purge the repair happened to do.
  - scenario: The named future writer (WS-4 verifier) or any caller does Catalog.set_status(id, quarantined) on an ingested clip (exactly what the new tests do); `va query`/caption/ocr still return its frames while the catalog says it is excluded. Safe path: document quarantine as status + purge and add a manage.quarantine_video() helper (remove_video minus the catalog delete), or gate read paths on status.
- **minor** `src/va/cli.py:21` — A quarantined dedup prints `[already-ingested] … frames=0`; CLAUDE.md:423 still names `done` as the sole ingest idempotency point and the COORDINATION.md:22 contract table row still says 'idempotent on done' (only the log entry was updated).
  - scenario: Operator re-ingests a quarantined nvr:// window, reads 'already-ingested', and assumes it is indexed; the next session reading CLAUDE.md/the contract table doesn't learn that quarantined also dedups. Safe path: print `[quarantined]` from the result status and add the one-line note to CLAUDE.md's ingest description and the contract table row.

---

## Full review

## Review: `0ab3af6 need_agent_review: make quarantined a first-class IngestStatus`

**Scope reviewed:** the single commit `origin/main..HEAD` (enum member, ingest dedup branch, reprocess refusal, 5 tests, COORDINATION log entry). I could not execute pytest in this context (needs approval; a full suite is also already live, PID 3584414), so test assessment is by inspection.

**What checks out**
- The crash site is real: `Catalog._from_row` → `IngestStatus(r["ingest_status"])` (`catalog_sqlite.py:77`) raised on the repair's raw `'quarantined'` rows; the enum member fixes `list()`/`get*`, hence `/api/videos`, `va migrate-layout`, and the ask catalog fallback. `test_quarantined_status_round_trips` inserts the row the way the repair did and fails on old code — not decoration.
- `get_or_create` returns the stored row without resetting status (`catalog_sqlite.py:156-161`), so the new `ingest.py:237` branch is reachable; `test_reingest_of_quarantined_is_a_noop` would fail on old code (non-done → roles re-run → `deduped=False`).
- `stale_report`/`plan_reprocess --all-stale` already done-filter (`stale.py:74`); the new tests construct drift first, so exclusion is actually exercised. `footage_domains`/`window_anchoring` (`tracks.py:172`, `catalog_sqlite.py:127`) already done-filter, so the aggregate tier ignores quarantined rows.
- `va watch` treats the dedup as a free replay and advances the watermark (`watch.py:230`) — right for a quarantined window. Durable web jobs report `deduped` as-is. No role×backend×profile variation — the change is catalog-level.
- COORDINATION.md append-only log entry is present and accurate about the additive nature.

**Findings — all minor, none invalidates the change**

1. **minor — `src/va/pipeline/ingest.py:241-242` (also `COORDINATION.md:857`, `src/va/cli.py:23`)** — The advertised escape hatch `va reingest` is described as "remove + fresh ingest", but for `nvr_recorded` rows `reingest_video` (`manage.py:113-127`) deliberately parks the preserved media in `cache/` and re-runs roles on the **same bytes** — no re-pull. Scenario: operator runs `va reingest` on one of the four 352×240 wholly-foreign clips (media kept by the repair); with `VA_NVR_MAIN_STREAM` unset the verifier's sub-stream check is inactive, so the contaminated cut is re-indexed and flips to `done` — exactly the re-admission the quarantine comment says must not happen. (`reprocess.py:77` already words it correctly as "`va remove` it or re-pull deliberately".) Safe path: align ingest.py/COORDINATION/the `--profile` note on `va remove` + fresh `va ingest nvr://…`, or make `reingest_video` refuse (or skip the cache-park) for quarantined rows.

2. **minor — `src/va/contracts/video.py:30-34`** — The enum contrasts quarantined with `done` "(indexed and searchable)", but no read path filters on `ingest_status`: `ShardedVectorStore` globs `*/vectors.npz` (`sharded.py:80`), and caption/transcript/ocr/actions stores are unfiltered. Quarantine is enforced today only because the repair purged rows/shards out-of-band. Scenario: the named future writer (WS-4 verifier) or any caller does `Catalog.set_status(id, quarantined)` on an ingested clip — precisely what the new tests do — and `va query`/caption/ocr keep returning its frames while the catalog says it is excluded. Safe path: state in the enum comment that quarantine = status **+** purge and add a `manage.quarantine_video()` helper (`remove_video` minus the catalog delete) for the writer, or gate read paths on status.

3. **minor — `src/va/cli.py:21` / `CLAUDE.md:423` / `COORDINATION.md:22` (doc parity)** — A quarantined dedup prints `[already-ingested] … frames=0`, so the operator cannot tell the clip is excluded rather than indexed; CLAUDE.md still names `done` as the sole idempotency point and the COORDINATION contract table row still reads "idempotent on `done`" (only the log entry was updated). Safe path: print `[quarantined]` from the result status, and add the one-line note in CLAUDE.md's ingest description and the contract table row.

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "src/va/pipeline/ingest.py", "line": 241, "issue": "The advertised un-quarantine path `va reingest` does not re-pull NVR footage — reingest_video (manage.py:113-127) parks the preserved media in cache/ and re-runs roles on the same bytes — so it re-admits the exact contaminated clip; COORDINATION.md:857 and the cli.py:23 --profile note repeat the claim.", "scenario": "Operator runs `va reingest` on one of the four quarantined 352x240 sub-stream clips with VA_NVR_MAIN_STREAM unset; the verifier's stream check is inactive, the parked cut is re-indexed, and the row flips to done. Safe path: word the escape hatch as `va remove` + fresh `va ingest nvr://…` (as reprocess.py:77 already does), or make reingest_video refuse/skip the cache-park for quarantined rows."},
{"severity": "minor", "file": "src/va/contracts/video.py", "line": 30, "issue": "The enum contrasts quarantined with done '(indexed and searchable)', but no read path filters on ingest_status (ShardedVectorStore globs */vectors.npz; caption/transcript/ocr/actions stores unfiltered) — quarantine is enforced only by the out-of-band row/shard purge the repair happened to do.", "scenario": "The named future writer (WS-4 verifier) or any caller does Catalog.set_status(id, quarantined) on an ingested clip (exactly what the new tests do); `va query`/caption/ocr still return its frames while the catalog says it is excluded. Safe path: document quarantine as status + purge and add a manage.quarantine_video() helper (remove_video minus the catalog delete), or gate read paths on status."},
{"severity": "minor", "file": "src/va/cli.py", "line": 21, "issue": "A quarantined dedup prints `[already-ingested] … frames=0`; CLAUDE.md:423 still names `done` as the sole ingest idempotency point and the COORDINATION.md:22 contract table row still says 'idempotent on done' (only the log entry was updated).", "scenario": "Operator re-ingests a quarantined nvr:// window, reads 'already-ingested', and assumes it is indexed; the next session reading CLAUDE.md/the contract table doesn't learn that quarantined also dedups. Safe path: print `[quarantined]` from the result status and add the one-line note to CLAUDE.md's ingest description and the contract table row."}
]}
```
