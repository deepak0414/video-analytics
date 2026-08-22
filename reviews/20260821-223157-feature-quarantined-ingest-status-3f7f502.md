# Agent review — approve

date: 2026-08-21T22:33:28.917322
range: origin/main..HEAD
branch: feature/quarantined-ingest-status
findings: 0


---

## Full review

Review complete. I read the full diff, the surrounding dedup ordering in `ingest.py`, `watch.py`'s dedup/watermark handling, `manage.lookup_video`/`remove_video`, the NVR `fetch()` cache path, the catalog `done`-filters (`footage_domains`, `tracks.py`, `stale.py`), the web `app.js` list/pill rendering, the `.va-24h` investigation's "repair as executed" section, and the previous review on this branch. (Test execution was not permitted in this sandbox, so the test files were reviewed by reading only.)

**What checks out**
- The `quarantined` dedup sits after the `done` check and before the profile probe / any role work, so a plain `va ingest` or a `va watch` replay is a pure no-op and the watcher's watermark still advances (it only charges budget on `not res.deduped`, and a deduped window doesn't abort the pass).
- `reingest_video` refuses before `load_config` and before `remove_video`; `plan_reprocess` refuses before the generic "not done → use reingest" routing; `stale_report` / `footage_domains` / windowed-count anchoring are already `done`-filtered, so quarantined rows stay out of those paths without new code.
- The "`va remove` + fresh `va ingest` = genuine re-pull" claim holds: ingest `shutil.move`s fetched media out of `cache/` into the video dir, `remove_video` (no `keep_media`) deletes that dir, so NVR `fetch()` finds no cache file and re-pulls (and re-verifies). `lookup_video` resolves local paths and `nvr://` URIs to a `source_key`, so the CLI's suggested `va remove <uri>` works for the URIs `va ingest` accepts.
- The previous review's single minor finding (`_cmd_reingest` letting the `ValueError` escape as a traceback) is fixed in this commit with a CLI test asserting exit 2 + "quarantined" on stderr.
- Documentation parity: no new env vars / flags / config keys; the new status, its semantics, the explicit "read paths do NOT yet filter on status" limitation, and the additive web `job.result.ingest_status` key are all recorded in COORDINATION.md (with ⚠ for the web layer) and CLAUDE.md. The doc claim that the `.va-24h` quarantined clips are unsearchable is accurate — the investigation log confirms the repair cleared their role rows and shards.
- Tests construct their scenarios (status set via `Catalog.set_status`, and a raw-SQL insert for the exact repair write path) and assert observable behavior (list() returns the row with status, dedup leaves status intact, refusal leaves the row intact, CLI output/exit codes, web job result).

**Findings:** none.

```json
{"verdict": "approve", "findings": []}
```
