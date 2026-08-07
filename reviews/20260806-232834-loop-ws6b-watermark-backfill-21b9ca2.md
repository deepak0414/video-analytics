# Agent review — approve

date: 2026-08-06T23:33:47.535534
range: origin/main..HEAD
branch: loop/ws6b-watermark-backfill
findings: 2

- **minor** `src/va/pipeline/watch.py:334` — The shared max_windows budget is consumed in fixed store.list() camera order, so a deeply backlogged first camera exhausts it every pass and later cameras ingest nothing until it fully catches up — contradicting the module docstring's claim that the cap prevents starving other cameras.
  - scenario: Multi-day outage across several cameras: every pass spends all 50 windows on nvr-ch1; nvr-ch2..N stay truncated at their old watermarks while their oldest footage expires from the ~6-day NVR ring — footage a per-camera budget split (max_windows // n) or rotating start order would have pulled in time.
- **minor** `COORDINATION.md:71` — The WS6.b entry enumerates the `va watch` CLI flags but omits the new --cluster-gap and --open-instant-age flags (CLAUDE.md's watch block also doesn't name them), leaving the cross-agent record of the surface incomplete — including the scene_detector gap_s name-collision caveat that only lives in --help.
  - scenario: Another agent (or the web frontend session) wires a watch invocation from the COORDINATION/CLAUDE.md description, never learns the episode-clustering gap is a separate knob from the profile's scene_detector gap_s, and tunes the wrong one; document both flags with the divergence note in the finalize pass.

---

## Full review

Review complete. This branch has been through many prior review rounds and all previously-flagged criticals/majors are verifiably fixed at HEAD with covering regression tests: the open-instant clustering data-loss bug (deferral now computed on raw events before `cluster_events`, tested at ≤gap_s merge distance), the attempts-bump-while-queued issue (resumed rows revert to `queued`, tested), the vector-shard duplication on resume (shards deleted + appearance refs nulled, tested), the poison-job cap, and the requeue-vs-terminal-write race. I confirmed the lnr backend really emits the `open` attribute the watcher keys on, `_pull_window` stubbing in tests bypasses the whole device layer (so the test rig is valid), the schema goes v6→v8 through ordered migrations logged in COORDINATION.md, and both plan "Done when" oracles exist (`test_crashed_running_job_resumes_exactly_once`, `test_outage_backfills_exactly_the_gap_once`; the ~6-day ring SLA is documented in CLAUDE.md/COORDINATION.md/the module docstring). Two pytest runs were already live, so per the repo lesson I did not launch another suite.

Two minor findings remain:

**1. Minor — `src/va/pipeline/watch.py:334` — the shared `max_windows` budget is spent in fixed camera-list order, so a backlogged first camera starves the rest, contradicting the module docstring's claim.** The docstring says the cap exists "so one giant backlog cannot starve the other cameras," but `budget` is global and the camera loop always starts from `store.list()` order: when camera A holds a multi-day backlog, every pass spends all 50 windows on A and cameras B..N mark `truncated` with zero ingests until A fully reaches its horizon. During a near-SLA outage (the exact scenario this watcher exists for), the later cameras' oldest footage keeps aging out of the ~6-day ring while they wait — footage that per-camera budgeting would have saved. Safe path: split the budget per camera (`max_windows // len(cameras)`, remainder to the first) or rotate the starting camera each pass; either preserves the bound while making the docstring's no-starvation claim true.

**2. Minor — `COORDINATION.md:71` (WS6.b entry) — the logged `va watch` CLI surface omits the `--cluster-gap` and `--open-instant-age` flags.** These are precisely the two knobs the round-3 review flagged as undocumented; the fix promoted them to CLI flags with good `--help` text (including the "independent of the scene_detector gap_s" caveat), but the COORDINATION.md entry enumerates the flag list as `[--camera ...] [--lookback-hours] [--settle] [--max-windows] [--interval]` and CLAUDE.md's watch block likewise names only the other bounds — so the cross-agent record of the new surface is incomplete, and the scene-detector `gap_s` name collision (a real foot-gun) is invisible outside `--help`. Safe path: add the two flags (with the gap_s-divergence note) to the COORDINATION.md entry and the CLAUDE.md watch comment when finalizing.

Everything else I probed dissolved: the quiet-advance guard's stale `cam.last_processed_epoch` comparison is safe because the SQL watermark update is itself monotonic; deduped replay windows are free (dedup fires before the device pull — pinned by `len(pulls) == 5`); the aged-open-instant recovery window can't overlap neighboring pulls because the recovered event re-enters clustering; the `_process`/`catch_up` late-binding imports make the tests' monkeypatching sound; `update(..., "queued")` clearing error/result is correct for a re-run; and the graceful-stop requeue is guarded against the terminal-write race.

No critical or major findings → approve.

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "src/va/pipeline/watch.py", "line": 334, "issue": "The shared max_windows budget is consumed in fixed store.list() camera order, so a deeply backlogged first camera exhausts it every pass and later cameras ingest nothing until it fully catches up — contradicting the module docstring's claim that the cap prevents starving other cameras.", "scenario": "Multi-day outage across several cameras: every pass spends all 50 windows on nvr-ch1; nvr-ch2..N stay truncated at their old watermarks while their oldest footage expires from the ~6-day NVR ring — footage a per-camera budget split (max_windows // n) or rotating start order would have pulled in time."},
{"severity": "minor", "file": "COORDINATION.md", "line": 71, "issue": "The WS6.b entry enumerates the `va watch` CLI flags but omits the new --cluster-gap and --open-instant-age flags (CLAUDE.md's watch block also doesn't name them), leaving the cross-agent record of the surface incomplete — including the scene_detector gap_s name-collision caveat that only lives in --help.", "scenario": "Another agent (or the web frontend session) wires a watch invocation from the COORDINATION/CLAUDE.md description, never learns the episode-clustering gap is a separate knob from the profile's scene_detector gap_s, and tunes the wrong one; document both flags with the divergence note in the finalize pass."}
]}
```
