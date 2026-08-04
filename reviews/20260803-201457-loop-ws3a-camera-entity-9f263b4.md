# Agent review — approve

date: 2026-08-03T20:19:00.340812
range: origin/main..HEAD
branch: loop/ws3a-camera-entity
findings: 2

- **minor** `src/va/pipeline/manage.py:124` — The camera-carry across reingest runs only on ingest success — a hard ingest failure (row marked failed, exception re-raised) skips set_camera, and the subsequent `va ingest` retry carries the recorded profile but not the camera, so the link is silently severed.
  - scenario: A camera-linked chunk is reingested after a model change; the YouTube re-download fails mid-ingest, reingest_video re-raises before set_camera runs; the user retries with `va ingest <url>`, which completes the not-done row (profile carried, camera_id NULL) — the chunk permanently vanishes from its camera's collection with no warning. Safe path: re-apply existing.camera_id on the failure path too (look up the recreated row by source_key before re-raising), or extend ingest's profile retry-carry seam to camera_id.
- **minor** `COORDINATION.md:437` — The WS3.a log entry predates the final amend: it omits the shared-surface behavior change that reingest_video now preserves camera links across remove+ingest, and records a stale suite count (579 vs the range's 581).
  - scenario: The WS-4 agent reads COORDINATION.md to learn the camera contract, sees 'nothing sets camera_id yet' with no mention of reingest's carry-forward (or its failure-path gap), and designs its stream source around wrong assumptions about reingest behavior. Safe path: amend the entry with the reingest camera-carry behavior and the current test count before finalize.

---

## Full review

Review complete. The suite is green (581 passed / 2 skipped), and I traced the full `origin/main..HEAD` range with particular attention to the delta since the last review round (4dda237 → 9f263b4), which is the fix for that round's one finding.

## What I verified

**The camera-carry fix (the new delta).** `reingest_video` (src/va/pipeline/manage.py:124-134) now captures `existing.camera_id` before the destructive removal and re-applies it via `Catalog.set_camera` after the fresh ingest, updating both the DB row and the returned `IngestResult.video`. A new test (`test_reingest_preserves_the_camera_link`) asserts both the returned model and the persisted row. `IngestResult` is a mutable dataclass, so the attribute reassignment is valid; the `result is not None` guard is correct against the not-found path.

**The rest of the range** (footage-profile layer → per-ingest recording → gating → knobs → camera entity), independently re-checked rather than trusted from prior rounds: the v2→v3→v4 migration chain is ordered and idempotent (`BEGIN IMMEDIATE` per migration, re-read under lock, `add_column` guard); `CAMERAS` precedes `VIDEOS` in `ALL_TABLES`; fresh-vs-migrated column equivalence and the A-EV-stays-NULL done-conditions of WS3.a are both tested; `CameraStore._from_row` handles both the ISO-with-T inserts and SQLite's `datetime('now')` space-separated default; the four config dirs carry byte-identical footage yamls (md5-verified); ingest/stale/reprocess all interpret `enabled:` through the same `cfg.role()` seam with the same dependency closure; all prior review-round findings I could reconstruct are incorporated. No disputes in workflow-trust-plan.md apply to this range.

## Findings (both minor)

**1. The camera-carry only survives a *successful* reingest.** If `ingest()` fails hard mid-reingest (e.g. a YouTube re-download fails), it marks the recreated row `failed` and re-raises (src/va/pipeline/ingest.py:507-509), so the `set_camera` re-link at manage.py:129 never runs. The retry path inside ingest carries the recorded *profile* forward on a not-yet-done row, but there is no camera analogue — so retrying with `va ingest` completes the video with `camera_id` NULL, silently severing the chunk from its camera. Safe path: re-apply the link on the failure path too (wrap the ingest call so a raised exception still looks up the recreated row by `existing.source_key` and calls `set_camera` before re-raising), or extend ingest's existing retry-carry seam to camera the way it works for profile. Nothing writes `camera_id` in production yet, so this is forward-looking like the finding it fixes.

**2. COORDINATION.md's WS3.a entry predates the amend.** The entry (COORDINATION.md:437-443) states "Nothing sets `camera_id` during ingest yet" and "Full suite 579 passed / 2 skipped", but the final commit also changed `reingest_video`'s shared behavior (it now preserves camera links across the remove+ingest cycle) and the suite is at 581. The WS-4 agent reading the log won't learn that reingest is camera-preserving (or that a failed reingest is not — finding 1). Safe path: append/amend the entry with the reingest carry-forward behavior and the current count before finalize.

Commit subjects are all provisional `need_agent_review:` forms (exempt from the clarity rule). Verdict: **approve** — both findings are minor.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/manage.py", "line": 124, "issue": "The camera-carry across reingest runs only on ingest success — a hard ingest failure (row marked failed, exception re-raised) skips set_camera, and the subsequent `va ingest` retry carries the recorded profile but not the camera, so the link is silently severed.", "scenario": "A camera-linked chunk is reingested after a model change; the YouTube re-download fails mid-ingest, reingest_video re-raises before set_camera runs; the user retries with `va ingest <url>`, which completes the not-done row (profile carried, camera_id NULL) — the chunk permanently vanishes from its camera's collection with no warning. Safe path: re-apply existing.camera_id on the failure path too (look up the recreated row by source_key before re-raising), or extend ingest's profile retry-carry seam to camera_id."}, {"severity": "minor", "file": "COORDINATION.md", "line": 437, "issue": "The WS3.a log entry predates the final amend: it omits the shared-surface behavior change that reingest_video now preserves camera links across remove+ingest, and records a stale suite count (579 vs the range's 581).", "scenario": "The WS-4 agent reads COORDINATION.md to learn the camera contract, sees 'nothing sets camera_id yet' with no mention of reingest's carry-forward (or its failure-path gap), and designs its stream source around wrong assumptions about reingest behavior. Safe path: amend the entry with the reingest camera-carry behavior and the current test count before finalize."}]}
```
