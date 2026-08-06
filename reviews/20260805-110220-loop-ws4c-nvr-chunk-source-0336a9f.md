# Agent review — request_changes

date: 2026-08-05T11:14:53.013194
range: origin/main..HEAD
branch: loop/ws4c-nvr-chunk-source
findings: 4

- **major** `src/va/registry.py:133` — Motion-episodes segments depend on the motion_source role's config, but scene_detector's provenance fingerprint excludes it and motion_source is not stamped — changing motion_source (e.g. sidecar->lnr-eventlog) is a missed stale, which the §6-b design forbids.
  - scenario: Security-profile chunk ingested with the default unconfigured sidecar lands zero segments stamped provenance-current; user configures motion_source: lnr-eventlog as the warning instructs; `va stale` reports nothing and the chunk stays unsegmented unless manually reingested.
- **minor** `src/va/sources/nvr.py:242` — A corrupt/truncated frame JPEG over 500 bytes makes Image.open raise, escaping the per-chunk retry loop and aborting the whole ingest instead of counting the frame dirty.
  - scenario: ffmpeg errors mid-write on one verification frame (check=False hides it); the >500-byte partial JPEG raises in PIL, the exception propagates through _pull_window, and the ingest is marked failed despite MAX_TRIES retry machinery existing for exactly this flakiness.
- **minor** `src/va/sources/nvr.py:137` — source_key epochs are computed from VA_NVR_TZ/system tz at resolve time, so the same naive-time nvr:// URI yields a different dedup identity when the environment changes.
  - scenario: Ingest with VA_NVR_TZ=America/Los_Angeles, later run `va reingest "nvr://..."` in a shell without it: lookup resolves a different source_key, reports not-found (or a fresh `va ingest` creates a duplicate row and re-pulls the same window).
- **minor** `src/va/sources/nvr.py:52` — The 120 s MAX_WINDOW_S cap on nvr:// windows is user-facing surface documented only in the runtime error message, not in CLAUDE.md/COORDINATION.md.
  - scenario: The next session scripts a 5-minute window pull from the CLAUDE.md example and only discovers the cap at runtime failure.

---

## Full review

The other session's pytest suite was still running, so per the repo's no-pile-up lesson (and this session's approval limits) I could not execute the new tests myself; the review below is from full static analysis of the two commits, the surrounding pipeline/provenance/manage code, the loop plan's done-when items, and COORDINATION.md.

## Review: `origin/main..HEAD` (WS4.b motion-episode scene detection + WS4.c nvr_recorded chunk source)

**What holds up well:** the `SceneDetector.detect` widening is source-compatible and logged in COORDINATION.md; degraded modes (missing epoch, MotionSource failure) warn and never abort ingest; WS4.b's "known windows → exactly those segments" done-when and WS4.c's "media + segments + start_epoch, idempotent reingest" done-when both have literal ground-truth tests; the WS3.a carry-overs (set_camera validation, atomic camera get_or_create) landed with tests; credentials travel via curl stdin config with escaping tests; the timeline-drift caveat is honestly documented and recorded in the loop backlog rather than presented as accurate.

**Findings:**

1. **Major — missed stale (`src/va/registry.py:133` / `src/va/provenance.py:50`).** The motion-episodes backend makes Role-1 output depend on the **`motion_source` role's config** (backend/model, `events_file`, host/tz), but `role_fingerprint("scene_detector", cfg)` hashes only the scene_detector spec, and `motion_source` is not in `PROVENANCE_ROLES`. Concrete scenario: all four shipped `security.yaml` profiles select motion-episodes while the default `motion_source` is the unconfigured sidecar — the first epoch-placed ingest lands **zero segments** (warned) and scene_detector is stamped provenance-current; the user then configures `motion_source: lnr-eventlog` as the warning instructs, runs `va stale`, and sees **nothing** — the chunk stays unsegmented forever unless manually reingested. That is exactly the "missed stale forbidden" failure §6-b's design rules out. Safe path: when `scene_detector.model == motion-episodes`, fold the motion_source role's salient spec into the scene_detector fingerprint (or stamp motion_source alongside it), and note the coupling in COORDINATION.md.

2. **Minor — broken error path in the pull retry loop (`src/va/sources/nvr.py:242`).** `_frame_hammings` treats a ≤500-byte frame file as dirty, but a *larger* truncated/corrupt JPEG (ffmpeg killed or erroring mid-write is exactly the flaky-device case; `check=False` hides it) makes `Image.open(...).convert("RGB")` raise, which escapes both the per-chunk `MAX_TRIES` loop and `_pull_window`, aborting the entire ingest — the retry machinery built for flaky device output can't see the failure it exists for. Safe path: wrap the per-frame hash in try/except and append the dirty sentinel (`10**6`), letting the existing clean-run/retry logic handle it.

3. **Minor — env-dependent dedup identity (`src/va/sources/nvr.py:137`).** `source_key` embeds epochs computed from `VA_NVR_TZ` (else system tz) at resolve time, so the *same* naive-time `nvr://` URI resolves to a different key if the environment differs between sessions: a later `va remove`/`va reingest <uri>` reports "not found", and a re-ingest silently creates a duplicate row and re-pulls footage already held. Safe path: document the foot-gun (identity is tz-of-resolve-dependent; use the UUID/source_key for lifecycle commands), or canonicalize by recording the tz used / requiring tz-aware times.

4. **Minor — undocumented surface (`src/va/sources/nvr.py:52`).** The 120 s `MAX_WINDOW_S` cap on `nvr://` windows is a real operational limit a user hits on their first long pull, but it appears only in the runtime error message — neither the new CLAUDE.md `nvr://` block nor COORDINATION.md mentions it. Safe path: one clause in the CLAUDE.md ingest comment ("windows capped at 120 s").

Verdict: request_changes on the strength of finding 1 — the change ships a default configuration whose documented first-run path (sidecar-unconfigured → zero segments) is unrecoverable through the repo's own staleness machinery.

```json
{"verdict": "request_changes", "findings": [
  {"severity": "major", "file": "src/va/registry.py", "line": 133, "issue": "Motion-episodes segments depend on the motion_source role's config, but scene_detector's provenance fingerprint excludes it and motion_source is not stamped — changing motion_source (e.g. sidecar->lnr-eventlog) is a missed stale, which the §6-b design forbids.", "scenario": "Security-profile chunk ingested with the default unconfigured sidecar lands zero segments stamped provenance-current; user configures motion_source: lnr-eventlog as the warning instructs; `va stale` reports nothing and the chunk stays unsegmented unless manually reingested."},
  {"severity": "minor", "file": "src/va/sources/nvr.py", "line": 242, "issue": "A corrupt/truncated frame JPEG over 500 bytes makes Image.open raise, escaping the per-chunk retry loop and aborting the whole ingest instead of counting the frame dirty.", "scenario": "ffmpeg errors mid-write on one verification frame (check=False hides it); the >500-byte partial JPEG raises in PIL, the exception propagates through _pull_window, and the ingest is marked failed despite MAX_TRIES retry machinery existing for exactly this flakiness."},
  {"severity": "minor", "file": "src/va/sources/nvr.py", "line": 137, "issue": "source_key epochs are computed from VA_NVR_TZ/system tz at resolve time, so the same naive-time nvr:// URI yields a different dedup identity when the environment changes.", "scenario": "Ingest with VA_NVR_TZ=America/Los_Angeles, later run `va reingest \"nvr://...\"` in a shell without it: lookup resolves a different source_key, reports not-found (or a fresh `va ingest` creates a duplicate row and re-pulls the same window)."},
  {"severity": "minor", "file": "src/va/sources/nvr.py", "line": 52, "issue": "The 120 s MAX_WINDOW_S cap on nvr:// windows is user-facing surface documented only in the runtime error message, not in CLAUDE.md/COORDINATION.md.", "scenario": "The next session scripts a 5-minute window pull from the CLAUDE.md example and only discovers the cap at runtime failure."}
]}
```
