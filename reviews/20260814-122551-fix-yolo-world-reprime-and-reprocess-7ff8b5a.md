# Agent review — approve

date: 2026-08-14T12:31:16.504947
range: origin/main..HEAD
branch: fix/yolo-world-reprime-and-reprocess
findings: 1

- **minor** `src/va/adapters/object_detector/yolo_world_inproc.py:92` — _prime's rebuild keeps the failed model referenced via self._model while MANAGER.get loads the replacement, so unload()'s gc/empty_cache frees nothing and two copies of the weights are resident during recovery.
  - scenario: On a memory-tight device with a larger yolo-world variant, a set_classes failure caused by memory pressure (caught by the same except as the device mismatch) triggers the rebuild, which loads a second copy of the weights while the first is still pinned and OOMs — the recovery path fails where dropping self._model before MANAGER.unload would have let it succeed; the ingest then degrades to a skipped Role 5/6.

---

## Full review

Review complete. I examined all three commits in `origin/main..HEAD`, verified every API the new code calls (`ModelManager.unload`, `Config.role`/`roles`, `replace_detections`/`replace_tracks`, `Workspace.video_dir`, `ProvenanceStore.get/record`, `stale_report` row keys, `trace()` kwargs, `IngestResult` fields), traced the executor's disabled/satisfied/restamp ordering against the new `_SATISFIES` edge, and checked the new tests construct their scenarios for real. I could not execute the test suite (pytest runs require approval this session), so verification is by inspection; the branch's committed review artifacts record prior green runs.

**What holds up:**

- **Adapter fix (`40d4830`):** the `_va_primed_classes` marker lives on the shared model (correct across per-video adapter instances), is set only after a successful `set_classes` on both paths, and a doubly-failed prime propagates into ingest's best-effort detector guard — degrade-and-continue, never an aborted ingest. The three offline tests genuinely reproduce prime-once, changed-vocab, and crash-recovery against a fake that fails the way the real device mismatch does, and the live validation is recorded in the docstring.
- **Reprocess wiring (`7feee60`):** rows-first/restamp-second holds on every failure path; the zero-decoded-frames guard counts frames (not detections) so a detection-free video still reprocesses; the removed-mid-run re-check lands immediately before the destructive writes; the tracker gate exactly mirrors ingest's `_enabled` tolerance for an absent role; the executor's disabled-skip runs before the `satisfied` check so a profile-disabled tracker can never be restamped via `_SATISFIES`; the fps fallback is null-safe against the fps-NULL restamp a first never-stamped reprocess produces. The stamped-and-disabled tracker cell stays false-stale after a reprocess purges its rows (remedy remains `va reingest`) — the safe direction, and deliberately chosen per the docstring, so not a finding.
- **Degradation percolation (`7ff8b5a`, the fresh surface):** the aggregate warn fires from the same `failed`/`skipped` sets the roles maintain, hits the app logger independent of `VA_TRACE` (the exact condition that hid the .va-24h gap), and intentional skips stay trace-only info so security ingests don't warn on every window. Both tests construct their scenarios (a real failing detector load; a real clean ingest) and assert observable behavior. The events fit qa-and-traceability-plan T1.2's "degradations in the ingest trace" item; no new env vars/flags/config keys. The provisional subject is lifecycle-exempt; the two finalized subjects are plainly descriptive.

**One finding survived:**

- **Minor** — `src/va/adapters/object_detector/yolo_world_inproc.py:92`: in `_prime`'s recovery path, `self._model` still references the evicted model while `MANAGER.get` builds its replacement, so `unload()`'s `gc.collect()`/`empty_cache()` reclaims nothing and both copies of the weights are resident simultaneously. If the original `set_classes` failure was memory pressure rather than the device mismatch (the except deliberately catches both), the rebuild can re-OOM when it would have succeeded. Safe path: drop the reference (`self._model = None`) before `MANAGER.unload(self._key)` so the old weights are collectable during the rebuild. With yolov8s-world this is tens of MB and academic; it matters only if a larger variant is configured on a tight device — hence minor, not major.

Suspicions that dissolved: a stale `_va_primed_classes` marker on an evicted model could mask a partially-mutated vocab, but no concurrent-adapter path exists in ingest/reprocess/serve, so the old instance is always discarded first; the restamp recording `fps=None` for a never-stamped detector is explicitly handled by the null-safe fallback; monkeypatched registry bindings reach the function-local imports (re-read at call time).

Verdict: **approve** — the single finding is minor.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/adapters/object_detector/yolo_world_inproc.py", "line": 92, "issue": "_prime's rebuild keeps the failed model referenced via self._model while MANAGER.get loads the replacement, so unload()'s gc/empty_cache frees nothing and two copies of the weights are resident during recovery.", "scenario": "On a memory-tight device with a larger yolo-world variant, a set_classes failure caused by memory pressure (caught by the same except as the device mismatch) triggers the rebuild, which loads a second copy of the weights while the first is still pinned and OOMs — the recovery path fails where dropping self._model before MANAGER.unload would have let it succeed; the ingest then degrades to a skipped Role 5/6."}]}
```
