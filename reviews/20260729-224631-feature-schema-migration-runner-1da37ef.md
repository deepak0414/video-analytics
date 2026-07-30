# Agent review — approve

date: 2026-07-29T22:49:52.278000
range: origin/main..HEAD
branch: feature/schema-migration-runner
findings: 2

- **minor** `src/va/storage/structured/schema.py:233` — The newer-DB-than-code path (warn + proceed without downgrading user_version) has no test, despite being behavior COORDINATION.md now promises the other agent.
  - scenario: A future edit inverts the fast-path version comparison or turns the warning into a raise; the suite stays green and the regression ships, breaking the advertised open-a-newer-workdir behavior. Fix: add a test that stamps user_version above SCHEMA_VERSION, reopens via connect(), and asserts (caplog) the warning fired and the version is unchanged.
- **minor** `src/va/storage/structured/schema.py:262` — connect() uses sqlite3's default 5s busy timeout while the migration runner's contract says concurrent openers serialize on the BEGIN IMMEDIATE lock — a migration longer than 5s makes the losing process error out instead of waiting.
  - scenario: A future data-backfill migration (explicitly invited by the in-file recipe) takes >5s on a large corpus; the web server opening the same workdir DB during the CLI's migration raises sqlite3.OperationalError 'database is locked' and its connect() fails. Fix: pass an explicit generous timeout (e.g. sqlite3.connect(p, timeout=60)) or document a migration-duration cap in the recipe.

---

## Full review

Review complete. This is round three for this branch; I verified all five prior-round findings are genuinely fixed at this HEAD, ran the full offline suite green myself (437 passed / 2 skipped, including the 8 migration tests), and empirically checked the paths the tests don't reach. Two minor findings survive.

**What I verified independently:**

- **Prior findings all fixed.** Indexes are now built after `_run_migrations` (`schema.py:245-249`) with the ordering rationale in both the recipe comment and inline; `user_version` is re-read inside the `BEGIN IMMEDIATE` transaction (`schema.py:212`) so a racing loser skips instead of re-stamping backward; COORDINATION.md carries the dated storage entry; the test docstring no longer dangles a WS-1 reference; and `traceability-plan.md:143-145` now names the migration runner instead of the retired `_ensure_videos_columns`.
- **Transaction mechanics are sound.** `PRAGMA user_version` and `ALTER TABLE` are both transactional in SQLite; the `conn.commit()` before each explicit `BEGIN IMMEDIATE` clears Python's implicit transaction; the rollback test exercises real behavior (a partial `ALTER` genuinely disappears on rollback, and the committed v1 sticks). The monkeypatch ordering in that test is correct.
- **Caller safety.** All eight structured stores open via `schema.connect()`. Two direct `sqlite3.connect` calls exist (`pipeline/manage.py:72`, `pipeline/text_index.py:39`) but both predate this diff, touch only pre-existing tables, and never run DDL — unaffected.
- **Plan conformance.** `crowdsourced-eval-plan.md` §6-a is delivered exactly: `user_version`, ordered per-transaction migrations on `connect()`, `_ensure_videos_columns` retired into `_m1_last_ingest_run_id`. §6-b (provenance/reprocess) is explicitly tracked separately.
- **Combination coverage.** The change sits below every role/backend/config seam — one shared `connect()` path — so stub-path tests are the right coverage. `schema.py` is a critical path; the `human-reviewed` label gate applies at PR time as designed.
- **Newer-DB path works as advertised** (verified live: warns, proceeds, does not stamp the version backward) — but see finding 1.

**Findings:**

1. **minor** — `src/va/storage/structured/schema.py:233` — the newer-DB-than-code path (warn + proceed, never downgrade) has zero test coverage, yet it is exactly the behavior COORDINATION.md now promises the other agent. If a future edit inverts the fast-path condition or turns the warning into a raise, nothing catches it; I had to verify it by hand. Safe path: one small test that stamps `user_version` above `SCHEMA_VERSION`, reopens, and asserts the warning fired and the version was not lowered.

2. **minor** — `src/va/storage/structured/schema.py:262` — `sqlite3.connect(p)` keeps the default 5-second busy timeout while `_run_migrations`' docstring promises concurrent openers "serialize on the lock"; the recipe explicitly invites data-backfill migrations, and the first one that runs longer than 5 s on a large corpus makes every concurrently-opening process (e.g. the web server while a CLI ingest migrates) fail `connect()` with `sqlite3.OperationalError: database is locked` instead of waiting. Today's migration is milliseconds, so this is latent — same future-triggered class as the prior index-ordering finding, but transient rather than corpus-breaking. Safe path: pass an explicit generous timeout (e.g. `sqlite3.connect(p, timeout=60)`) or add a line to the recipe capping migration duration.

Things I checked that dissolved: no silent-hardcoding or determinism-as-correctness issues (this is pure mechanism, no content); best-effort ingest semantics untouched; `test_trace_link.py` change is comment-only, nothing weakened; the module-level `assert` is backed by `test_migration_count_matches_version` so `-O` stripping is covered; a newer-DB with `has_videos` absent is unreachable in practice and harmless if forced (empty migration range, no downgrade); the provisional `need_agent_review:` subject is exempt from the commit-clarity rule.

Verdict: approve — both findings are minor.

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "src/va/storage/structured/schema.py", "line": 233, "issue": "The newer-DB-than-code path (warn + proceed without downgrading user_version) has no test, despite being behavior COORDINATION.md now promises the other agent.", "scenario": "A future edit inverts the fast-path version comparison or turns the warning into a raise; the suite stays green and the regression ships, breaking the advertised open-a-newer-workdir behavior. Fix: add a test that stamps user_version above SCHEMA_VERSION, reopens via connect(), and asserts (caplog) the warning fired and the version is unchanged."},
{"severity": "minor", "file": "src/va/storage/structured/schema.py", "line": 262, "issue": "connect() uses sqlite3's default 5s busy timeout while the migration runner's contract says concurrent openers serialize on the BEGIN IMMEDIATE lock — a migration longer than 5s makes the losing process error out instead of waiting.", "scenario": "A future data-backfill migration (explicitly invited by the in-file recipe) takes >5s on a large corpus; the web server opening the same workdir DB during the CLI's migration raises sqlite3.OperationalError 'database is locked' and its connect() fails. Fix: pass an explicit generous timeout (e.g. sqlite3.connect(p, timeout=60)) or document a migration-duration cap in the recipe."}
]}
```
