# Agent review — approve

date: 2026-08-05T11:41:09.027919
range: origin/main..HEAD
branch: loop/ws4c-nvr-chunk-source
findings: 1

- **minor** `src/va/sources/nvr.py:152` — NVR dedup identity (source_key nvr:ch<n>:<epochs>) and camera id (nvr-ch<n>) omit any recorder identifier while the recorder itself is selected by the VA_NVR_HOST environment variable at fetch time.
  - scenario: A user repoints VA_NVR_HOST at a second recorder and ingests the same channel/window: ingest reports [already-ingested] and returns the first recorder's footage; both recorders' channel-N cameras also collapse into one cameras row, cross-linking motion queries. Safe path: fold a recorder id into source_key/camera id now (before multi-NVR workdirs exist) or document the single-recorder assumption in CLAUDE.md.

---

## Full review

## Review: `origin/main..HEAD` (WS4.b motion-episode scene detection + WS4.c nvr_recorded chunk source)

I read the full diff plus the surrounding code it touches (`manage.py`, `provenance.py`, `stale.py`, `registry.py`, `ingest.py`, the sidecar adapter) and the loop plan's "Done when" items. A pytest run was already live in this workspace, so per the repo lesson I did not launch another; this is a code-level review.

**What I verified and found sound:**

- **Correctness of the episode→segment math**: clamping, padding, min-span filtering, and the post-pad merge in `motion_episodes_inproc.py` are consistent; the overlap merge repairs the Role-1 "ordered, non-overlapping" contract after padding. `longest_clean_run`'s tie-breaking and the dirty-frame index preservation in `_frame_hammings` (keeping index→time mapping intact) are correct.
- **Contract change handled properly**: the `SceneDetector.detect` signature widening is defaulted, both existing backends were updated, ingest is the only caller, no test doubles of the protocol exist (I grepped per the 2026-08-03 lesson), and the change is logged in COORDINATION.md.
- **Provenance/stale direction**: folding `motion_source` config into the scene-detector fingerprint only for `motion-episodes` is the right §6-b direction (missed-stale forbidden), and `stale.py:52` overlays the per-video profile, so the profile-dependent fingerprint is computed correctly per video.
- **Degraded modes never abort ingest**: missing `start_epoch`, MotionSource failure, dangling `camera_id`, deleted camera at reingest — all warn and degrade, each with a test.
- **Plan conformance**: WS4.b and WS4.c "Done when" items each have a literal-ground-truth oracle test; both WS3.a carry-overs (`set_camera` validation, atomic `get_or_create`) and the WS4.a round-8 warn carry-over are done and tested. Timeline-drift and query-side-profile caveats are recorded in the loop backlog and CLAUDE.md.
- **Documentation parity**: the new URI form, 120 s cap, env vars, `query_margin_s` and the other knobs, the source-derived `security` default, and the unconfigured-sidecar warning are all in CLAUDE.md within this change.
- **Reingest flow**: the preserved-clip park into `cache/<source_key>.mp4` matches exactly what `fetch()` looks for; pre-attach of chunk metadata before ingest correctly fixes the epoch-blind-reingest bug and is regression-tested.

**One finding (minor):** the NVR identity has no recorder component. `source_key = nvr:ch<n>:<start>-<end>` (`nvr.py:152`) and camera id `nvr-ch<n>` (`nvr.py:158`) identify only channel + window, while the actual recorder is chosen by `VA_NVR_HOST` at fetch time. Point the env at a second NVR (the stated productization target is multi-user/multi-camera) and a pull of the same channel/window silently dedups to the *first* recorder's footage, and both recorders' channel-1 cameras share one `cameras` row, cross-linking motion queries. Safe path: fold a recorder identifier into `source_key` and the camera id (a breaking identity change — cheapest now, before any real multi-NVR workdir exists), or explicitly document the single-recorder assumption in CLAUDE.md and guard against host changes. Not blocking for the current single-LNR608 scope.

I also checked and dismissed: fractional-second URI truncation (consistent across resolve/fetch, within the documented ~1 s alignment slop), `_stop_load` exception masking in `finally` (curl is a hard dependency of the recipe), and offline coverage of the retry loop itself (pure helpers are unit-tested; the device loop is live-validated, matching the accepted WS4.a2 pattern and prior review rounds cited in the code).

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/sources/nvr.py", "line": 152, "issue": "NVR dedup identity (source_key nvr:ch<n>:<epochs>) and camera id (nvr-ch<n>) omit any recorder identifier while the recorder itself is selected by the VA_NVR_HOST environment variable at fetch time.", "scenario": "A user repoints VA_NVR_HOST at a second recorder and ingests the same channel/window: ingest reports [already-ingested] and returns the first recorder's footage; both recorders' channel-N cameras also collapse into one cameras row, cross-linking motion queries. Safe path: fold a recorder id into source_key/camera id now (before multi-NVR workdirs exist) or document the single-recorder assumption in CLAUDE.md."}]}
```
