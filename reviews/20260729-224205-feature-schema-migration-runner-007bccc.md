# Agent review — approve

date: 2026-07-29T22:45:01.926752
range: origin/main..HEAD
branch: feature/schema-migration-runner
findings: 1

- **minor** `traceability-plan.md:144` — Tracked plan doc still describes the retired _ensure_videos_columns mechanism in present tense; this commit removed it in favor of the migration runner.
  - scenario: A future session debugging the trace-link column reads traceability-plan.md's as-built note, greps for _ensure_videos_columns, finds nothing in src/, and has to re-derive that _m1_last_ingest_run_id in the MIGRATIONS list is its successor. Fix: update the sentence to name the migration runner (schema.py MIGRATIONS / _m1_last_ingest_run_id) in this same change.

---

## Full review

Review complete. This commit is the second round for this branch: the prior review (`reviews/20260729-223203-...-147ba1d.md`, request_changes) had four findings, and I verified each is genuinely fixed in this version — indexes are now built after `_run_migrations` (with the ordering rationale in both the recipe comment and an inline comment), `user_version` is re-read inside the `BEGIN IMMEDIATE` transaction so a racing loser skips instead of re-stamping backward, COORDINATION.md carries a dated entry, and the test docstring is now self-sufficient (no dangling WS-1 reference).

What I checked beyond the prior findings, all of which dissolved:

- **Transaction mechanics**: `conn.commit()` before each explicit `BEGIN IMMEDIATE` correctly clears any implicit transaction under Python's legacy isolation mode; `PRAGMA user_version` writes and `ALTER TABLE` are both transactional in SQLite, so the rollback test (`test_failed_migration_rolls_back_atomically`) verifies real behavior, and its monkeypatch ordering is correct (`SCHEMA_VERSION` patched to `len(S.MIGRATIONS)` *after* the list is extended).
- **Caller safety**: every store (`segments`, `tracks`, `actions`, `detections`, `transcripts`, `ocr`, `observations`, `catalog_sqlite`) opens via `schema.connect()`, so `apply_schema`'s internal commits can never swallow a caller's in-flight transaction.
- **Upgrade paths**: pre-versioning DBs that already got `last_ingest_run_id` via the retired `_ensure_videos_columns` convert cleanly (idempotent `add_column`, version stamped to 1); a newer-than-code DB warns and proceeds, which COORDINATION.md documents.
- **Combination coverage**: the change is backend/config-agnostic (one shared `connect()` path for every role × stub/real × config dir), so stub-path tests are the right coverage here. `schema.py` is on `scripts/critical_paths.txt`, so CI will require the `human-reviewed` label at PR time — that's the existing gate doing its job, not a gap.
- **Plan conformance**: WS-1 §6-a items (versioned runner, retire `_ensure_videos_columns`, tests) are delivered; §6-b (provenance, reprocess, shard tagging) is explicitly marked "STILL OPEN, tracked separately" in the plan, so its absence here is scoped, not a gap.
- **Test integrity**: `test_trace_link.py` changes are comment-only; the new suite covers fresh-create, old-DB migrate with row preservation, fresh-vs-migrated schema equivalence (the drift guard), idempotence, and atomic rollback.

I could not execute the test suite in this session (the pytest command required approval), so suite health rests on the CI `offline-tests` gate; the prior review ran it green (437 passed / 2 skipped) on a near-identical tree.

One minor finding survives: this commit retires `_ensure_videos_columns`, but the tracked `traceability-plan.md` (line 144) still describes, in present tense, that "Existing DBs get the column via an additive `ALTER` in `apply_schema` (`_ensure_videos_columns`)" — a future session following that as-built note will find the mechanism gone. Safe path: a one-line touch-up in that doc pointing at the migration runner (`_m1_last_ingest_run_id` / `MIGRATIONS`), in this same change. The untracked plan docs also mention the old helper, but they are outside the committed tree and this review's scope.

The provisional `need_agent_review:` subject is exempt from the commit-clarity rule.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "traceability-plan.md", "line": 144, "issue": "Tracked plan doc still describes the retired _ensure_videos_columns mechanism in present tense; this commit removed it in favor of the migration runner.", "scenario": "A future session debugging the trace-link column reads traceability-plan.md's as-built note, greps for _ensure_videos_columns, finds nothing in src/, and has to re-derive that _m1_last_ingest_run_id in the MIGRATIONS list is its successor. Fix: update the sentence to name the migration runner (schema.py MIGRATIONS / _m1_last_ingest_run_id) in this same change."}]}
```
