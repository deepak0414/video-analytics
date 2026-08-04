# Agent review — request_changes

date: 2026-08-03T17:25:50.936577
range: origin/main..HEAD
branch: loop/ws2b-per-ingest-profile
findings: 2

- **major** `src/va/pipeline/manage.py:103` — reingest_video re-ingests without passing the video's recorded profile, so videos.profile is silently reset to the source-derived default ('generic'), and `va reingest` offers no --profile flag to re-specify it.
  - scenario: va ingest cam.mp4 --profile security, then the documented model-upgrade flow va reingest cam.mp4 (also the va reprocess fallback) -> the row's profile becomes 'generic' with no warning; once WS2.c makes roles consume the profile, a security-camera video is silently re-processed under the wrong domain. Fix: pass profile=video.profile in reingest_video (the object is already in hand at manage.py:95) plus a test, or record the re-derive decision in COORDINATION.md.
- **minor** `src/va/pipeline/ingest.py:130` — The dedup early-return happens before profile resolution/validation, so an explicit --profile on an already-done video is silently ignored and a typo'd profile name is never reported.
  - scenario: va ingest <already-done-url> --profile secruity (typo) prints [already-ingested] and exits 0 — the user believes the profile applied and the name was valid; a valid differing profile is likewise dropped with no notice. Fix: when profile is explicitly provided, run the load_config validation probe before the dedup return and print a notice when the requested profile differs from the stored videos.profile.

---

## Full review

Both prior WS2.a review rounds are on file (approved; the top-level-`roles:`-mapping minor is recorded as a carry-over to WS2.c in `architecture-evolution-loop.md`, so I won't re-report it). My review therefore concentrates on the new WS2.b commit. I've read the full diff, `ingest.py`, `schema.py`, `manage.py`, `cli.py`, the plan's done-when items, and the prior reviews.

## Review summary

**Scope:** two commits — `a6c53ee` (WS2.a, third config layer; already twice-reviewed and approved, previous minors fixed or carried) and `faf1310` (WS2.b, per-ingest profile recorded as `videos.profile`).

**What checks out:**

- **WS2.b done-when conformance:** the v3 migration is tested both fresh (`test_fresh_db_has_profile_column_at_v3`) and in-place from a simulated v2 DB (`test_pre_profile_ingest_reads_back_null`); with/without-flag ingests store the expected profile; pre-profile rows read back NULL. Migration machinery follows the repo recipe (base DDL + idempotent `add_column` + version bump, assert ties `SCHEMA_VERSION` to the migration count).
- **Fail-fast validation is correctly placed:** the unknown-profile `load_config` probe sits after dedup but before `fetch()`, outside the inner try, so a typo'd name raises without downloading and without stamping the row `failed` — and the test asserts the row never advances.
- **Contract/docs parity:** COORDINATION.md logs both changes with the additive-signature and schema-v3 details; CLAUDE.md documents the three layers, `--profile`, `active_footage_profile`, and the NULL semantics in the same change. The `test_provenance_ingest` double was *extended* (`lambda **kw`), not weakened — the pinned-config assertion still bites.
- **Provenance deliberately fingerprints the base config** (stamping an overlay-modified cfg before roles consume it would stamp models that didn't run) — reasoned and logged in COORDINATION; not a finding.
- **Combinations:** run-siglip/run-claude/run-qwen3vl dirs ride the tolerated-missing-`generic` path (tested); a profile is validated against whatever `VA_CONFIG_DIR` is active, which is the self-consistent behavior.

**Findings:**

1. **major — `src/va/pipeline/manage.py:103`** — `reingest_video` silently destroys the recorded profile. It calls `ingest(src, workdir=workdir, fps=fps)` with no `profile`, so the re-ingest re-derives the source default (`generic`) and overwrites `videos.profile`; the removed `video` object with `.profile` in hand at line 95 is simply not consulted, and `va reingest` has no `--profile` flag, so the operator cannot even re-specify it. Scenario: `va ingest cam.mp4 --profile security`, then the documented model-upgrade flow `va reingest cam.mp4` (also the `va reprocess` fallback for non-wired roles) → the row now reads `profile='generic'` with no warning; the WS2.b deliverable — a durable record of the ingest domain — doesn't survive the repo's own routine maintenance command, and once WS2.c makes roles consume the profile this becomes silently-wrong models/vocab on re-ingest. Safe path: carry it forward — `ingest(src, workdir=workdir, fps=fps, profile=video.profile)` (None for pre-profile rows preserves today's default), plus a test; if re-deriving is actually intended, record that decision in COORDINATION.md/the loop doc.

2. **minor — `src/va/pipeline/ingest.py:130`** — the dedup early-return precedes profile resolution/validation, so an explicit `--profile` on an already-`done` video is silently ignored *and* never validated: `va ingest <done-url> --profile secruity` (typo) prints `[already-ingested]` and exits 0, teaching the user the name was fine; the same command with a real profile leaves the stored profile unchanged with no hint. Safe path: when `profile` was explicitly passed, validate it (cheap `load_config` probe) even on the dedup path and print a notice when it differs from the stored `videos.profile` (actually changing it belongs to reingest).

**Verdict: request_changes** — one major (recorded profile silently lost through the documented reingest path, with no operator override).

```json
{"verdict": "request_changes", "findings": [
  {"severity": "major", "file": "src/va/pipeline/manage.py", "line": 103, "issue": "reingest_video re-ingests without passing the video's recorded profile, so videos.profile is silently reset to the source-derived default ('generic'), and `va reingest` offers no --profile flag to re-specify it.", "scenario": "va ingest cam.mp4 --profile security, then the documented model-upgrade flow va reingest cam.mp4 (also the va reprocess fallback) -> the row's profile becomes 'generic' with no warning; once WS2.c makes roles consume the profile, a security-camera video is silently re-processed under the wrong domain. Fix: pass profile=video.profile in reingest_video (the object is already in hand at manage.py:95) plus a test, or record the re-derive decision in COORDINATION.md."},
  {"severity": "minor", "file": "src/va/pipeline/ingest.py", "line": 130, "issue": "The dedup early-return happens before profile resolution/validation, so an explicit --profile on an already-done video is silently ignored and a typo'd profile name is never reported.", "scenario": "va ingest <already-done-url> --profile secruity (typo) prints [already-ingested] and exits 0 — the user believes the profile applied and the name was valid; a valid differing profile is likewise dropped with no notice. Fix: when profile is explicitly provided, run the load_config validation probe before the dedup return and print a notice when the requested profile differs from the stored videos.profile."}
]}
```
