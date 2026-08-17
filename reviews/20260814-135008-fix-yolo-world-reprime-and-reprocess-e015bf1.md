# Agent review — approve

date: 2026-08-14T13:55:08.810216
range: origin/main..HEAD
branch: fix/yolo-world-reprime-and-reprocess
findings: 0


---

## Full review

Review complete. I read all four commits in `origin/main..HEAD` and verified every seam the new code touches: `ModelManager.get/unload`, `trace()` kwargs, ingest's `failed`/`skipped` sets and `_enabled` gate, `GATEABLE_ROLES`/`GATE_DEPENDENTS`, `replace_detections`/`replace_tracks`, `Workspace.video_dir`, `config_for`, the executor's disabled-before-satisfied ordering, and the synth/manage helpers the tests use. A pytest run was already live in this environment, so per the repo's piled-suites lesson I did not launch another; test verification is static (both prior reviews on this branch also record the construction checks, and the branch's committed review artifacts record green runs).

**The head commit (`e015bf1`) delivers what the earlier major finding demanded.** The previous review (7ab3e90) showed the free-before-rebuild was a no-op because the rebuild ran inside `except Exception as e:` where the live traceback pins the evicted model. The current code uses exactly the prescribed flag pattern: catch → log `str(e)` (not the exception object, so no retaining log handler keeps the traceback via `record.args`) → set `rebuild = True` → drop/unload/rebuild/re-prime after the `try/except`. The reworked test takes a `weakref.ref` to model #1, installs its own retaining log handler, and asserts the model is dead inside the unload spy after `gc.collect()` — I walked the object graph statically (except block exited, args are strings/lists, adapter ref dropped, cache popped) and the assertion is both achievable on the fixed code and would fail on either regression it names. This is no longer a decoration test.

**The earlier minors were also resolved:** the `va reprocess --help` string now names `object_detector` (cli.py:565), and the two-connection replace window is answered with a code comment whose claim I verified — no query path joins `object_detections` to `object_tracks` (the only co-mention is `va remove`'s deletion list), the role stays stale across the window, and the single-transaction fix is explicitly deferred as a store-layer change. That finding dissolves.

**What else holds up:** the reprocess tracker gate exactly mirrors ingest's `_enabled` tolerance for an absent role; a stale-but-disabled tracker is routed to `skipped` before the `satisfied` check, so the new `_SATISFIES` edge can never restamp a profile-forbidden role; the fps fallback to `visual_embedder` is null-safe against the fps-NULL restamp its own first run produces; rows are built fully before any write, the zero-decoded-frames guard counts frames rather than detections, and the removed-mid-run re-check sits immediately before the destructive writes. The degradation surfacing fires from the same `failed`/`skipped` sets the roles maintain and warns independently of `VA_TRACE` — the exact condition that hid the original gap. CLAUDE.md, COORDINATION.md, and the CLI help were all updated in the same change; the three finalized commit messages are plain-language and self-sufficient, and the provisional `need_agent_review:` head is exempt.

**Suspicions I chased that dissolved:** a stale `_va_primed_classes` marker misleading a second live adapter after a rebuild (adapters are per-video and sequential in ingest/reprocess/serve); `fps=0.0` short-circuiting the `or` fallback (0 fps is not a real recorded value); the executor restamping a tracker absent from `stale_roles` (it iterates only the plan's stale set); the reprocessors loading `config_for` fresh instead of the executor's pinned config (pre-existing pattern shared with the visual/captioner reprocessors, not introduced by this range); and `_drop_appearance_shard` in the tracker-off branch (unlink is `missing_ok`).

No findings survived verification.

```json
{"verdict": "approve", "findings": []}
```
