# Agent review — approve

date: 2026-08-14T12:21:10.763627
range: origin/main..HEAD
branch: fix/yolo-world-reprime-and-reprocess
findings: 0


---

## Full review

Verification complete. Here is my review of `origin/main..HEAD` (two commits: the YOLO-World prime-once/survive-re-prime adapter fix at `40d4830`, and the `need_agent_review:` reprocess wiring for object_detector/object_tracker at `65681e7`).

## What I verified

**The adapter commit** has been through four committed review rounds (all four artifacts are in this range) and every prior finding was closed, including the live two-vocab validation recorded in the `_prime` docstring. I independently re-confirmed the final state: the `_va_primed_classes` marker is set only after a successful `set_classes` on both the happy and rebuild paths; `ModelManager.unload` exists with the assumed semantics; a doubly-failed prime propagates into ingest's best-effort detector guard (degrade-and-continue, never an aborted ingest); and the four offline tests genuinely reproduce the prime-once, changed-vocab, and crash-recovery behaviors against a fake that fails exactly the way the real device mismatch does.

**The reprocess commit** is the fresh surface. Two uncommitted worktree reviews of its earlier iterations (`441cf38`, `3273e94`) raised six findings; I verified all six are addressed at this HEAD:

- The tracker gate now mirrors ingest exactly (`"object_tracker" not in cfg.roles or cfg.role(...).enabled` — same tolerance for an absent role as ingest's `_enabled`), the disabled-tracker path writes untracked detections (`model_copy` shape identical to ingest's) plus zero tracks, and `test_reprocess_object_detector_honors_disabled_tracker` exercises it through the real config overlay via a temp `VA_CONFIG_DIR`, asserting the tracker is neither re-run nor restamped.
- The zero-decoded-frames guard raises before any write (counting frames, not detections, so a legitimately detection-free video still reprocesses), with a test proving prior rows survive and the role stays stale.
- The removed-during-reprocess re-check lands immediately before the destructive `replace_*` calls, matching `reindex_visual`, with a test that removes the video mid-track and asserts no resurrection and no restamp.
- The orphaned `appearance.{npz,json}` shard is dropped best-effort (the swallow is logged, and the filenames match what `_capture_appearance` writes at `video_dir / "appearance"`).
- The COORDINATION.md append-only entry exists with the web-agent implications, and the `_SATISFIES`/`_dependency_ordered` comments now describe both active edges.

Beyond re-checking those closures, I independently verified: the fps fallback chain is sound (`ProvenanceStore.record` is an upsert keyed by (video, role), so `rows[0]` is well-defined; visual_embedder shares the single-decode fps at ingest and is a critical always-stamped role; the null-safe `_recorded_fps` correctly survives the fps-NULL restamp a first reprocess of a never-stamped role produces, since `execute_reprocess` preserves `prev fps = None`). `stale_report` treats never-stamped roles as stale, so the motivating `.va-24h` 231-window scenario (constructed for real in the never-stamped test via raw SQL against the actual `role_provenance` table) flows through the fallback rather than refusing. The executor's disabled-role skip runs before the `satisfied` check, so a profile-disabled tracker can never be restamped via `_SATISFIES`. Rows-first/restamp-second ordering holds on every failure path.

**Dissolved suspicions**, for the record: monkeypatching `va.registry.get_ingest_classes` reaches the reprocessor despite its function-local import (re-read at call time); the two sequential `replace_*` transactions leave a brief window where new detections reference not-yet-written tracks, but this matches ingest's own write pattern, no current query path joins across it, and a crash there leaves the role stale for a clean retry; a tracker-only-stale video (e.g. an iou→bytetrack swap) routes to `va reingest` with an explicit skip message — the documented D5 scope cap, not a silent gap; a mid-batch config edit can only make the restamp read stale-again (never falsely current), same as the sibling reprocessors. Documentation parity holds (no new env vars/flags/config keys; the CLAUDE.md reprocess line and COORDINATION.md entry are in the change), the finalized adapter subject is plainly descriptive, and the HEAD subject is a provisional `need_agent_review:` (exempt). I could not run the suite from this session (sandboxed), and the worktree carries unrelated uncommitted edits (`ingest.py`, a degradation-trace test) that would pollute a run anyway — my verification of the new tests is by inspection: each constructs its scenario for real (real ingest, real synth video, real overlay config) and would fail on the pre-fix code paths it names.

No findings survived verification.

Verdict: **approve**.

```json
{"verdict": "approve", "findings": []}
```
