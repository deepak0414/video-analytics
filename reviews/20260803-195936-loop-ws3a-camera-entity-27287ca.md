# Agent review — approve

date: 2026-08-03T20:06:10.267002
range: origin/main..HEAD
branch: loop/ws3a-camera-entity
findings: 2

- **minor** `src/va/cli.py:25` — The already-ingested notice suggests `va reingest --profile <name>` without the required positional video argument, so the printed remedy fails argparse if copy-pasted.
  - scenario: User runs `va ingest URL --profile security` on a done video, copies the suggested command verbatim, and gets 'the following arguments are required: video' instead of the reingest; include the ident (e.g. `va reingest {uri} --profile {name}`) in the message.
- **minor** `src/va/pipeline/ingest.py:435` — When the profile disables only object_tracker, the purge of prior-attempt track rows runs only inside the `det_ok and frames_dets` branch, so a retry whose detector fails to load, raises mid-decode (frames_dets reset to []), or decodes zero frames leaves stale object_tracks rows that the disabled-and-unstamped staleness exclusion then never surfaces.
  - scenario: Attempt 1 writes tracks then the ingest fails late; retry under a tracker-disabling profile hits a detector load error — tracker registers as skipped and is excluded from `va stale`, yet the old track rows stay live, breaking the stated 'skipped role leaves NO live rows' invariant; hoist the _purge to run whenever the tracker gate is off, like the other disabled-role purges.

---

## Full review

Review complete. I read the full range (five stacked commits: footage-profile config layer → per-ingest recording → role/vocab gating → profile-wide knobs → camera entity), traced the gating logic through ingest/stale/reprocess, checked the migration path, verified all four config dirs carry the new profile files and the roles the security overlay touches, and ran the offline suite: **579 passed / 2 skipped** — matching the COORDINATION.md claim.

What I checked and how each suspicion resolved:

- **Gating coherence (ingest ↔ stale ↔ reprocess).** The three consumers interpret `enabled:` through the same pydantic seam (`cfg.role()`), the loader rejects non-bool `enabled:` and core-role disables, and the dependency closure (STT→diarizer, detector→tracker) is applied identically in `_enabled`/`stale_report`/`execute_reprocess`. The tricky stamped-vs-unstamped disabled-role semantics (excluded only while unstamped; stamped-and-disabled reads stale) are implemented as documented and tested (`test_role_disabled_after_it_ran_reads_stale`, `test_dependency_skipped_roles_are_not_stale`).
- **Record==reality.** Ingest pins one overlaid config, passes it to every getter, tags both vector shards from it (`embedder_id(role, cfg)`), and stale/reprocess rebuild+restamp per recorded profile via `config_for()` with per-profile memoized pins. The query-path profile-unawareness is a real gap but is explicitly documented as a caveat in CLAUDE.md with a backlog item — not a silent break.
- **Migration.** v2→v3→v4 ordering is sound: `CAMERAS` precedes `VIDEOS` for fresh DBs, `add_column` with a NULL-default `REFERENCES` clause is legal SQLite, and fresh-vs-migrated equivalence is tested. The unenforced FK (`PRAGMA foreign_keys` never enabled) matches the repo-wide convention already accepted in the provenance-identity review, so I don't report it.
- **Contract logging.** Every shared-surface change (schema v3/v4, `ingest(profile=)`, 3-tuple `skipped` rows, the 0-transcript-rows-by-design behavior note) is logged in COORDINATION.md with ⚠ where breaking.
- **Test integrity.** No tests weakened; the new suites cover the profile matrix well, including the minimal-roles.yaml shape, string-`"false"` coercion divergence, retry purge, and all four config dirs' profile files parsing.

Two minor findings survive:

1. **Broken remedy command in the dedup notice** (`src/va/cli.py:25`). The printed suggestion `va reingest --profile <name>` omits the required positional video argument, so copy-pasting it fails argparse. The safe path: include the ident the user just typed — `va reingest {args.uri} --profile {args.profile}`.
2. **Tracker-disabled purge is conditional on this attempt's detections** (`src/va/pipeline/ingest.py:435`). The prior-attempt `object_tracks` purge only runs inside the `det_ok and frames_dets` branch. If a profile disables only the tracker and this retry's detector fails to load, fails mid-decode (which resets `frames_dets` to `[]`), or decodes zero frames, prior-attempt track rows survive — and because the tracker is then disabled-and-unstamped, `va stale` excludes it, so nothing ever surfaces the leftover rows. This breaks the stated "a skipped role must leave NO live rows" invariant on a narrow path. Safe path: hoist the `_purge("object_tracker", …)` so it runs whenever `not tracker_on` (alongside the skip registration at line 417), unconditionally of `det_ok`/`frames_dets` — mirroring how the STT/OCR/action/detector-disabled purges already run.

Neither is major; the verdict is **approve**.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/cli.py", "line": 25, "issue": "The already-ingested notice suggests `va reingest --profile <name>` without the required positional video argument, so the printed remedy fails argparse if copy-pasted.", "scenario": "User runs `va ingest URL --profile security` on a done video, copies the suggested command verbatim, and gets 'the following arguments are required: video' instead of the reingest; include the ident (e.g. `va reingest {uri} --profile {name}`) in the message."}, {"severity": "minor", "file": "src/va/pipeline/ingest.py", "line": 435, "issue": "When the profile disables only object_tracker, the purge of prior-attempt track rows runs only inside the `det_ok and frames_dets` branch, so a retry whose detector fails to load, raises mid-decode (frames_dets reset to []), or decodes zero frames leaves stale object_tracks rows that the disabled-and-unstamped staleness exclusion then never surfaces.", "scenario": "Attempt 1 writes tracks then the ingest fails late; retry under a tracker-disabling profile hits a detector load error — tracker registers as skipped and is excluded from `va stale`, yet the old track rows stay live, breaking the stated 'skipped role leaves NO live rows' invariant; hoist the _purge to run whenever the tracker gate is off, like the other disabled-role purges."}]}
```
