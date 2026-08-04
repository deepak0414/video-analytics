# Agent review — approve

date: 2026-08-03T18:48:47.652136
range: origin/main..HEAD
branch: loop/ws2c-profile-gates-roles
findings: 2

- **minor** `src/va/pipeline/ingest.py:139` — Retrying a failed profile-ingest without --profile ignores the row's recorded videos.profile and silently reverts to the source-derived default, while reingest deliberately carries the recorded profile forward; the CLI's not-applied notice only fires on the deduped (done) path.
  - scenario: `va ingest cam.mp4 --profile security` fails mid-fetch; the operator retries `va ingest cam.mp4`; the retry runs speech roles and the full detector vocab and records profile=generic with no warning — default a retry to the existing recorded profile (mirroring reingest_video) or print the notice on the retry path.
- **minor** `src/va/pipeline/reprocess.py:346` — _cfg_for_item's config_for() call sits outside the per-role try/except, so a video whose recorded footage-profile yaml was deleted/renamed raises FileNotFoundError and aborts the whole batch — contradicting the docstring's 'does NOT abort the batch (resumable)' contract (va stale's _current has the same shape and dies wholesale).
  - scenario: One video was ingested under a profile whose yaml is later renamed; `va reprocess --all-stale --yes` dies at that item leaving all later plan items unattempted, and `va stale` reports nothing at all — catch the config error per item, record it in `failed`, and continue.

---

## Full review

Review complete. This is round 4 of this branch (HEAD c6e38db, one commit past the last-reviewed 01d69ef). I verified all four round-3 minors were genuinely fixed in code and tests:

- The tracker-disabled branch now has end-to-end coverage (`test_disabled_tracker_stores_untracked_detections` asserts detections stored with `track_id` NULL, zero tracks, tracker unstamped).
- Skip-path purges are wrapped in their own try/except (`_purge` at `ingest.py:201-205`), so a locked catalog.db during bookkeeping can no longer abort the ingest.
- Core-role `enabled: false` is now rejected at load time (`_load_footage_overlay` raises; `test_core_role_cannot_be_disabled_by_a_profile`), and the stale exclusion is restricted to `GATEABLE_ROLES` — the missed-stale hole is closed from both ends.
- The generic.yaml example now demonstrates the real `classes:` / `enabled:` keys, in all four config dirs.

I ran the full offline suite myself: **557 passed / 2 skipped** (COORDINATION claims 555 — two tests were added since; direction is fine). The WS2.c done-conditions are met with real tests (security profile → 0 transcript rows + overridden vocab, generic byte-identical), the WS2.a carry-over (list-form `roles:` validation) is closed with named-error tests, schema v3 is migrated with fresh-vs-migrated tests, and every shared-surface change is logged in COORDINATION.md with the web-agent behavior note. I also chased two suspicions that dissolved: the tracker gate's short-circuit can't strand a disabled tracker unstamped-and-unpurged because `frames_dets` gets an entry for every frame even with zero detections, and the gate-order for a disabled diarizer on a silent video is both handled and tested.

Two new minors remain, both on non-happy paths:

**1. Retrying a failed profile-ingest without `--profile` silently reverts the video to the source default** (`src/va/pipeline/ingest.py:139-142`). The profile resolution (explicit arg > `active_footage_profile` > source default) never consults the existing row's recorded `videos.profile` for a not-done video. `reingest_video()` deliberately carries the recorded profile forward because silently resetting it was judged wrong there — but the plain retry path has exactly that behavior, and the CLI's "not applied" notice only fires on the *deduped* (done) path, so nothing warns. Scenario: `va ingest cam.mp4 --profile security` fails mid-fetch; the operator re-runs `va ingest cam.mp4`; the retry runs speech roles and the full detector vocab and records `generic`. The record stays honest (record==reality holds), but the operator's choice is dropped without a word. Safe path: when the existing row has a recorded profile and no explicit `--profile` is given, default to the recorded profile (mirroring reingest), or at least print the reingest-style notice on the retry path.

**2. A missing recorded-profile yaml aborts the whole stale/reprocess batch, contrary to the documented resumability contract** (`src/va/pipeline/reprocess.py:346`, same shape in `stale.py`'s `_current`). `execute_reprocess`'s docstring promises a failure "marks (video, role) stale and does NOT abort the batch", but `_cfg_for_item` calls `config_for()` outside the per-role try/except — if one video's recorded profile yaml was renamed/deleted since ingest, `FileNotFoundError` escapes and kills the loop for every remaining item (and `va stale` dies wholesale on the first such video, reporting nothing). Misconfiguration-only and fail-loud, hence minor. Safe path: catch the config error per item, append `(vid, role, error)` to `failed` like the handler path, and continue.

No contract breaks (schema migrated + logged), no hardcoded content (the security vocab is flagged as the plan §3.2 set with an "adjust per install" note), commit subjects are all provisional `need_agent_review:` and exempt from the clarity rule.

Verdict: **approve** — no critical or major findings; the two minors are cleanup-pass material.

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "src/va/pipeline/ingest.py", "line": 139, "issue": "Retrying a failed profile-ingest without --profile ignores the row's recorded videos.profile and silently reverts to the source-derived default, while reingest deliberately carries the recorded profile forward; the CLI's not-applied notice only fires on the deduped (done) path.", "scenario": "`va ingest cam.mp4 --profile security` fails mid-fetch; the operator retries `va ingest cam.mp4`; the retry runs speech roles and the full detector vocab and records profile=generic with no warning — default a retry to the existing recorded profile (mirroring reingest_video) or print the notice on the retry path."},
{"severity": "minor", "file": "src/va/pipeline/reprocess.py", "line": 346, "issue": "_cfg_for_item's config_for() call sits outside the per-role try/except, so a video whose recorded footage-profile yaml was deleted/renamed raises FileNotFoundError and aborts the whole batch — contradicting the docstring's 'does NOT abort the batch (resumable)' contract (va stale's _current has the same shape and dies wholesale).", "scenario": "One video was ingested under a profile whose yaml is later renamed; `va reprocess --all-stale --yes` dies at that item leaving all later plan items unattempted, and `va stale` reports nothing at all — catch the config error per item, record it in `failed`, and continue."}
]}
```
