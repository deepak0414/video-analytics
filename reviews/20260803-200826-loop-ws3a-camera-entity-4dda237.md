# Agent review — approve

date: 2026-08-03T20:13:00.308968
range: origin/main..HEAD
branch: loop/ws3a-camera-entity
findings: 1

- **minor** `src/va/pipeline/manage.py:91` — reingest_video carries the recorded footage profile across remove+ingest but drops camera_id — the row is deleted and recreated with camera_id NULL, so reingesting a camera-linked chunk silently severs it from its camera's collection.
  - scenario: WS-4 (or anyone using the new Catalog.set_camera API) links a chunk to cam-2; later `va reingest <chunk>` runs after a model change — the recreated row has camera_id NULL and the chunk vanishes from the camera's chunk set with no warning; capture existing.camera_id in reingest_video and re-apply it via set_camera after ingest, or log the limitation in COORDINATION.md for the WS-4 agent.

---

## Full review

Suite is green (580 passed / 2 skipped — one more than COORDINATION's 579 because the review-fix commit added a regression test). I've traced the full range: config overlay loader, ingest gating/purges, stale/reprocess profile pinning, the schema v3→v4 migrations, and the camera entity. One last check of the reingest path confirmed my one surviving suspicion, detailed below.

## Review summary

**Scope reviewed:** the five stacked commits `origin/main..HEAD` (footage-profile config layer → per-ingest recording → role/vocab gating → profile-wide knobs → camera entity, schema v4). Earlier commits in the stack were reviewed in prior rounds; I re-verified the whole range rather than trusting those, and confirmed HEAD incorporates the fixes for both findings of the last review (27287ca): the dedup notice now includes the video ident in the suggested `va reingest` command, and the disabled-tracker purge is hoisted out of the `det_ok and frames_dets` branch, with a new regression test (`test_disabled_tracker_purges_prior_rows_even_with_zero_frames`) that exercises the zero-frame path.

**What held up under scrutiny:**

- **Gating coherence.** Ingest, `stale_report`, and `execute_reprocess` all interpret `enabled:` through the same `cfg.role()` pydantic seam; the loader rejects non-bool `enabled:` and core-role disables; the dependency closure (STT→diarizer, detector→tracker) is applied identically in all three consumers. The stamped-and-disabled → stale semantics are implemented as documented and tested.
- **Record==reality.** Ingest pins one overlaid config, passes it to every getter, tags both vector shards from it; stale/reprocess rebuild and restamp per recorded profile via `config_for()` with per-profile memoized pins; `execute_reprocess` restamps with the per-item cfg, not a global one. The query-path profile-unawareness is explicitly documented as a caveat with a backlog item.
- **Migrations.** v2→v3→v4 ordering is sound; `CAMERAS` precedes `VIDEOS` in `ALL_TABLES` for fresh DBs; `add_column` with a NULL-default `REFERENCES` clause is legal SQLite; fresh-vs-migrated equivalence and the A-EV-stays-NULL done-condition are both tested. The unenforced FK matches the accepted repo convention.
- **Error paths.** A broken/renamed profile yaml fails loudly on explicit input, pre-removal on reingest, per-item (not batch-fatal) in reprocess, and per-video with a warning in `va stale`. Skipped-role purges are best-effort and cannot abort ingest.
- **Docs and contract logging.** All new surface (`--profile` flags, `active_footage_profile`, footage knobs, the cameras table, staleness semantics) is in CLAUDE.md, and every shared-surface change is logged in COORDINATION.md with ⚠ where breaking. All four config dirs carry byte-identical profile files.
- **Plan conformance.** WS3.a's "Done when" (migration equivalence tests pass; A-EV videos keep `camera_id` NULL) is met; `start_epoch` is correctly deferred to WS3.b.

**One finding survives (minor):**

`reingest_video` (`src/va/pipeline/manage.py:91`) deliberately carries the recorded `profile` forward across the remove+ingest cycle, but drops `camera_id`: `remove_video` deletes the catalog row and the fresh ingest recreates it with `camera_id` NULL, and the `existing` row it already looks up is consulted only for the profile. Nothing sets `camera_id` today, so no current data is at risk — but this commit ships `Catalog.set_camera` as the API for linking chunks to cameras, and the moment WS-4 (or a human via the API) uses it, `va reingest` — the documented remedy for model changes and for profile-disabled stale roles — will silently sever the chunk from its camera collection. Safe path: capture `existing.camera_id` alongside the profile and re-apply it via `set_camera` after the ingest returns (or, at minimum, log the limitation in COORDINATION.md so the WS-4 agent knows reingest is camera-lossy).

Verdict: **approve** — the one finding is minor and forward-looking.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/manage.py", "line": 91, "issue": "reingest_video carries the recorded footage profile across remove+ingest but drops camera_id — the row is deleted and recreated with camera_id NULL, so reingesting a camera-linked chunk silently severs it from its camera's collection.", "scenario": "WS-4 (or anyone using the new Catalog.set_camera API) links a chunk to cam-2; later `va reingest <chunk>` runs after a model change — the recreated row has camera_id NULL and the chunk vanishes from the camera's chunk set with no warning; capture existing.camera_id in reingest_video and re-apply it via set_camera after ingest, or log the limitation in COORDINATION.md for the WS-4 agent."}]}
```
