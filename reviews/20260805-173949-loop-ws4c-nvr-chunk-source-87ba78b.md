# Agent review — approve

date: 2026-08-05T17:43:42.925626
range: origin/main..HEAD
branch: loop/ws4c-nvr-chunk-source
findings: 0


---

## Full review

Verification is complete. There's a live pytest run from the dev session (per the repo lesson I did not launch another — this round's verification is static, matching prior rounds), and the only delta since the round-8 approval is the rebase onto the merged WS4.b main plus a four-line comment in `tests/test_nvr_source.py` explaining the deliberately-redundant provenance-fingerprint twin test.

## Review — round 9, `origin/main..HEAD` (87ba78b, WS4.c nvr_recorded chunk source)

**Verdict: approve, no findings.**

This branch has been through eight prior review rounds; my job this round was to independently re-trace the current state rather than assume the fixes stuck. What I verified:

**Correctness.** I traced every pure function in `src/va/sources/nvr.py` against its tests: `parse_nvr_uri` (tz-aware conversion is correct for both the `VA_NVR_TZ` and system-local branches; window validation rejects end≤start and >120 s), `chunk_bounds` (covers the window, clipped tail), `longest_clean_run` (first-longest on tie, single-frame runs handled), and the trim math in `_pull_chunk_verified` (`run[0]/fps` to `(run[1]+1)/fps` is the correct inclusive-bounds conversion; `-ss` after `-i` is an accurate seek). `_frame_hammings` preserves the index→time mapping for torn/truncated frames by scoring them dirty instead of skipping (which would shift trim bounds) — regression-tested. The canonical-UTC stored URI round-trips to the same `source_key` under a different `VA_NVR_TZ` (tested), and fractional-second truncation is consistent between `resolve()` and `fetch()` since fetch re-parses the canonical URI.

**The reingest path** (the trickiest flow): `remove_video(keep_media=True)` parks the clip at `cache/reingest-<name>`, the nvr branch in `manage.py:129-133` moves it to exactly the filename `fetch()` reuses (`source_key.replace(":", "_") + ".mp4"` matches on both sides), and `_preattach_chunk_metadata` puts `start_epoch` on the recreated row *before* roles run — epoch first, camera attach best-effort with a warning, so a deleted camera can't crash a reingest after the purge. All three scenarios (dead device, deleted camera, idempotent re-ingest) have end-to-end tests.

**Contract and storage changes.** `ResolvedVideo` grew two defaulted optional fields (evolution-tolerant per the contracts rule; `model_rebuild()` resolves the forward ref) and is logged in COORDINATION.md. `CameraStore.get_or_create` is now genuinely atomic (INSERT OR IGNORE + re-SELECT; `rowcount` correctly distinguishes created from existing) and never clobbers a user rename (tested). `set_camera` validation is the right direction given FK pragma is off, and the dangling-link test in `test_motion_scene_detector.py` was correctly *strengthened*, not weakened — it now creates the dangle the only way it can still occur (delete after attach).

**Credential handling.** Creds travel via `--config -` on stdin, never argv; the curl-config escaping order (backslash before quote) is correct and newline injection is rejected loudly — each with a test. `--anyauth` is deliberate and documented (the endpoints are Basic-only).

**Combination coverage / docs.** The e2e oracle runs the source-derived `security` profile with a sidecar motion source and asserts literal ground truth (`segs == [(1.0, 4.0)]` from a known event + pad); the live device path is validated against the real LNR608 and recorded in the loop backlog ("LIVE-VALIDATED: probe → pull → ingest"), matching the accepted WS4.a2 pattern. CLAUDE.md documents the new URI form, the 120 s cap, env vars, the lighting-mismatch foot-gun, the timeline-drift caveat, and `query_margin_s` in the same change. The `need_agent_review:` subject is exempt from the clarity rule.

**Prior findings, re-judged.** All round 1–7 findings are fixed in code with regression tests (I spot-checked each cited "round-N review finding" comment against its actual implementation rather than trusting the comment). The round-8 minor — no recorder identifier in `source_key`/camera id while `VA_NVR_HOST` selects the recorder — is dispositioned in the loop backlog (`architecture-evolution-loop.md:231-235`) as carried to multi-NVR work (WS-5 territory), with the repointed-host consequence spelled out. That is the "document the assumption" arm of the safe path I'd accept for a single-recorder scope; re-reporting a tracked deferral would be churn, so I note it here rather than as a finding.

Suspicions I chased and dismissed: `_stop_load` inside `finally` re-raising on missing env (env is validated before the temp dir is created), stale `.frames` dir collisions across retry attempts (cleaned every pass), `shutil.move` onto an existing cache file (POSIX rename overwrite is intended), and the widened motion query dragging pre-window episodes into the chunk (clamping plus `min_span_s` confine them, and the straddle tests still pass unchanged).

```json
{"verdict": "approve", "findings": []}
```
