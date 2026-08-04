# Agent review — approve

date: 2026-08-03T20:27:45.955898
range: origin/main..HEAD
branch: loop/ws3a-camera-entity
findings: 3

- **minor** `src/va/storage/structured/schema.py:58` — videos.camera_id declares REFERENCES cameras(id) but no connection runs PRAGMA foreign_keys=ON, so the FK is never enforced and set_camera accepts dangling camera ids silently.
  - scenario: A WS-4 stream source (or manual call) typos 'cam-01' for a registered 'cam-1'; set_camera succeeds, the chunk joins no camera collection, and camera-scoped queries silently miss it while the schema reads as if enforced. Safe path: enable foreign_keys=ON in connect() (verifying migrations tolerate it) or validate camera existence in Catalog.set_camera.
- **minor** `src/va/storage/structured/cameras.py:39` — CameraStore.get_or_create is a non-atomic SELECT-then-INSERT, so concurrent registration of the same camera id raises IntegrityError instead of returning the existing row, contradicting the documented idempotency contract.
  - scenario: Under WS-4, two chunk-ingests of the same camera run in parallel and both call get_or_create('cam-1'); the loser hits the SELECT before the winner's INSERT commits, then its own INSERT raises IntegrityError and aborts that ingest. Safe path: INSERT OR IGNORE followed by a re-SELECT.
- **minor** `COORDINATION.md:445` — The WS3.a log entry records 'Full suite 581 passed / 2 skipped' but the range's actual suite is 582 passed / 2 skipped — the count predates the final amend, recurring the prior round's stale-count finding.
  - scenario: The WS-4 agent (or the human approving the digest) cross-checks the logged count against a fresh run, sees a mismatch, and has to re-audit which tests the entry actually covers; the evidence gate requires real counts. Safe path: carry the /verify-generated count into the finalize digest and amend the entry.

---

## Full review

Review complete. I examined the full `origin/main..HEAD` range (five stacked commits: footage-profile config layer → per-ingest profile recording → role/vocab gating → profile-wide knobs → WS3.a camera entity), ran the offline suite (582 passed / 2 skipped, green), and re-checked the fixes for the previous review round's findings.

## What I verified

**Prior findings are resolved.** The last review (9f263b4, approve with 2 minors) asked for a failure-path camera re-attach and a COORDINATION amend. Both landed: `reingest_video` now wraps `ingest()` and calls `_reattach_camera()` on both success and exception paths (`src/va/pipeline/manage.py:777-786` in the diff), with a dedicated test (`test_failed_reingest_still_reattaches_the_camera_link`) that fails the ingest after row recreation and asserts the link survives on the failed row. The COORDINATION WS3.a entry now documents the reingest camera-carry including the failure path.

**Correctness of the gating/staleness machinery** (traced end-to-end, no defects found): the enabled-gate, staleness exclusion, and reprocess routing all interpret `enabled:` through the same `cfg.role()` pydantic seam with the same `GATE_DEPENDENTS` closure, so the edge cases converge — re-enabling a skipped role reads stale → reingest runs it; a stamped-then-disabled role reads stale → reingest purges it; a disabled tracker with a live detector stores untracked detections and stays excluded from staleness. The schema v2→v3→v4 migration chain is ordered, idempotent, and tested fresh-vs-migrated; `CAMERAS` precedes `VIDEOS` for the FK; `CameraStore._from_row` handles both ISO-with-T and SQLite's space-separated `datetime('now')`. All four config dirs' footage profiles are load-tested (`test_all_shipped_profiles_parse`). No relevant disputes in workflow-trust-plan.md.

## Findings (all minor)

1. **`src/va/storage/structured/schema.py:58` — the `camera_id REFERENCES cameras(id)` constraint is decorative: nothing runs `PRAGMA foreign_keys=ON`.** Python's sqlite3 defaults FK enforcement off, and `connect()` doesn't enable it, so `Catalog.set_camera` silently accepts a dangling camera id. A WS-4 stream source (or manual call) that typos `"cam-01"` for `"cam-1"` attaches the chunk to a camera that doesn't exist — it joins no collection and camera-scoped queries silently miss it, while the schema reads as if enforced. Safe path: enable `foreign_keys=ON` in `connect()` (verifying the migration order tolerates it) or validate existence inside `set_camera`.

2. **`src/va/storage/structured/cameras.py:39` — `get_or_create` is SELECT-then-INSERT, so two concurrent registrations of the same camera id race to an `IntegrityError`** — contradicting the docstring's idempotency contract. Plausible under WS-4, where parallel chunk-ingests of one camera each call `get_or_create`. Safe path: `INSERT OR IGNORE` followed by a re-SELECT.

3. **`COORDINATION.md:445` — the WS3.a entry's suite count is stale again: it logs "581 passed / 2 skipped" but the range's suite is 582 / 2** (a test was added after the entry was written — a recurrence of the previous round's finding). The WS-4 agent reading the log gets a count that doesn't match reality, and the evidence gate requires real numbers. Safe path: carry the `/verify`-generated count into the finalize digest and amend the entry.

Commit subjects are all provisional `need_agent_review:` forms (exempt from the clarity rule). Verdict: **approve** — nothing critical or major.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/storage/structured/schema.py", "line": 58, "issue": "videos.camera_id declares REFERENCES cameras(id) but no connection runs PRAGMA foreign_keys=ON, so the FK is never enforced and set_camera accepts dangling camera ids silently.", "scenario": "A WS-4 stream source (or manual call) typos 'cam-01' for a registered 'cam-1'; set_camera succeeds, the chunk joins no camera collection, and camera-scoped queries silently miss it while the schema reads as if enforced. Safe path: enable foreign_keys=ON in connect() (verifying migrations tolerate it) or validate camera existence in Catalog.set_camera."}, {"severity": "minor", "file": "src/va/storage/structured/cameras.py", "line": 39, "issue": "CameraStore.get_or_create is a non-atomic SELECT-then-INSERT, so concurrent registration of the same camera id raises IntegrityError instead of returning the existing row, contradicting the documented idempotency contract.", "scenario": "Under WS-4, two chunk-ingests of the same camera run in parallel and both call get_or_create('cam-1'); the loser hits the SELECT before the winner's INSERT commits, then its own INSERT raises IntegrityError and aborts that ingest. Safe path: INSERT OR IGNORE followed by a re-SELECT."}, {"severity": "minor", "file": "COORDINATION.md", "line": 445, "issue": "The WS3.a log entry records 'Full suite 581 passed / 2 skipped' but the range's actual suite is 582 passed / 2 skipped — the count predates the final amend, recurring the prior round's stale-count finding.", "scenario": "The WS-4 agent (or the human approving the digest) cross-checks the logged count against a fresh run, sees a mismatch, and has to re-audit which tests the entry actually covers; the evidence gate requires real counts. Safe path: carry the /verify-generated count into the finalize digest and amend the entry."}]}
```
