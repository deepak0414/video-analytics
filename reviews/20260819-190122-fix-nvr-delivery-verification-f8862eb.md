# Agent review — request_changes

date: 2026-08-19T19:09:56.481392
range: origin/main..HEAD
branch: fix/nvr-delivery-verification
findings: 4

- **major** `src/va/sources/nvr.py:200` — The census's #1 mandatory safeguard (burned-in-clock gate) ships inactive with no TimestampReader implementation, and the default defences provably pass the observed same-camera wrong-week contamination (21/92 census heads at dHash <= 18, below the 20 threshold; 25 clips 'right camera, wrong week'), while CLAUDE.md/COORDINATION claim 'fail closed, never a silently contaminated clip'.
  - scenario: va watch re-pulls after merge; the seek prepends a same-camera 7-day-stale head; head-vs-body dHash reads <= 18 so the verifier accepts, VA_NVR_MAIN_STREAM checks resolution only, no clock reader exists; the wrong-week frame ingests as sample idx 0 and feeds phantom ReID matches — the exact census failure — under documentation asserting it cannot happen.
- **minor** `tests/test_nvr_contamination.py:177` — test_verify_and_trim_removes_a_foreign_head and test_verify_and_trim_accepts_a_clean_clip_unchanged read ambient VA_NVR_MAIN_STREAM via _expected_profile without clearing it, so the documented operator setup turns them red.
  - scenario: Operator exports VA_NVR_MAIN_STREAM=2688x1520@20 per CLAUDE.md for real pulls; the 64x64 synth clips are rejected as wrong-stream, both tests raise DeliveryRejected, the offline suite goes red, and the Stop gate / push are blocked (the repo's known env-pollution failure mode).
- **minor** `src/va/sources/nvr.py:242` — fetch() trusts any existing cache/<source_key>.mp4 and skips the pull, so clips pulled by pre-fix code bypass the new delivery-verification gate on re-ingest.
  - scenario: va remove + re-ingest of a window whose pre-fix contaminated mp4 survives in the workdir cache lands the foreign head unverified on the very machine holding .va-24h-era caches.
- **minor** `src/va/sources/nvr.py:366` — No test injects a TimestampReader through NvrRecordedSource, so the clock-signal extraction call and the recheck's shifted start_epoch flow are untested wiring.
  - scenario: A future real OCR reader is plugged into a mis-wired seam (wrong arg order, recheck epoch not shifted) and the gate silently never trims, with all existing tests green.

---

## Full review

A live pytest belongs to another session — per the repo lesson I won't stack runs, and my findings are verifiable directly from the code and census data, so no test run is needed. My review is complete.

# Review: `f8862eb` — NVR delivery verification (origin/main..HEAD)

**Scope reviewed:** one commit — `CLAUDE.md`, `COORDINATION.md`, `src/va/sources/verify.py` (new), `src/va/sources/nvr.py`, `src/va/media/frames.py`, `src/va/media/synth.py`, `tests/test_nvr_contamination.py` (new), `tests/test_nvr_source.py`.

The core design is sound: a pure, injectable verifier; true-first-frame extraction that closes the measured `-vf fps` sampler blindness; fail-closed integration in `_pull_window`; regression tests that genuinely reproduce the old duration-gate blindness; COORDINATION logged with ⚠; `VA_NVR_MAIN_STREAM` documented in the same change. I verified and dismissed several suspicions (the `_trim_encode` offset math, the trim/recheck `start_epoch` shift, the `frames_at` empty-body "fail-open" — `frames_at` raises rather than returning empty, the `_det_harness` pass-through stub — legitimate, since the gate has its own real-frame tests). Four findings survived.

## Findings

**1. MAJOR — plan conformance / overclaim: the census's #1 mandatory safeguard is not operational, and the shipped default gate provably passes an observed contamination class** (`src/va/sources/nvr.py:200`; claim at CLAUDE.md "fail closed, never a silently contaminated clip", COORDINATION "Fixes the `.va-24h` contamination").
`va-24h-data-integrity-investigation.md` §5 lists the burned-in-clock gate as mandatory item 1 — "the single highest-value control", "must be a gate on the pull, not a post-hoc audit" — before any re-pull. This change ships it as a seam with `timestamp_reader=None` and **no reader implementation anywhere**, so in every real configuration the clock gate never runs. That matters because the census's own numbers show the two defences that do run cannot cover the observed failure: of the 92 measured head lead-ins, **21 were "same view, different day" at dHash ≤ 18** — below the shipped `IDENTITY_MAX_DHASH = 20` **by construction** — and "a further 25 clips contain footage from the right camera but the wrong week". The head-vs-own-body check accepts those heads; the stream-identity check is inactive unless `VA_NVR_MAIN_STREAM` is set; nothing else looks. *Failure scenario:* the watcher re-pulls after this merge; a seek prepends a same-camera 7-day-stale head (≈23 % of observed heads); the pull verifies "clean", the clip ingests, and the wrong-week frame lands as sample idx 0 in `vectors.npz` — exactly the phantom-ReID input the census was written to stop — while CLAUDE.md now asserts "never a silently contaminated clip". The gap is disclosed as backlog in-code, but the plan marks it mandatory and the summary claims contradict the disclosure. *Safe path:* implement a default `TimestampReader` over the already-proven RapidOCR overlay parse (the census parsed 8 337 readings with it — the doc calls it "nearly free"), or narrow the CLAUDE.md/COORDINATION/final-commit claims to what actually runs ("cross-camera heads trimmed; sub-streams rejected when `VA_NVR_MAIN_STREAM` is set; same-camera wrong-week footage NOT yet caught") and record in the investigation doc that re-pulls stay gated on item 1.

**2. MINOR — new accept-path tests break when the operator's shell exports `VA_NVR_MAIN_STREAM`** (`tests/test_nvr_contamination.py:177` and `:206`).
`test_verify_and_trim_removes_a_foreign_head` and `test_verify_and_trim_accepts_a_clean_clip_unchanged` reach `_expected_profile`, which reads ambient `VA_NVR_MAIN_STREAM`. CLAUDE.md now tells the operator of this very box to export it (`"2688x1520@20"`); with it set, the 64×64 synth clips are rejected as wrong-stream and both tests error with `DeliveryRejected` — a red offline suite that blocks the Stop gate and push, this repo's documented env-pollution failure mode (the file already pins `VA_NVR_TZ`-style env elsewhere; these two missed it). *Safe path:* `monkeypatch.delenv("VA_NVR_MAIN_STREAM", raising=False)` in both (or an autouse fixture for the file).

**3. MINOR — pre-fix contaminated clips in `cache/` bypass the new gate on re-ingest** (`src/va/sources/nvr.py:242`).
`fetch()` still trusts any existing `cache/<source_key>.mp4` and skips `_pull_window` entirely, so a clip pulled by the pre-fix code and left in a workdir cache re-ingests unverified — on the one machine known to hold such artifacts. *Failure scenario:* `va remove` + re-ingest of a window whose contaminated mp4 survives in `cache/` lands the foreign head with the new code "in effect". *Safe path:* run `_verify_and_trim` (or at least the head check) on a cache hit once, or version the cache filename so pre-gate files are ignored; alternatively document "clear `cache/` after upgrading" in the investigation doc's repair steps.

**4. MINOR — the `timestamp_reader` wiring in `NvrRecordedSource` has zero coverage** (`src/va/sources/nvr.py:366`).
The pure clock gate is well tested, but no test injects a fake `TimestampReader` through `NvrRecordedSource(timestamp_reader=...)`, so the extraction call (`read_head_clock(str(cut), HEAD_FRAMES)`) and the recheck's shifted `start_epoch` flow are untested — the advertised seam could be wired wrong and nothing would notice until a real reader ships. *Safe path:* one test with a stub reader returning a stale-then-aligned sequence, asserting the trim lands through the full `_verify_and_trim` path.

## Verdict

`request_changes` — finding 1 is major: either close the clock-gate gap or bring the claims down to what the shipped defences actually cover before finalize.

```json
{"verdict": "request_changes", "findings": [
  {"severity": "major", "file": "src/va/sources/nvr.py", "line": 200, "issue": "The census's #1 mandatory safeguard (burned-in-clock gate) ships inactive with no TimestampReader implementation, and the default defences provably pass the observed same-camera wrong-week contamination (21/92 census heads at dHash <= 18, below the 20 threshold; 25 clips 'right camera, wrong week'), while CLAUDE.md/COORDINATION claim 'fail closed, never a silently contaminated clip'.", "scenario": "va watch re-pulls after merge; the seek prepends a same-camera 7-day-stale head; head-vs-body dHash reads <= 18 so the verifier accepts, VA_NVR_MAIN_STREAM checks resolution only, no clock reader exists; the wrong-week frame ingests as sample idx 0 and feeds phantom ReID matches — the exact census failure — under documentation asserting it cannot happen."},
  {"severity": "minor", "file": "tests/test_nvr_contamination.py", "line": 177, "issue": "test_verify_and_trim_removes_a_foreign_head and test_verify_and_trim_accepts_a_clean_clip_unchanged read ambient VA_NVR_MAIN_STREAM via _expected_profile without clearing it, so the documented operator setup turns them red.", "scenario": "Operator exports VA_NVR_MAIN_STREAM=2688x1520@20 per CLAUDE.md for real pulls; the 64x64 synth clips are rejected as wrong-stream, both tests raise DeliveryRejected, the offline suite goes red, and the Stop gate / push are blocked (the repo's known env-pollution failure mode)."},
  {"severity": "minor", "file": "src/va/sources/nvr.py", "line": 242, "issue": "fetch() trusts any existing cache/<source_key>.mp4 and skips the pull, so clips pulled by pre-fix code bypass the new delivery-verification gate on re-ingest.", "scenario": "va remove + re-ingest of a window whose pre-fix contaminated mp4 survives in the workdir cache lands the foreign head unverified on the very machine holding .va-24h-era caches."},
  {"severity": "minor", "file": "src/va/sources/nvr.py", "line": 366, "issue": "No test injects a TimestampReader through NvrRecordedSource, so the clock-signal extraction call and the recheck's shifted start_epoch flow are untested wiring.", "scenario": "A future real OCR reader is plugged into a mis-wired seam (wrong arg order, recheck epoch not shifted) and the gate silently never trims, with all existing tests green."}
]}
```
