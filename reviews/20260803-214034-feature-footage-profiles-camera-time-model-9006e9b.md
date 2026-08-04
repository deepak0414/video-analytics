# Agent review — approve

date: 2026-08-03T21:47:41.797034
range: origin/main..HEAD
branch: feature/footage-profiles-camera-time-model
findings: 2

- **minor** `src/va/storage/structured/cameras.py:38` — CameraStore.get_or_create is a non-atomic check-then-insert: a concurrent registration of the same camera id makes the loser raise sqlite3.IntegrityError instead of returning (existing, False) as the docstring promises.
  - scenario: WS-4 multi-camera ingest runs two chunk ingests for the same camera concurrently; both see get() return None, both INSERT, and one process crashes with IntegrityError mid-ingest. Safe path: INSERT OR IGNORE then re-get, or catch IntegrityError and return the stored row.
- **minor** `src/va/pipeline/manage.py:123` — reingest_video re-attaches camera_id/start_epoch only after ingest() returns or raises, so a hard process kill mid-reingest loses both silently — and the documented plain-ingest retry then completes the row without them.
  - scenario: An NVR chunk with camera_id/start_epoch set is reingested; the process is OOM-killed during the heavy caption pass (no Python exception, so the failure-path re-attach never runs); a later `va ingest` retry completes the row with both fields NULL, silently severing the chunk from its camera and from wall-clock queries. Safe path: write the carried metadata at row-recreation time, e.g. thread optional camera_id/start_epoch through ingest() the way profile already is.

---

## Full review

The suite verification matches COORDINATION.md's claim exactly (589 passed / 2 skipped), the CLI does surface the stale-report profile warning via Python's last-resort stderr handler (so that suspicion dissolves), and the prior review's 588-vs-589 count finding is already fixed in the current COORDINATION.md entry. My remaining suspicions about the gating/staleness/purge semantics all dissolved on reading: the enabled-gate, dependency closure, stamped-and-disabled staleness, and prior-row purging are mutually consistent across ingest, `va stale`, and `va reprocess`, and each is covered by a dedicated test (including the zero-frame and minimal-roles.yaml edge cases). Segment captions need no purge because `replace_segments` wipes them each ingest.

# Review — origin/main..HEAD (9006e9b)

**Scope:** footage-profile config layer + gating (WS-2 complete), camera entity and dual time model (WS3.a/b). One commit, provisional `need_agent_review:` subject (exempt from the message-clarity rule).

**What I verified**
- **Correctness:** the profile resolution order (explicit arg > `active_footage_profile` > source default), the record==reality rule across ingest/stale/reprocess, the dependency closures (STT→diarizer, detector→tracker), the untracked-detections path when only the tracker is disabled, and the purge-on-skip invariant are all internally consistent and individually tested. `wallclock_to_chunks` clamping/skip logic is correct for known- and unknown-duration chunks; boundary-touching chunks yield zero-width ranges, which is harmless.
- **Contracts:** schema v2→v5 migrations are ordered, idempotent, and crash-safe under the existing `BEGIN IMMEDIATE` runner; the `execute_reprocess` skipped-row shape change (2-tuple → 3-tuple) and all other shared-surface changes are logged in COORDINATION.md with the ⚠ markers.
- **Tests:** 589 passed / 2 skipped locally, matching the coordination log. Test doubles were widened (`lambda *a, **k:`), not weakened; the batch-pin test was strengthened (now also asserts memoization). New behavior has dense coverage including the nasty edges (string-`"false"` enabled, broken carried profile on the dedup path, zero-frame ingest with a disabled tracker).
- **Combinations:** the footage yamls ship in all four config dirs and `test_all_shipped_profiles_parse` loads generic+security from each. The one genuinely untested combination (embedder-model override in a footage profile vs. the profile-unaware query path) is explicitly documented as a forbidden caveat in CLAUDE.md with the fix backlogged — acceptable.
- **Docs:** `--profile` (ingest/reingest), the config layer, knobs, the YAML `off` foot-gun, and the caveat are all in CLAUDE.md in this same change.

**Findings (both minor):**

1. **minor — `src/va/storage/structured/cameras.py:38`** — `get_or_create` is a non-atomic check-then-insert: two concurrent registrations of the same camera id (exactly the multi-camera concurrent-ingest shape WS-4 is building toward, and this repo already engineers for concurrent DB openers in the migration runner) make the loser raise `sqlite3.IntegrityError` instead of honoring the documented `(existing, False)` contract. Safe path: `INSERT OR IGNORE` followed by `self.get(camera.id)`, or catch `IntegrityError` and return the winner's row.

2. **minor — `src/va/pipeline/manage.py:123` (`_reattach_chunk_metadata`)** — the camera link and `start_epoch` are re-attached only after `ingest()` returns or raises; a hard process kill mid-reingest (OOM during a 58 GB-model caption pass is a realistic way to die without a Python exception) leaves the recreated row with both NULL, and the documented later plain-`va ingest` retry then completes the row as-is — the chunk is silently severed from its camera and dropped from wall-clock queries. Safe path: write the carried metadata at row-recreation time (e.g. thread optional `camera_id`/`start_epoch` through `ingest()` next to `profile`, which already solved this exact problem for the profile field). Inert today (nothing sets these fields in production until WS-4), hence minor.

**Verdict: approve** — no critical or major findings.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/storage/structured/cameras.py", "line": 38, "issue": "CameraStore.get_or_create is a non-atomic check-then-insert: a concurrent registration of the same camera id makes the loser raise sqlite3.IntegrityError instead of returning (existing, False) as the docstring promises.", "scenario": "WS-4 multi-camera ingest runs two chunk ingests for the same camera concurrently; both see get() return None, both INSERT, and one process crashes with IntegrityError mid-ingest. Safe path: INSERT OR IGNORE then re-get, or catch IntegrityError and return the stored row."}, {"severity": "minor", "file": "src/va/pipeline/manage.py", "line": 123, "issue": "reingest_video re-attaches camera_id/start_epoch only after ingest() returns or raises, so a hard process kill mid-reingest loses both silently — and the documented plain-ingest retry then completes the row without them.", "scenario": "An NVR chunk with camera_id/start_epoch set is reingested; the process is OOM-killed during the heavy caption pass (no Python exception, so the failure-path re-attach never runs); a later `va ingest` retry completes the row with both fields NULL, silently severing the chunk from its camera and from wall-clock queries. Safe path: write the carried metadata at row-recreation time, e.g. thread optional camera_id/start_epoch through ingest() the way profile already is."}]}
```
