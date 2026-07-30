# Agent review — request_changes

date: 2026-07-30T15:46:39.268691
range: origin/main..HEAD
branch: feature/provenance-identity
findings: 2

- **major** `src/va/pipeline/manage.py:25` — _ROLE_TABLES was not extended with the new role_provenance table, so va remove / reingest (the CLAUDE.md 'delete a video everywhere' contract) will leave orphaned provenance rows once anything writes the table
  - scenario: PROV-3 (or any ProvenanceStore caller) stamps rows for a video; the user runs va remove or va reingest; the video's provenance rows survive under the now-deleted video_id (FKs are unenforced), and va stale / the batch reprocess scanning role_provenance by role reports ghost videos. Fix: add role_provenance to _ROLE_TABLES in this change and assert the purge in the remove test.
- **minor** `provenance-reprocess-plan.md:63` — The PROV-2 spec still lists a `dim` column, but the implemented table drops it (adding fps, renaming rows to row_count) and the status entry records only the fps addition, not the dim-drop decision
  - scenario: The PROV-4 / RPRC implementer reads the spec line, expects role_provenance.dim for embedder staleness checks, and either blocks or re-adds a redundant column; safe path: update the spec line or record the drop rationale (dim lives on the TAG-2 shard tag) in the status entry.

---

## Full review

Test execution is blocked in this session (sandbox denies pytest), so this is a static review; the new tests read as sound against the `connect()`/`Config` APIs they use. I've verified everything else I need. Summary of the review:

## Review summary

The range contains two commits: `8097417` (PROV-1, finalized — the fingerprint helper, already through four review rounds whose majors are visibly fixed in the final version: `load.model` checkpoint test, credential exclusion, role-level salient keys, default-vocab fold test) and `a47849a` (PROV-2 provisional — schema v2 `role_provenance` table, migration, `ProvenanceStore`, tests, COORDINATION.md entry).

The migration machinery is correct: `apply_schema` creates the table via `ALL_TABLES` on the slow path, `_m2` is an idempotent no-op backstop, indexes build after migrations, the version-count assertion holds, and `test_migrations.py` was correctly *generalized* (not weakened) to inject its failing migration after all real ones. The store round-trips via `connect()`'s `sqlite3.Row` factory; upsert semantics match the "latest processing wins" doc. COORDINATION.md was logged in the same change and its description matches the DDL.

Two findings survived verification:

**Major — `va remove` no longer deletes everywhere.** `src/va/pipeline/manage.py:25` `_ROLE_TABLES` (comment: "every per-role table keyed by video_id (schema.py)") was not extended with `role_provenance`. CLAUDE.md's contract for `va remove` is "delete a video everywhere", and `reingest` = remove + ingest. Once PROV-3 stamps rows (or any caller uses the `ProvenanceStore` this commit ships), removing a video leaves its provenance rows orphaned under a dead `video_id`; a reingested video gets a fresh id, so `va stale`/the batch reprocess scanning `role_provenance(role)` via the new index sees ghost videos. No later plan item owns this list, so it ships broken silently. Safe path: add `role_provenance` to `_ROLE_TABLES` in this change and extend the remove test to assert the table is purged.

**Minor — plan-spec column drift recorded nowhere.** The PROV-2 spec (`provenance-reprocess-plan.md:63-64`) defines the table with a `dim` column; the implemented table drops `dim` (and renames `rows`→`row_count`, adds `fps`). The status entry records the `fps` addition but not the `dim` drop or its rationale (presumably redundant with TAG-2 shard tagging). A PROV-4/RPRC implementer working from the spec line will expect `dim`. Safe path: edit the spec line or state the drop decision in the status entry.

Suspicions checked that dissolved: FK enforcement is off in `connect()` so store tests inserting unknown `video_id`s are consistent with the rest of the repo; the fast-path version check handles newer-DB downgrade with a warning; `_COLS` is a constant so the f-string SQL is safe; the schema change is config-independent so stub/real combinations are equally covered by the pure-SQLite tests; `need_agent_review:` subject is exempt from the clarity rule and the finalized PROV-1 subject is self-sufficient; no new env var/CLI/config surface needing docs; the workflow-trust-plan dispute ledger has no dispute touching these findings.

Verdict: **request_changes** (one major finding).

```json
{"verdict": "request_changes", "findings": [
{"severity": "major", "file": "src/va/pipeline/manage.py", "line": 25, "issue": "_ROLE_TABLES was not extended with the new role_provenance table, so va remove / reingest (the CLAUDE.md 'delete a video everywhere' contract) will leave orphaned provenance rows once anything writes the table", "scenario": "PROV-3 (or any ProvenanceStore caller) stamps rows for a video; the user runs va remove or va reingest; the video's provenance rows survive under the now-deleted video_id (FKs are unenforced), and va stale / the batch reprocess scanning role_provenance by role reports ghost videos. Fix: add role_provenance to _ROLE_TABLES in this change and assert the purge in the remove test."},
{"severity": "minor", "file": "provenance-reprocess-plan.md", "line": 63, "issue": "The PROV-2 spec still lists a `dim` column, but the implemented table drops it (adding fps, renaming rows to row_count) and the status entry records only the fps addition, not the dim-drop decision", "scenario": "The PROV-4 / RPRC implementer reads the spec line, expects role_provenance.dim for embedder staleness checks, and either blocks or re-adds a redundant column; safe path: update the spec line or record the drop rationale (dim lives on the TAG-2 shard tag) in the status entry."}
]}
```
