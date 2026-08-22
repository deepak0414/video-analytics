# Agent review — request_changes

date: 2026-08-21T22:15:54.177470
range: origin/main..HEAD
branch: feature/quarantined-ingest-status
findings: 2

- **major** `src/va/pipeline/manage.py:92` — `va reingest` on a quarantined clip has no guard: remove_video drops the quarantined row and (for nvr_recorded) re-runs roles on the preserved contaminated bytes, silently re-admitting it — contradicting the commit's own docs/comments (and tests/test_e2e.py:61 says reingest IS the deliberate path while testing plain ingest).
  - scenario: Operator runs `va reingest nvr://2/...` on one of the 4 quarantined .va-24h sub-stream clips; it comes back `done` and searchable with no warning, re-contaminating the aggregate count. Safe path: refuse in reingest_video (before remove_video) with the quarantine message pointing at `va remove` + fresh `va ingest`, mirroring plan_reprocess; add the test; fix the misleading test comment/name.
- **minor** `src/va/web/jobs.py:283` — Web ingest job result carries no ingest_status, so app.js:178 renders a quarantined dedup as 'done (already ingested)' — the same misreport the CLI was fixed to avoid.
  - scenario: User submits a quarantined nvr:// URI via the web UI, sees 'done (already ingested)', searches and finds nothing. Safe path: add ingest_status to job.result (additive, log in COORDINATION.md) and render 'quarantined — not searchable'.

---

## Full review

Review complete. I could not run the touched test files: 3 pytest suites are already live (Stop-gate storm — CLAUDE.md 2026-08-04 lesson forbids piling on), so this review is by reading.

**What the commit does well:** the `IngestStatus.quarantined` member fixes a real crash (`Catalog._from_row` raised `ValueError` on the `.va-24h` repair rows → `/api/videos`, `migrate-layout`, deep-scan fallback); the dedup no-op, `stale`/`reprocess` exclusion, CLI wording, CLAUDE.md + COORDINATION entries, and the regression tests all construct their scenarios and would fail on the old code.

**Findings**

1. **major — `src/va/pipeline/manage.py:92` (`reingest_video`) / `src/va/cli.py:268`** — `va reingest <quarantined>` has no guard, so it silently does exactly what the commit says must never happen silently. `reingest_video` → `remove_video` deletes the catalog row (status gone), parks the preserved NVR bytes in `cache/` for `nvr_recorded`, and re-ingests them with roles — the contaminated clip comes back `done` and searchable with no warning. The commit guards the *less* dangerous sibling (`plan_reprocess` refuses with a quarantine-specific message) and documents "NOT `va reingest`" in CLAUDE.md, COORDINATION.md, the CLI note, and the ingest.py comment — but enforcement is zero, and the new test at `tests/test_e2e.py:61` says the opposite ("Explicit `va reingest` is the deliberate path") while testing plain `ingest`, not reingest. Scenario: operator sees the `[quarantined]` CLI note, reaches for the familiar `va reingest nvr://2/...` on `.va-24h` → the 352x240 foreign sub-stream is re-admitted into the aggregate count. Safe path: mirror `plan_reprocess` — in `reingest_video` (before the destructive `remove_video`) refuse a quarantined target with the same message pointing at `va remove` + fresh `va ingest`; add the test; fix the test comment/name (it tests `ingest`, not reingest); log the refusal in COORDINATION.md.

2. **minor — `src/va/web/jobs.py:283` + `src/va/web/static/app.js:178`** — the web ingest job reports a quarantined dedup as `done (already ingested)`, the exact misreport the CLI was changed to avoid; `job.result` carries no status, so the UI can't tell. Scenario: a user submits a quarantined `nvr://` URI in the web UI, sees "done (already ingested)", searches, finds nothing, and assumes search is broken. Safe path: add `ingest_status` to `job.result` (additive; log in COORDINATION.md) and render "quarantined — not searchable".

```json
{"verdict": "request_changes", "findings": [{"severity": "major", "file": "src/va/pipeline/manage.py", "line": 92, "issue": "`va reingest` on a quarantined clip has no guard: remove_video drops the quarantined row and (for nvr_recorded) re-runs roles on the preserved contaminated bytes, silently re-admitting it — contradicting the commit's own docs/comments (and tests/test_e2e.py:61 says reingest IS the deliberate path while testing plain ingest).", "scenario": "Operator runs `va reingest nvr://2/...` on one of the 4 quarantined .va-24h sub-stream clips; it comes back `done` and searchable with no warning, re-contaminating the aggregate count. Safe path: refuse in reingest_video (before remove_video) with the quarantine message pointing at `va remove` + fresh `va ingest`, mirroring plan_reprocess; add the test; fix the misleading test comment/name."}, {"severity": "minor", "file": "src/va/web/jobs.py", "line": 283, "issue": "Web ingest job result carries no ingest_status, so app.js:178 renders a quarantined dedup as 'done (already ingested)' — the same misreport the CLI was fixed to avoid.", "scenario": "User submits a quarantined nvr:// URI via the web UI, sees 'done (already ingested)', searches and finds nothing. Safe path: add ingest_status to job.result (additive, log in COORDINATION.md) and render 'quarantined — not searchable'."}]}
```
