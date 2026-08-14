# Agent review — approve

date: 2026-08-14T13:47:05.509496
range: origin/main..HEAD
branch: fix/yolo-world-reprime-and-reprocess
findings: 2

- **minor** `src/va/pipeline/reprocess.py:397` — replace_detections and replace_tracks run on separate connections/transactions, so a failure between them leaves new detections referencing track ids absent from object_tracks (old tracks still live), contradicting the stated prior-rows-intact invariant.
  - scenario: Under a running `va serve`, replace_detections succeeds and replace_tracks raises (or the process dies between them); until the stale role is retried, `va objects` shows new detections whose track_ids match nothing while `va count` answers from the old tracks. Fix: perform both replaces on one SQLite connection in a single transaction (same catalog.db file).
- **minor** `src/va/cli.py:565` — The `va reprocess` --help text still says only 'text/visual embedders + captioner wired, others → reingest' though object_detector (and tracker restamp) is now wired; CLAUDE.md was updated in this change but the operator-facing help was not.
  - scenario: An operator checks `va reprocess --help`, sees the detector unlisted, and re-runs the 238-window .va-24h repair via whole-video `va reingest` instead of the in-place reprocess this branch built. Fix: update the subparser help string in this same change.

---

## Full review

Review complete. I read the full diff (4 commits: YOLO-World re-prime fix, reprocess wiring for detector+tracker, ingest degradation surfacing, and the free-before-rebuild memory fix), then verified the supporting APIs (`MANAGER.unload`, `trace()` kwargs, `Workspace.video_dir`, `replace_detections`/`replace_tracks`, `config_for`, `stale_report`'s never-stamped-is-stale behavior, ingest's `_enabled` gate) and the CLI wiring. I could not execute the test suite (sandbox denied pytest), so test verification is static; the three new test files are well-constructed — they patch the real seams the reprocessor imports, exercise the real config overlay for the tracker-off case, and the weakref-based memory test genuinely discriminates both regressions it names.

What holds up well:

- The re-prime marker moved onto the shared model is the correct fix for per-instance guards over a `MANAGER`-shared model, and the rebuild-outside-the-`except` plus log-`str(e)`-not-`e` reasoning is sound Python traceback-lifetime analysis, backed by a test that fails on either regression.
- The reprocess tracker gate (`"object_tracker" not in cfg.roles or cfg.role("object_tracker").enabled`) exactly mirrors ingest's `_enabled`, and the executor's `disabled`-before-`satisfied` ordering means a stale-but-disabled tracker is routed to skipped, never restamped.
- The fps fallback to `visual_embedder` is null-safe and matches the real `.va-24h` gap; `stale_report` does treat never-stamped roles as stale, so that test scenario is real.
- Determinism-vs-correctness: the device-mismatch recovery is documented as live-validated on the real backend 2026-08-13, not just fake-tested.
- COORDINATION.md and CLAUDE.md were updated in the same change; commit subjects are plain-language.

Two minor findings survived verification:

1. **reprocess.py:397–406** — `replace_detections` and `replace_tracks` run on two separate SQLite connections, so a failure or crash between them leaves new detections whose `track_id`s reference tracks that don't exist while the old `object_tracks` rows survive. The docstring's "a mid-run failure leaves the prior rows intact" only holds up to the first write. The role stays stale so a retry heals it, but under a live `va serve` a `va count`/`va objects` reader in that window gets mutually inconsistent answers. Safe path: both tables live in the same `catalog.db` — do both replaces on one connection inside one transaction.

2. **cli.py:565** — the `va reprocess` help string still reads "text/visual embedders + captioner wired, others → reingest"; CLAUDE.md was updated but the operator-facing `--help` wasn't. An operator consulting `--help` would conclude the detector isn't wired and route the 238-window fix through whole-video `va reingest`. Safe path: update the help string in this change.

Suspicions I chased that dissolved: the stale-adapter-instance memory duplication after a rebuild (adapters are per-video and sequential); the fps=NULL restamp after a never-stamped reprocess (the null-safe fallback explicitly anticipates it); `_SATISFIES` restamping a profile-disabled tracker (the `disabled` check runs first); the executor stamping a tracker that wasn't in `stale_roles` (it only iterates the plan's stale set); `setattr` of the marker on an ultralytics `nn.Module`-derived model (plain tuple lands in `__dict__` normally, and the real backend was live-validated).

No critical or major findings, so the verdict is approve.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/reprocess.py", "line": 397, "issue": "replace_detections and replace_tracks run on separate connections/transactions, so a failure between them leaves new detections referencing track ids absent from object_tracks (old tracks still live), contradicting the stated prior-rows-intact invariant.", "scenario": "Under a running `va serve`, replace_detections succeeds and replace_tracks raises (or the process dies between them); until the stale role is retried, `va objects` shows new detections whose track_ids match nothing while `va count` answers from the old tracks. Fix: perform both replaces on one SQLite connection in a single transaction (same catalog.db file)."}, {"severity": "minor", "file": "src/va/cli.py", "line": 565, "issue": "The `va reprocess` --help text still says only 'text/visual embedders + captioner wired, others → reingest' though object_detector (and tracker restamp) is now wired; CLAUDE.md was updated in this change but the operator-facing help was not.", "scenario": "An operator checks `va reprocess --help`, sees the detector unlisted, and re-runs the 238-window .va-24h repair via whole-video `va reingest` instead of the in-place reprocess this branch built. Fix: update the subparser help string in this same change."}]}
```
