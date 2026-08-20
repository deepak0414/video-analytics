# Agent review — approve

date: 2026-08-19T20:04:22.094684
range: origin/main..HEAD
branch: fix/nvr-delivery-verification
findings: 0


---

## Full review

My independent review is complete. All five prior review rounds' findings are fixed at this HEAD, and my own pass surfaced no new defects that survived verification.

# Review: `77ac16b` — NVR delivery verification (origin/main..HEAD, round 6)

**Scope:** one commit — new `src/va/sources/verify.py` (pure verifier + injectable seam), reworked `src/va/sources/nvr.py` (gate wiring, cache re-verification, `_parse_main_stream`), `va.media.frames.first_frames`, `va.media.synth.write_frames_video`, new `tests/test_nvr_contamination.py`, extended `tests/test_nvr_source.py`, CLAUDE.md + COORDINATION.md. A pytest run from another session is live (PID 1275631), so per the repo lesson I did not launch one; I verified test arithmetic and wiring by tracing every case.

## Prior findings — all verified resolved at this HEAD

- **Round 3 (pooled resolutions × fps):** `_parse_main_stream` now returns a frozenset of paired `(w, h, fps_or_None)` tuples matched atomically in `verify_delivery`; the exact cross-match scenario is pinned by `test_stream_identity_matches_resolution_and_fps_as_a_pair`.
- **Round 4/5b7 minor 1 (non-blank spec parsing to zero profiles fails open):** fixed — a non-blank-but-empty spec now raises `ValueError` with an explicit comment explaining why silent `None` would deactivate the gate; pinned by the `","` case in `test_parse_main_stream_keeps_pairs_and_fails_closed_on_garbage`.
- **Round 4/5b7 minor 2 (parser untested):** that same test now covers the multi-entry comma form, fps-omitted form, unset/blank → `None`, and four garbage forms.
- **Round 5/829 minor (cache-hit TRIM branch untested):** `test_fetch_trims_a_foreign_head_in_an_existing_cache_file` now drives `fetch()` over a fabricated foreign-head cache file, asserts the trimmed clip lands back at the cache path, the head is clean, the duration shortened, and — via a dead-man `_pull_window` — that no re-pull happens.
- Rounds 1–2 (clock-gate overclaim narrowed; cache re-verify + set-aside recovery) remain in place.

## Suspicions traced and dismissed this round

- **`os.replace` across filesystems:** the pull's work dir is `mkdtemp(dir=out_mp4.parent, ...)` (nvr.py:519), and the cache-hit trim writes `<stem>.verified.mp4` into the cache dir itself — every replace stays on one filesystem; no EXDEV risk.
- **Trim bounds:** `_trim_encode(cut, trim, window_len, trimmed)` matches the `[a, b)` cut signature; the cut's timeline is `[0, window_len]`, and `-to` past a slightly-short ring-edge clip just stops at EOF. All synthetic-test arithmetic (0.5 s head at 10 fps → 5 foreign frames → trim at t=0.5 → 5.0/5.5 s kept) checks out.
- **Cache set-aside dead-end:** if the re-pull after a set-aside fails, `out` doesn't exist and the aside remains, so the next fetch takes the fresh-pull path — the window is never dead-ended, matching the stated recovery contract.
- **Recheck loop safety:** a recheck verdict of anything but `accept` raises — no trim loop; a foreign head longer than the 8 inspected frames rejects (fail closed) rather than under-trimming, with margin over the census's 1–5-frame heads.
- **Constructor compatibility:** `base.resolve_source` still calls `NvrRecordedSource()` — both new `__init__` params default, so the dispatcher, watch path, and reingest path are unbroken.
- **fps-metadata fallback in `first_frames`** (default 30.0 when meta lacks fps): a wrong fps could mis-scale head timestamps, but an under-trim fails closed at the recheck, and every clip reaching the gate is a libx264 re-encode that carries fps metadata — not reportable.
- **Docs/contract parity:** `VA_NVR_MAIN_STREAM` (comma form included), `first_frames`, `write_frames_video`, the cache re-verify note, and the honest partial-coverage disclosure (clock gate as a seam with no default reader; "do not treat re-pulls as fully safe") appear consistently in CLAUDE.md, COORDINATION.md (⚠ pull-contract entry), and the module docstring. The identity threshold (20, between measured ≤18 / ≥24 bands) is flagged, census-calibrated, and pinned by a test. Commit subject is a provisional `need_agent_review:` — exempt from the plain-description rule.
- **Test integrity:** no tests deleted or weakened; the `_det_harness` pass-through stub of `_verify_and_trim` is a justified scope split (its 4 KB fake cuts can't decode), with the pull-loop × verification integration separately covered by `test_pull_fails_closed_when_delivery_cannot_be_verified`.

## Findings

None. Five rounds of review have converged; every previously offered safe path was taken, and my fresh pass found nothing new that survives verification.

## Verdict

**approve** — no findings. The pure-verifier seam, fail-closed pull integration, paired stream-profile matching with fail-loud parsing, cache re-verification with set-aside recovery and in-place trim (all three cache outcomes now tested), honest coverage disclosure, and regression tests that reproduce the original sampler blindness are sound.

```json
{"verdict": "approve", "findings": []}
```
