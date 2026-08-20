# Agent review — approve

date: 2026-08-19T19:24:51.216193
range: origin/main..HEAD
branch: fix/nvr-delivery-verification
findings: 2

- **minor** `src/va/sources/nvr.py:490` — The _pull_window docstring still claims 'fetch() trusts an existing cache file', contradicting this same commit's cache-hit verification in fetch().
  - scenario: A later session reads the docstring as the cache contract and either re-adds a redundant guard or removes the cache-hit _verify_and_trim call as inconsistent, silently re-opening the pre-gate-cache bypass the commit closed.
- **minor** `src/va/sources/nvr.py:266` — A cache-hit DeliveryRejected leaves the bad file at the path fetch() checks first and never falls back to a re-pull, permanently failing the window with no remedy named in the error.
  - scenario: va reingest of a window whose pre-fix sub-stream clip sits in cache/ (VA_NVR_MAIN_STREAM set) purges the row's data, then fetch() rejects the cached clip on every retry even while clean footage is still on the ~6-day ring; the operator must discover and delete the cache file by hand to recover.

---

## Full review

All four findings from the prior review round (f8862eb) are addressed in this commit: the overclaim was resolved by narrowing the CLAUDE.md/COORDINATION/docstring claims exactly along the honest-coverage path the reviewer offered; the env-pollution tests got autouse `delenv` fixtures in both files; `fetch()` now re-verifies cache hits (with tests); and the `TimestampReader` wiring is exercised through `NvrRecordedSource` by two stub-reader tests. I verified the round-1 fixes rather than re-litigating them.

I also traced and dismissed several fresh suspicions: the trim/recheck `start_epoch` shift is consistent under both head-replacement and head-prepend models (skew ≤ trim < 5 s tolerance); `_head_trim`'s all-foreign → reject and min-kept → reject branches are correct and tested; the trim math in `test_verify_and_trim_removes_a_foreign_head` checks out frame-by-frame (5 foreign frames at 10 fps → trim 0.5 s → 5.0 s kept); `DeliveryRejected` escaping `fetch()` is a `RuntimeError` subclass caught by `_ingest_impl`'s status-`failed` handler, so the COORDINATION contract claim holds; reingest verification needs no NVR credentials (`_verify_and_trim` never calls `_conn()`); `base.resolve_source()` constructs `NvrRecordedSource()` zero-arg so the new optional ctor params break nothing; the `_det_harness` verify stub is a justified scope split with the pull-loop × verification integration separately covered by `test_pull_fails_closed_when_delivery_cannot_be_verified`; and the thresholds (`IDENTITY_MAX_DHASH=20`, `CLOCK_TOL_S=5.0`) are explicitly flagged and census-calibrated, with a test pinning the band, satisfying the hardcoded-content rule. A live pytest from another session prevented me from running the suite (repo lesson: never stack runs); I verified the new tests by tracing their arithmetic instead.

Two minor findings survived:

**1. minor — `src/va/sources/nvr.py:490` — stale docstring contradicts the new cache-hit behavior.** The `_pull_window` docstring still says "fetch() trusts an existing cache file, so a clip killed mid-write must never be reusable under the final name," but this same commit made `fetch()` verify cache hits. A next session reading this docstring (the atomic-rename rationale) will conclude cache files bypass the gate and may re-add a redundant guard or, worse, "simplify" the cache-hit verification away as inconsistent with the documented contract. Safe path: update the clause to "fetch() re-verifies an existing cache file, but verification is not a torn-file check — the atomic rename still must hold."

**2. minor — `src/va/sources/nvr.py:266` — a rejected cache file dead-ends the window with no re-pull and no remedy in the error.** On a cache hit, a `DeliveryRejected` verdict (e.g. one of the four pre-fix 352x240 sub-stream clips, with `VA_NVR_MAIN_STREAM` set) propagates out while the bad file stays at the exact path `fetch()` checks first — so every retry re-fails identically, even when the window is still on the NVR ring and a fresh pull (which now verifies) would succeed. On a `va reingest` this lands after `remove_video` has already purged the row's role data, leaving status `failed` and a poisoned cache file the operator must find and delete by hand; the exception text names the mismatch but not the remedy. Safe path: on cache-hit rejection, rename the bad file aside (e.g. `.rejected.mp4`) and fall through to `_pull_window` — which already fails closed if the window is genuinely gone — or at minimum append "delete <path> to force a fresh pull" to the raised error.

Neither is major: the shipped gate's decision logic, fail-closed integration, honest coverage disclosure, tests, and documentation are all sound.

```json
{"verdict": "approve", "findings": [
  {"severity": "minor", "file": "src/va/sources/nvr.py", "line": 490, "issue": "The _pull_window docstring still claims 'fetch() trusts an existing cache file', contradicting this same commit's cache-hit verification in fetch().", "scenario": "A later session reads the docstring as the cache contract and either re-adds a redundant guard or removes the cache-hit _verify_and_trim call as inconsistent, silently re-opening the pre-gate-cache bypass the commit closed."},
  {"severity": "minor", "file": "src/va/sources/nvr.py", "line": 266, "issue": "A cache-hit DeliveryRejected leaves the bad file at the path fetch() checks first and never falls back to a re-pull, permanently failing the window with no remedy named in the error.", "scenario": "va reingest of a window whose pre-fix sub-stream clip sits in cache/ (VA_NVR_MAIN_STREAM set) purges the row's data, then fetch() rejects the cached clip on every retry even while clean footage is still on the ~6-day ring; the operator must discover and delete the cache file by hand to recover."}
]}
```
