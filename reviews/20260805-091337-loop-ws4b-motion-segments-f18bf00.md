# Agent review — approve

date: 2026-08-05T09:17:42.058090
range: origin/main..HEAD
branch: loop/ws4b-motion-segments
findings: 2

- **minor** `run-siglip/config/profiles/footage/security.yaml:25` — Round-1 carry: real-model + security changes Role 1 for existing epoch-less NVR clips from pyscenedetect per-shot segments to one full-span segment, and the commit still records neither a golden re-run nor a measurement showing the fixtures are unaffected.
  - scenario: Reingesting the .va-nvr golden clips under current code yields one segment/caption per clip while the pre-ingested golden harness stays green on stale data; caption/action fixtures calibrated on per-shot segmentation may silently no longer describe what ingest now produces. Fix: run the golden gate for the PR (the diff touches four golden-verified critical paths, so WT.7 requires the attestation anyway) or record the pyscenedetect-segments-per-static-clip measurement, and name this combination in the digest.
- **minor** `src/va/pipeline/ingest.py:254` — When videos.camera_id is set but the cameras row is missing (FK declared but unenforced — known WS4.c gap), camera_ref silently falls back to None, which the MotionSource contract interprets as ALL cameras.
  - scenario: In a multi-camera workdir a chunk whose camera row was deleted gets segmented by other cameras' motion episodes with no warning — unlike every other degraded mode in this change, which warns. Fix: log a warning when camera_id resolves to no camera row before falling back.

---

## Full review

Review complete. This is round 2 of the WS4.b review: I verified that both actionable round-1 findings were genuinely fixed in the amended commit (`reingest_video` now pre-attaches camera/start_epoch before `ingest()` with a regression test that fails on the old code, and the unconfigured sidecar now warns, also with a test), traced the pad/clamp/merge math in `motion_episodes_inproc.py` across every edge (straddling start, beyond-end, padding-induced overlap, slivers, empty events, missing epoch, source failure) and found it correct, and confirmed the Protocol extension is source-compatible, logged in COORDINATION.md, and documented in CLAUDE.md with its knobs. The WS4.b done-when ("known motion windows yield exactly those segments, ground truth in the test") is met by the end-to-end ingest oracle. Two pytest processes were already live, so per the repo lesson I did not launch another run; verification is static.

## What holds up

- The reingest fix is the right shape: metadata lands on the recreated row *before* ingest, the failure path is covered by the pre-created row, and `update_metadata`/`set_paths` don't clobber `camera_id`/`start_epoch` (verified in `catalog_sqlite.py`).
- No test was deleted or weakened — the split of the sidecar test into configured-but-absent (silent) vs unconfigured (warns) strengthens both branches.
- The lnr flat-shape End-Time warning closes the WS4.a round-8 carry-over with a regression test.
- No disputes in `workflow-trust-plan.md` touch these findings.

## Findings

**1. Minor — round-1 finding 3 is still unaddressed in the commit.** Under `run-siglip`/`run-claude`/`run-qwen3vl` + `security`, every existing epoch-less real NVR clip (the 22 clips behind the `.va-nvr` golden workdir) changes from pyscenedetect per-shot segments to ONE full-span segment, and the diff records neither a golden re-run nor the "pyscenedetect already produced ~1 segment on static clips" measurement. The diff also touches four `golden-verified` critical paths (`src/va/adapters/`, `src/va/pipeline/`, `config/`, `run-*/config/`), so the WT.7 attestation will be required at PR time anyway. Safe path: run the golden gate (or record the segment-count measurement) and name this combination in the digest — the degrade *behavior* is documented in CLAUDE.md, but the effect on the calibrated fixtures is not measured anywhere in-repo.

**2. Minor — a dangling `camera_id` silently widens motion queries to ALL cameras.** In `ingest.py:254`, `camera_ref = cam.source_ref if cam else None` — if the `cameras` row is missing (the FK is declared but unenforced, the known WS4.c carry-over), `camera_ref` becomes None, which per the MotionSource contract means "all cameras". Scenario: in an A-MCLSSRVF workdir, a chunk whose camera row was deleted gets segmented by *other* cameras' motion episodes, with no warning — unlike every other degraded mode in this change, which warns. Safe path: log a warning when `camera_id` is set but resolves to no camera row (degrading to all-cameras or to the no-epoch full-span path, either is defensible — but audibly).

Neither finding is major, so the verdict is approve.

```json
{"verdict": "approve", "findings": [
  {"severity": "minor", "file": "run-siglip/config/profiles/footage/security.yaml", "line": 25, "issue": "Round-1 carry: real-model + security changes Role 1 for existing epoch-less NVR clips from pyscenedetect per-shot segments to one full-span segment, and the commit still records neither a golden re-run nor a measurement showing the fixtures are unaffected.", "scenario": "Reingesting the .va-nvr golden clips under current code yields one segment/caption per clip while the pre-ingested golden harness stays green on stale data; caption/action fixtures calibrated on per-shot segmentation may silently no longer describe what ingest now produces. Fix: run the golden gate for the PR (the diff touches four golden-verified critical paths, so WT.7 requires the attestation anyway) or record the pyscenedetect-segments-per-static-clip measurement, and name this combination in the digest."},
  {"severity": "minor", "file": "src/va/pipeline/ingest.py", "line": 254, "issue": "When videos.camera_id is set but the cameras row is missing (FK declared but unenforced — known WS4.c gap), camera_ref silently falls back to None, which the MotionSource contract interprets as ALL cameras.", "scenario": "In a multi-camera workdir a chunk whose camera row was deleted gets segmented by other cameras' motion episodes with no warning — unlike every other degraded mode in this change, which warns. Fix: log a warning when camera_id resolves to no camera row before falling back."}
]}
```
