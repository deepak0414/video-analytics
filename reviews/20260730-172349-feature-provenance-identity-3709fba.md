# Agent review — approve

date: 2026-07-30T17:29:33.051433
range: origin/main..HEAD
branch: feature/provenance-identity
findings: 1

- **minor** `src/va/pipeline/deep_scan.py:279` — The commit's headline missed-stale fix — folding captioner/reasoner fingerprints into the observations cache keys so a model upgrade re-runs the sweep — has no test asserting invalidation; only the stable-key cache-hit path is covered (tests/test_deep_scan.py::test_deep_scan_video_with_cache).
  - scenario: A later refactor hoists or simplifies the prompt_key/map_key composition and drops the fingerprint component; the suite stays green, and after the next vlm_captioner upgrade `va ask` silently serves code-counted answers from old-model captions — the exact defect this commit fixed, invisible to `va stale`. Safe path: add a test that changes the captioner/reasoner config between two deep_scan_video calls and asserts the second run is not cached.

---

## Full review

Review complete. The suite is green (482 passed, 2 skipped — note this includes the uncommitted PROV-4 work in the tree, but nothing in the range depends on it).

## What I reviewed

Three commits implementing PROV-1/2/3: `role_fingerprint` (output-only identity per role), the `role_provenance` table + schema v2 migration + `ProvenanceStore`, and ingest stamping plus a deep-scan cache-key fix.

## What I verified and found sound

- **Fingerprint design**: `Config.role()` folds profile-global + per-model keys into `load`; the conservative-by-exclusion `_NON_OUTPUT_KEYS` filter fails toward false-stale (safe direction), with 16 tests covering checkpoint/vocab/threshold salience and device/dtype/credential exclusion, including the default-vocab fold.
- **Ingest failure tracking**: every best-effort `except` adds the role (and dependents — diarizer with STT, tracker with detector) to `failed`; a partially-captioned failure correctly reads as stale rather than current. The stamp itself is wrapped so it can never abort ingest — no best-effort contract break.
- **Migration**: v2 follows the existing runner (per-migration `BEGIN IMMEDIATE`, indexes built after migrations); the rollback test was *generalized*, not weakened — it still asserts the real migrations stick and the injected failure rolls back. `va remove`/`reingest` purge the new table (tested), so no ghost rows.
- **deep_scan key fold**: the fingerprinted roles match the models actually used (`get_vlm_captioner()` for the sweep; `ask.py` always passes the *configured* reasoner, so the `map_key` reasoner fingerprint agrees with the reasoner that produced the mapping). I chased both as potential mismatches; both dissolve.
- **COORDINATION.md** logs the schema change and the stamping behavior; commit messages are plain descriptions with IDs as trailing references; no new env vars/flags needing docs in this range.

## One finding

**Minor — untested cache-invalidation contract in deep_scan.** The PROV-3 commit's stated fix ("a vlm_captioner/reasoner upgrade busts the observations cache") has no test asserting it: `tests/test_deep_scan.py::test_deep_scan_video_with_cache` covers the cache-*hit* path under a stable config, but nothing asserts that a changed captioner or reasoner fingerprint produces a fresh sweep/normalization. Per the repo's own lesson (a fix without a test that fails on the old code is decoration), a later refactor — say, hoisting the `role_fingerprint` calls or simplifying the key format — could silently reintroduce the exact missed-stale this commit closes, invisible to `va stale` by design. Safe path: add a test that monkeypatches the captioner config (or `role_fingerprint`) between two `deep_scan_video` calls and asserts `cached is False` + new VLM calls on the second.

No critical or major findings, so: **approve**.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/deep_scan.py", "line": 279, "issue": "The commit's headline missed-stale fix — folding captioner/reasoner fingerprints into the observations cache keys so a model upgrade re-runs the sweep — has no test asserting invalidation; only the stable-key cache-hit path is covered (tests/test_deep_scan.py::test_deep_scan_video_with_cache).", "scenario": "A later refactor hoists or simplifies the prompt_key/map_key composition and drops the fingerprint component; the suite stays green, and after the next vlm_captioner upgrade `va ask` silently serves code-counted answers from old-model captions — the exact defect this commit fixed, invisible to `va stale`. Safe path: add a test that changes the captioner/reasoner config between two deep_scan_video calls and asserts the second run is not cached."}]}
```
