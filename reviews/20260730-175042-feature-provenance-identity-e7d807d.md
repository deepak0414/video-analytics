# Agent review — approve

date: 2026-07-30T17:56:32.888215
range: origin/main..HEAD
branch: feature/provenance-identity
findings: 0


---

## Full review

Review complete. I read the full range (`8097417`, `d3abaf0`, `e7d807d` — PROV-1/2/3), verified the implementation against `ingest.py`, `deep_scan.py`, `configuration.py`, `registry.py`, `schema.py`, the config dirs, and the covering plan, and ran the offline suite (485 passed, 2 skipped — green, though note the working tree also contains uncommitted PROV-4 files, so that count includes them).

**Verdict: approve — no findings survived verification.** This is unusually defensible work; every suspicion I chased dissolved on reading further:

- **Fingerprint semantics** (`provenance.py`): conservative-by-exclusion is the right failure direction (false stale, never missed stale), and the tests pin both sides (salient: checkpoint-in-`load.model`, conf threshold, role-level knobs, vocab-with-defaults-folded; non-salient: device/dtype/residency/credentials). `text_embedder` and every stamped role are configured in all four config dirs, so the `"unknown"` degradation has the low exposure the docstring claims.
- **Ingest stamping**: every best-effort `except` block adds the role to `failed`; roles whose failure aborts ingest (scene detector, visual embedder) correctly produce *no* stamp at all because `_record_provenance` is only reached on success. All `counts` variables are initialized before their try blocks, so no path references an unbound name. The config-pinned-at-role-launch race reasoning is correct (an edit can only make the stamp look older than the run — a safe false stale) and is guarded by a sentinel-object test.
- **Edge I checked and dismissed**: a video with no speech lines skips the diarizer yet stamps `speaker_diarizer` as current — behaviorally harmless (there was nothing to diarize; a later model upgrade still flips it stale via the fingerprint).
- **Deep-scan cache keys**: both `role_fingerprint` call sites are inside `deep_scan_video` with call-time imports, so the monkeypatch-based invalidation test genuinely exercises the mechanism, and the suite confirms the wrap-counting test's assumptions (fresh adapter per `get_vlm_captioner` call). The mapping cached under the *configured* reasoner's fingerprint while `normalize_observations` may fall back to the rule reasoner is a pre-existing property of the old `:norm2` key — this change strictly narrows it, so I don't report it. Orphaned old-key `observations` rows accumulating across upgrades is real but already recorded in the plan's D6 correction as B-phase work.
- **Migration**: v2 follows the runner's recipe exactly (base DDL + idempotent migration + version bump + assert), old-DB path covered by a dedicated test, and the `test_migrations.py` edit is a future-proofing generalization, not a weakening. `manage.py` purge and COORDINATION.md logging (schema change, stamping, one-time cache invalidation for the web agent) are all in place.

One non-finding reminder for the eventual PR: this range touches `human-reviewed` paths (`schema.py`, `ingest.py`) and `golden-verified` paths (`src/va/pipeline/`), so CI will require both labels — and the first golden-ask run on `.va-shots` after merge will re-run the multi-minute sweep due to the intentional cache invalidation (already documented in COORDINATION.md).

```json
{"verdict": "approve", "findings": []}
```
