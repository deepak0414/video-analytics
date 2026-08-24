# Agent review — approve

date: 2026-08-22T21:16:15.376436
range: origin/main..HEAD
branch: feature/nvr-clock-gate-default-reader
findings: 2

- **minor** `src/va/sources/nvr.py:311` — The commit body still states CLOCK_OCR_TOL_S=600s 'between the 56s drift max and the 12h foreign floor' while the code is 12 h + 300 s and the docs now say 12 h + 5 min (prior-round minor 3 was only half-closed).
  - scenario: The finalize amend reuses this body and ships a wrong constant and rationale into history. Safe path: rewrite the nvr.py bullet in the /task-commit digest before consuming .commit-approved.
- **minor** `scripts/validate_clock_gate_va24h.py:166` — --real mode builds the reader via default_timestamp_reader(), which localizes OCR readings with VA_NVR_TZ/system-local and ignores --tz, while _body_status and --proxy use --tz.
  - scenario: On a box whose system tz differs from the NVR's (VA_NVR_TZ unset), `--real --tz America/Los_Angeles` OCRs heads in the wrong zone but classifies bodies in LA, so the tally and the body-aligned split disagree (masked up to 12 h by the coarse tolerance; >12 h rejects every clip). Safe path: OcrClockReader(RapidOcrFrameOcr(), tz=tz) in run_real, or set VA_NVR_TZ from --tz before building the reader.

---

## Full review

I have what I need. Summary of the review, then the verdict block.

## Review — `origin/main..HEAD` (one commit, `a93e0a1`)

**Scope checked:** the full diff (`ocr_clock.py`, `nvr.py`, `frames.py`, `conftest.py`, both test files, the validation script, CLAUDE.md / COORDINATION.md / the dispute record), plus the unchanged code it plugs into — `verify.py`'s `_head_trim` gate, `_pull_window`'s two-phase fallthrough, `fetch()`'s cache-reverify branch, `watch.py`'s failure branch, `manage.py`'s reingest media parking, the RapidOCR adapter's `_read_frame`, `Workspace.video_dir`, and the census "Mandatory" list. I could **not** execute pytest or the `.va-24h` validation script in this session (commands need approval that isn't available non-interactively), so "suite green" and the 211/5/22 tally are the author's / Stop-gate's claims, not mine.

**Prior-round findings re-judged.** Round-6's three minors: (1) the substitution residual is now documented in `ocr_clock.py`, CLAUDE.md and COORDINATION.md — closed; (2) `_body_status` now splits aligned / off / unreadable and the docs state 3 whole-clip + 19 body-aligned — closed; (3) the docs now say "12 h + 5 min", but the **commit body still says `CLOCK_OCR_TOL_S=600s`** — half-closed, re-flagged below. The round-3 dispute (long-head reject ≠ stall) I accept on the merits: `_pull_window` catches a phase-1 `DeliveryRejected`, retries, then falls through to the exact-window phase 2 with no pre-pad seek (`nvr.py:590-596`).

**Verified and not reported:** the `_AUTO_READER` sentinel keeps `base.resolve_source()`'s bare `NvrRecordedSource()` auto-wired while explicit `None` forces the gate off and an explicit reader injects; `default_timestamp_reader()` returns before probing `rapidocr` when the knob is off, so the conftest keeps every bare construction model-free; the coarse tolerance applies only with a reader; `clock_readings_from_texts` keeps every ≥2-member cluster in time order so the verifier can trim between a foreign head and an aligned tail; 12 AM/PM and DST-fold arithmetic are right; `RapidOcrFrameOcr` self-disable is per-pull (a fresh source per `resolve_source`), not per-process; the cache-reverify restore is safe because `_pull_window` only lands `out` via a final atomic rename and the new test constructs the real scenario (cache clip + raising `_pull_window`); `head_clock_frames` endpoint math matches its test (30 frames → indices 0..29, last t=1.45); the reader builds the adapter with `load=None`, which equals the run-*/config OCR spec (no load params) and shares the `rapidocr::en` ModelManager key with Role 10; no schema/contract change; `VA_NVR_CLOCK_GATE`, `RUN_OCR_CLOCK`, the conftest force-off and the script are documented.

### MINOR 1 — commit body still carries the stale `CLOCK_OCR_TOL_S=600s`
`HEAD` commit message, nvr.py bullet: "coarse clock tolerance (CLOCK_OCR_TOL_S=600s) … between the 56s legit-drift max and the 12h foreign floor" — the code is `12 * 3600 + 300` (`src/va/sources/nvr.py:311`) and the docs now say 12 h + 5 min. Scenario: the finalize amend reuses this body and ships a wrong constant and a wrong rationale into history. Safe path: rewrite the bullet in the `/task-commit` digest before the sentinel is consumed.

### MINOR 2 — the validation script's `--tz` is silently ignored in `--real` mode
`scripts/validate_clock_gate_va24h.py:166` builds the reader via `default_timestamp_reader()`, whose `OcrClockReader` has `tz=_UNSET` and resolves `VA_NVR_TZ` / system-local per read, while `_body_status` and the proxy mode localize with `--tz`. Scenario: on a box whose system tz ≠ the NVR's and with `VA_NVR_TZ` unset, `--real --tz America/Los_Angeles` OCRs head readings in the wrong zone but classifies bodies in LA — the accept/trim/reject tally and the body-aligned split stop describing the same thing (masked up to 12 h by the coarse tolerance; a >12 h offset rejects every clip). On this box the system tz is PDT, so the documented 211/5/22 is consistent — but the flag is a foot-gun for reproduction. Safe path: `OcrClockReader(RapidOcrFrameOcr(), tz=tz)` in `run_real` (or set `VA_NVR_TZ` from `--tz` before building the reader).

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "src/va/sources/nvr.py", "line": 311, "issue": "The commit body still states CLOCK_OCR_TOL_S=600s 'between the 56s drift max and the 12h foreign floor' while the code is 12 h + 300 s and the docs now say 12 h + 5 min (prior-round minor 3 was only half-closed).", "scenario": "The finalize amend reuses this body and ships a wrong constant and rationale into history. Safe path: rewrite the nvr.py bullet in the /task-commit digest before consuming .commit-approved."},
{"severity": "minor", "file": "scripts/validate_clock_gate_va24h.py", "line": 166, "issue": "--real mode builds the reader via default_timestamp_reader(), which localizes OCR readings with VA_NVR_TZ/system-local and ignores --tz, while _body_status and --proxy use --tz.", "scenario": "On a box whose system tz differs from the NVR's (VA_NVR_TZ unset), `--real --tz America/Los_Angeles` OCRs heads in the wrong zone but classifies bodies in LA, so the tally and the body-aligned split disagree (masked up to 12 h by the coarse tolerance; >12 h rejects every clip). Safe path: OcrClockReader(RapidOcrFrameOcr(), tz=tz) in run_real, or set VA_NVR_TZ from --tz before building the reader."}
]}
```
