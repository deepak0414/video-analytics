# Agent review — approve

date: 2026-07-30T15:53:31.299788
range: origin/main..HEAD
branch: feature/provenance-identity
findings: 0


---

## Full review

Review complete. The range holds two commits: the finalized PROV-1 fingerprint helper (`8097417`, already through four prior review rounds ending in approve) and the provisional PROV-2 commit (`7ddb3ec`: `role_provenance` table, schema v2 migration, `ProvenanceStore`, `va remove` purge). I read the full diff, the schema runner, `configuration.py`, `manage.py`, and all touched tests, and ran the offline suite: **474 passed, 2 skipped** — green, including the 20 new provenance tests.

**Verdict: approve — no findings survived verification.**

What I checked and how each suspicion resolved:

- **Migration correctness.** `_m2_role_provenance` is idempotent (`CREATE TABLE IF NOT EXISTS`), runs inside the runner's per-migration `BEGIN IMMEDIATE` transaction, and the index on the new table is built after migrations per the runner's documented ordering. Fresh, v0, v1, newer-than-code, and failed-rollback paths are all tested. The concurrent-opener race (two processes migrating the same v1 DB) is handled by the existing re-read-under-lock skip.
- **`dict(r)` in `ProvenanceStore.get`** requires `row_factory = sqlite3.Row` — verified `schema.connect()` sets it. The FK `REFERENCES videos(id)` is unenforced (no `PRAGMA foreign_keys=ON`), consistent with every other role table, so store tests without catalog rows are valid, not accidental.
- **`va remove` purge ordering.** `remove_video` opens the catalog (which migrates the DB) before the raw `DELETE FROM role_provenance`, so the table is guaranteed to exist even on a pre-v2 workdir; the purge is covered by a real two-video test asserting the other video's provenance survives.
- **Test integrity.** The `test_failed_migration_rolls_back_atomically` edit is a genuine generalization — `last_good` is captured before the monkeypatch, and all three original assertions (version stuck at last good, real migration retained, failed DDL rolled back) are preserved, now version-count-agnostic instead of hardcoding v1.
- **Contract/coordination.** The schema bump is logged in COORDINATION.md with the auto-migrate behavior for both agents; an older build opening a v2 DB hits the additive-schema warning path (tested), so no downgrade hazard. The plan doc records PROV-2 done with the column decision (`fps` in, `dim` out — dim lives on the TAG-2 shard tag) that PROV-1's review note asked for.
- **PROV-1 re-check.** The prior rounds' findings (whisper `load["model"]` checkpoint, speaker bounds, role-level keys, credential exclusion, default-vocab fold) are all resolved in the finalized code and each carries a dedicated test.
- **Docs/messages.** No new env var, CLI flag, or config key — nothing owed to CLAUDE.md. The finalized PROV-1 message and the provisional PROV-2 body both describe the change plainly with IDs as trailing references; the `need_agent_review:` subject is exempt by rule.

Combination coverage is not a concern here: the schema and store are backend-agnostic (pure SQLite, no role backends involved), and nothing writes the table until PROV-3, so no config-dir-specific behavior exists yet to test.

```json
{"verdict": "approve", "findings": []}
```
