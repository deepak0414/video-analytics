# Agent review — approve

date: 2026-08-05T10:46:14.510678
range: origin/main..HEAD
branch: loop/ws4c-nvr-chunk-source
findings: 2

- **minor** `src/va/sources/nvr.py:194` — NVR credentials are passed via curl's -u on the subprocess command line, exposing VA_NVR_PASS in the process list for every transfer, unlike the lnr adapter which keeps the same credentials in-process.
  - scenario: On a shared machine, any unprivileged local user polling ps//proc/<pid>/cmdline during a pull (many curl invocations per window) captures the NVR admin password; safe path is --config /dev/stdin or a 0600 netrc file in the pull-private tempdir.
- **minor** `src/va/pipeline/manage.py:155` — _preattach_chunk_metadata calls the now-validating Catalog.set_camera after remove_video's destructive purge and before set_start_epoch, so a dangling camera_id crashes the reingest mid-cycle and drops the epoch attach as well.
  - scenario: A chunk whose camera row was deleted out from under it (acknowledged as possible in set_camera's own docstring; ingest's read path merely warns on the same condition): va reingest purges all role data, then dies on ValueError with the recreated row carrying neither camera nor start_epoch — a later plain ingest retry of a local-source chunk completes epoch-blind, landing the silently-degraded full-span segmentation stamped provenance-current that WS4.b exists to prevent.

---

## Full review

A pytest run is already live (PID 3899176), so per the repo's pile-up lesson I will not launch another test run and will rely on static verification.

# Review — origin/main..HEAD (WS4.b motion-episode scene detection + WS4.c nvr_recorded chunk source)

**Verdict: approve** (two minor findings, nothing blocking).

## What I verified as clean

- **Plan conformance:** Both done-when items are met with literal ground truth in tests — WS4.b's known-window oracle (`test_known_windows_become_exactly_those_segments`, plus the end-to-end ingest test asserting `[(1.0, 5.0)]` in the segments table) and WS4.c's pulled-window ingest landing media + segments + `start_epoch` with idempotent re-ingest. All three carry-overs (lnr unparseable-End-Time warning, `set_camera` validation, atomic `CameraStore.get_or_create`) are closed exactly as the loop file specifies, each with a regression test.
- **Prior review round:** the six minor findings from the 5488a43 review are resolved in HEAD — dirty frames now get a sentinel Hamming instead of being skipped (`_frame_hammings` keeps index→time mapping intact), pulls run in a private tempdir with an atomic final rename, NVR reingest reuses preserved cache media (tested with a dead-device stub), the live-snapshot lighting constraint and `query_margin_s` are documented in CLAUDE.md, and timeline drift is recorded as an explicit plan backlog item with the caveat in CLAUDE.md/COORDINATION.md. None repeated here.
- **Contracts:** `SceneDetector.detect` gained an optional defaulted parameter (source-compatible; both visual backends accept-and-ignore); `ResolvedVideo` grew defaulted optional fields; both logged in COORDINATION.md. No scene-detector lambda doubles exist in tests (checked per the 2026-08-03 lesson). No DB schema change — `camera_id`/`start_epoch` columns pre-exist.
- **Best-effort discipline:** MotionSource failure and missing epoch degrade to a warned full-span segment, never aborting ingest; a hard NVR *fetch* failure correctly fails the ingest (a source is not a best-effort role).
- **Determinism vs ground truth:** the security-profile scene_detector switch is backed by a measured comparison on the 22 real NVR clips (pyscenedetect 1 segment on 21/22 ≡ the full-span fallback), recorded in the profile comment; the `query_margin_s` default is live-validated with the before/after measurements ((0,2) → (0.0, 31.7)) in COORDINATION.md. The dHash thresholds carry their measured basis.
- **Time math:** tz-aware URI parsing tested for UTC and a DST zone; clamp/pad/merge edges each have literal-truth tests; `longest_clean_run` hand-traces correctly including ties and longest-not-first.
- **Docs:** `nvr://` form, security-profile default, verify-and-trim caveats, and all four knobs are in CLAUDE.md; both commit subjects are provisional `need_agent_review:` (exempt from the clarity rule).

## Findings

**1. minor — `src/va/sources/nvr.py:194` — NVR credentials are passed on the curl command line, exposing them in the process list.** Every transfer runs `curl -u user:pass …` via subprocess, so `VA_NVR_PASS` is readable by any local process via `ps`/`/proc/<pid>/cmdline` for the duration of each pull (many invocations per window). This is a hygiene regression relative to the lnr adapter, which deliberately keeps the same credentials in-process as a urllib header, and sits oddly next to this file's own "credentials never live in config files" stance. Scenario: on a shared box, any unprivileged user polling the process list during an ingest captures the NVR admin password. Safe path: feed credentials to curl off-argv — `--config /dev/stdin` with `user = "user:pass"` piped in, or a 0600 netrc file inside the pull-private tempdir (`--netrc-file`).

**2. minor — `src/va/pipeline/manage.py:155` — reingest can now crash *after* the destructive removal if the camera row is dangling, losing the epoch attach too.** `Catalog.set_camera` newly raises `ValueError` on a missing camera row, and `_preattach_chunk_metadata` calls it after `remove_video` has already purged all role data — and *before* `set_start_epoch`, so the recreated pending row ends up with neither camera nor epoch. `reingest_video`'s own design rule is "validate before the destructive removal" (it does this for the profile), and the ingest read path handles the identical dangling-camera condition with a warning instead. Scenario: a chunk whose camera row was removed (the `set_camera` docstring itself acknowledges dangling links "can still arise from later camera deletion"; no delete API exists today, so the trigger is currently manual DB surgery or a future WS-3 web-layer delete) — `va reingest` purges the video's data, then dies on ValueError; for a non-NVR chunk (the WS4.b local-source pattern) a later plain `va ingest` retry completes the row epoch-blind, producing the silently-degraded full-span segmentation stamped provenance-current that WS4.b's fix exists to prevent. Safe path: in `_preattach_chunk_metadata`, set `start_epoch` first and wrap `set_camera` in a caught-and-warned `ValueError` (mirroring ingest's degraded-mode warning), or validate the camera row up front next to the profile validation, pre-removal.

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "src/va/sources/nvr.py", "line": 194, "issue": "NVR credentials are passed via curl's -u on the subprocess command line, exposing VA_NVR_PASS in the process list for every transfer, unlike the lnr adapter which keeps the same credentials in-process.", "scenario": "On a shared machine, any unprivileged local user polling ps//proc/<pid>/cmdline during a pull (many curl invocations per window) captures the NVR admin password; safe path is --config /dev/stdin or a 0600 netrc file in the pull-private tempdir."},
{"severity": "minor", "file": "src/va/pipeline/manage.py", "line": 155, "issue": "_preattach_chunk_metadata calls the now-validating Catalog.set_camera after remove_video's destructive purge and before set_start_epoch, so a dangling camera_id crashes the reingest mid-cycle and drops the epoch attach as well.", "scenario": "A chunk whose camera row was deleted out from under it (acknowledged as possible in set_camera's own docstring; ingest's read path merely warns on the same condition): va reingest purges all role data, then dies on ValueError with the recreated row carrying neither camera nor start_epoch — a later plain ingest retry of a local-source chunk completes epoch-blind, landing the silently-degraded full-span segmentation stamped provenance-current that WS4.b exists to prevent."}
]}
```
