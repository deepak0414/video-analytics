# Agent review — approve

date: 2026-08-06T22:46:34.170925
range: origin/main..HEAD
branch: loop/ws6a-durable-jobs
findings: 1

- **minor** `src/va/pipeline/ingest.py:513` — The two round-7 fixes (appearance_ref nulling, guarded requeue) shipped without pinning tests; the nulling sits in a swallow-all except, so a regression in it would be silent.
  - scenario: A later refactor changes the object_tracks id format or column name: the nulling UPDATE matches zero rows or raises (caught, trace-only), dangling appearance_ref pointers into a deleted appearance.npz return, and no test or visible signal catches it. Fix: add a test forging resume state with prior-attempt refs and assert they are nulled, plus a JobStore unit test that requeue_if_running is a no-op on a done row.

---

## Full review

All context checks pass: `_trace_fail(role, exc)` exists at module scope with a matching signature, `video_dir` is bound at `ingest.py:270` before the deletion block, and the dedup early-return at line 235 precedes it, so `done` videos never reach the shard-deletion path. My review is complete.

# Review — origin/main..HEAD (`7eff910`, WS6.a durable job queue, round 8)

## Summary

This HEAD is round 7's approved code plus the two fixes that round requested, both implemented correctly. **Verdict: approve, one minor finding** — the two new fix paths shipped without pinning tests, breaking this branch's own fix-plus-test convention.

## Prior-round follow-up (both round-7 minors fixed, verified)

- **Guarded requeue:** `stop()` now calls `JobStore.requeue_if_running` (`src/va/storage/structured/jobs_store.py:184` region), whose `WHERE id = ? AND state = 'running'` clause makes it a no-op against a terminal state the worker wrote just after the join timed out. I walked the interleavings: worker finishes before the read → `_current` is None, no write; worker writes `done` first → guard blocks; requeue lands first → the worker's later terminal write wins. The "last write wins" comment is now actually true.
- **Appearance-ref nulling:** the resume path that deletes `appearance.npz` now also nulls `object_tracks.appearance_ref` for that video (`src/va/pipeline/ingest.py:513-521`). I verified the WHERE clause matches storage reality — `TrackStore.replace_tracks` stores `video_id` as `str(uuid)` (`src/va/storage/structured/tracks.py:42`), same as the nulling's `str(video.id)`. It runs only on non-`done` rows (the dedup return at `ingest.py:235` filters completed videos), is wrapped best-effort so it can never abort ingest, and a successful tracker run rewrites the refs via `replace_tracks`.

## Verified and dissolved (not findings)

- **Test execution:** the authoring session has a full-suite run live (with an amend-on-green chain), so per the 2026-08-04 lesson I did not launch another; all eight tests verified by inspection. The monkeypatch interceptions are real (`_process` imports `ingest`/`ask` at call time, so module-attribute patches are seen), the graceful-stop test's `("queued", "failed")` acceptance correctly tolerates the late-unblock race, and the poison/innocent ordering holds (`pending()` orders by `created_at`).
- The shard-deletion filenames (`vectors`/`appearance` × `.npz`/`.json`) exactly match `NumpyFlatVectorStore`'s `with_suffix` layout; `video_dir` and `_trace_fail` are in scope with matching signatures.
- Plan conformance: WS6.a's done-when ("a test kills a worker mid-job and a restarted worker resumes it exactly once, idempotency asserted") is `test_crashed_running_job_resumes_exactly_once` plus the partial-shard non-duplication test. COORDINATION.md carries the cross-boundary ⚠ entry including the round-4/5 amendments; CLAUDE.md documents the restart semantics. No new env vars or config keys.
- Combination coverage: the queue/table layer is config-orthogonal (SQLite + file level, no role backends involved), so stub-path tests suffice — consistent with round 7's assessment.
- Not re-reported: the unclosed `sqlite3` connection on the nulling's exception path (transient leak inside a caught-and-traced block, no behavioral consequence) and the pre-existing stop/start re-entrancy window (unchanged from pre-WS6.a code).

## Finding

**1. minor — `src/va/pipeline/ingest.py:513` — both round-7 fixes landed without a pinning test, and the nulling's failure mode is silent.** Every prior review fix on this branch shipped with a test that fails on the pre-fix code (round 6's three fixes each did, per the review ledger); these two did not. The appearance-ref nulling is the riskier of the pair: it's raw SQL inside a swallow-all `except`, so a regression (wrong id format, renamed column, connection failure) would degrade to a trace line and silently resurrect the dangling-ref state round 7 flagged — no test or user-visible signal would catch it. The requeue guard's distinguishing behavior (refusing to clobber a terminal write) is likewise only indirectly exercised — `test_graceful_stop_requeues_without_attempt_guilt` accepts both `queued` and `failed` outcomes and pins only the attempt count. Safe path: extend `test_jobs_durable.py` with (a) a forged resume state carrying prior-attempt track rows with non-NULL `appearance_ref`, asserting they're nulled after the resumed run when the tracker doesn't rewrite them, and (b) a direct `JobStore` unit test that `requeue_if_running` is a no-op on a `done` row.

No critical or major findings.

```json
{"verdict": "approve", "findings": [
  {"severity": "minor", "file": "src/va/pipeline/ingest.py", "line": 513,
   "issue": "The two round-7 fixes (appearance_ref nulling, guarded requeue) shipped without pinning tests; the nulling sits in a swallow-all except, so a regression in it would be silent.",
   "scenario": "A later refactor changes the object_tracks id format or column name: the nulling UPDATE matches zero rows or raises (caught, trace-only), dangling appearance_ref pointers into a deleted appearance.npz return, and no test or visible signal catches it. Fix: add a test forging resume state with prior-attempt refs and assert they are nulled, plus a JobStore unit test that requeue_if_running is a no-op on a done row."}
]}
```
