# Agent review — request_changes

date: 2026-07-29T22:37:41.875445
range: origin/main..HEAD
branch: feature/schema-migration-runner
findings: 4

- **major** `src/va/storage/structured/schema.py:236` — apply_schema creates INDEXES before running migrations, so the first future index on a migrated-in column makes connect() crash on every pre-existing workdir — and the in-file schema-change recipe leads the next author straight into it.
  - scenario: WS-2 adds videos.profile per the recipe (base DDL + migration + version bump) plus an index on it; a workdir ingested at v1 then throws sqlite3.OperationalError 'no such column: profile' during the INDEXES loop, before _m2 can ALTER the column in — reproduced empirically. Fix: call _run_migrations before the INDEXES loop and note index ordering in the recipe comment.
- **minor** `src/va/storage/structured/schema.py:202` — user_version is read outside the BEGIN IMMEDIATE lock, so a racing process re-runs completed migrations and transiently stamps user_version backwards; a crash in that window persists the downgrade.
  - scenario: Two processes open a v0 DB with SCHEMA_VERSION=2; A migrates to 2; B (which read current=0) then stamps user_version=1 and crashes before re-stamping 2 — the DB reports v1 while holding v2 schema until the next open self-heals. Fix: re-read user_version inside the transaction and skip targets already applied.
- **minor** `COORDINATION.md:41` — No log entry for the schema-versioning change to the shared catalog.db, whose open-time behavior (auto-migrate + newer-DB warning) now differs for the web agent's stores.
  - scenario: The web agent opens a workdir DB unaware it is mutated and version-stamped on open, or sees the newer-DB warning with no channel explaining it. Fix: append a dated additive-change entry per the file's own rule.
- **minor** `tests/test_migrations.py:1` — The docstring cites 'WS-1' but the covering plan doc (architecture-evolution-plan.md / crowdsourced-eval-plan.md §6-a) is untracked, so the reference resolves to nothing in the committed tree.
  - scenario: A future session reads the test file, cannot find WS-1 anywhere in git, and loses the design rationale for the runner. Fix: commit the plan doc with the change, or make the reference self-sufficient; raise placement with the human if plan docs are staged deliberately.

---

## Full review

Review complete. The suite is green (437 passed / 2 skipped, including the 7 new migration tests), the WS-1 plan items (§6-a) are genuinely delivered, and the transaction/atomicity design is sound — the rollback test verifies real behavior. But I confirmed one structural defect empirically, plus three minor items.

## Findings

**Major — index creation runs before migrations, so the first index on a migrated column breaks every existing workdir** (`src/va/storage/structured/schema.py:236-239`). `apply_schema` executes the `INDEXES` loop before `_run_migrations`. Today no index references a migrated-in column, but the very next planned schema change — WS-2's `videos.profile`, named in the plan as "via WS-1" — will follow the in-file recipe (base DDL + migration + version bump), and if it also adds an index on that column, every pre-existing workdir crashes at `connect()` with `sqlite3.OperationalError: no such column: profile`, because the old `videos` table hits the index DDL before the migration adds the column. I reproduced this exactly: a v1 DB + a recipe-conformant v2 change crashes on open. This is precisely the corpus-breaking event WS-1 exists to prevent, and even a careful author cannot avoid it without reordering — the recipe walks them into it. Safe path: move `_run_migrations(conn)` before the `INDEXES` loop (tables → migrations → indexes works for both fresh and old DBs), and add one line to the recipe comment noting index ordering.

**Minor — the migration loop can transiently stamp `user_version` backwards** (`schema.py:202`). `current` is read outside the `BEGIN IMMEDIATE`, so a process that loses the open-race re-runs the whole loop and re-stamps `user_version = 1` on a DB another process already advanced to 2; a crash in that window persists the downgrade. Idempotent migrations self-heal it on the next open, so the impact is bounded, but the docstring's serialization claim oversells. Safe path: re-read `user_version` inside the transaction and skip the migration if it's already ≥ target.

**Minor — no COORDINATION.md log entry.** The catalog DB is shared with the web agent (its stores open `catalog.db` via this same `connect()`), and this change alters open-time behavior for every workdir: old DBs are now mutated and version-stamped on open, and a newer-than-code DB logs a warning. COORDINATION.md's own rule is "additive changes are welcome — log them." Safe path: append a dated entry.

**Minor — dangling plan reference.** `tests/test_migrations.py:1` cites "WS-1", but the covering plan doc (`architecture-evolution-plan.md`, and the `crowdsourced-eval-plan.md` §6-a it implements) is untracked — an uninformed reader of the committed tree can't resolve the reference. Safe path: commit the plan doc alongside the change, or make the docstring self-sufficient (a question for the human if plan docs are intentionally staged separately).

Things I checked that dissolved: the monkeypatch ordering in the rollback test is correct (`SCHEMA_VERSION` is computed after the list patch); Python's implicit-transaction handling around `BEGIN IMMEDIATE` is handled by the `conn.commit()` guards; all stores go through `connect()` so no caller reaches `apply_schema` mid-transaction; the newer-DB warning path works as claimed (verified live); `test_trace_link.py` changes are comment-only, no test weakened; the change is config/backend-agnostic so stub-only test coverage is appropriate here; the critical-paths `human-reviewed` label will be enforced by CI at PR time.

```json
{"verdict": "request_changes", "findings": [
{"severity": "major", "file": "src/va/storage/structured/schema.py", "line": 236, "issue": "apply_schema creates INDEXES before running migrations, so the first future index on a migrated-in column makes connect() crash on every pre-existing workdir — and the in-file schema-change recipe leads the next author straight into it.", "scenario": "WS-2 adds videos.profile per the recipe (base DDL + migration + version bump) plus an index on it; a workdir ingested at v1 then throws sqlite3.OperationalError 'no such column: profile' during the INDEXES loop, before _m2 can ALTER the column in — reproduced empirically. Fix: call _run_migrations before the INDEXES loop and note index ordering in the recipe comment."},
{"severity": "minor", "file": "src/va/storage/structured/schema.py", "line": 202, "issue": "user_version is read outside the BEGIN IMMEDIATE lock, so a racing process re-runs completed migrations and transiently stamps user_version backwards; a crash in that window persists the downgrade.", "scenario": "Two processes open a v0 DB with SCHEMA_VERSION=2; A migrates to 2; B (which read current=0) then stamps user_version=1 and crashes before re-stamping 2 — the DB reports v1 while holding v2 schema until the next open self-heals. Fix: re-read user_version inside the transaction and skip targets already applied."},
{"severity": "minor", "file": "COORDINATION.md", "line": 41, "issue": "No log entry for the schema-versioning change to the shared catalog.db, whose open-time behavior (auto-migrate + newer-DB warning) now differs for the web agent's stores.", "scenario": "The web agent opens a workdir DB unaware it is mutated and version-stamped on open, or sees the newer-DB warning with no channel explaining it. Fix: append a dated additive-change entry per the file's own rule."},
{"severity": "minor", "file": "tests/test_migrations.py", "line": 1, "issue": "The docstring cites 'WS-1' but the covering plan doc (architecture-evolution-plan.md / crowdsourced-eval-plan.md §6-a) is untracked, so the reference resolves to nothing in the committed tree.", "scenario": "A future session reads the test file, cannot find WS-1 anywhere in git, and loses the design rationale for the runner. Fix: commit the plan doc with the change, or make the reference self-sufficient; raise placement with the human if plan docs are staged deliberately."}
]}
```
