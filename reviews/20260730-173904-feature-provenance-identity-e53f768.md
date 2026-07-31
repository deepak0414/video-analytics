# Agent review — approve

date: 2026-07-30T17:45:04.829436
range: origin/main..HEAD
branch: feature/provenance-identity
findings: 2

- **minor** `src/va/pipeline/ingest.py:106` — _record_provenance fingerprints roles from a fresh load_config() at the END of ingest, not the config the roles actually ran under, so a mid-ingest config edit stamps old-model rows with the new fingerprint.
  - scenario: During a minutes-long real-model ingest the user switches vlm_captioner in roles.yaml; captions were produced by the old model but the row is stamped with the new fingerprint, so `va stale` reports the video current — a missed stale, which the design forbids. Safe path: resolve one Config at _ingest_impl start and pass it to role_fingerprint (and optionally the registry getters).
- **minor** `COORDINATION.md:305` — The deep-scan observations cache-key change (captioner+reasoner fingerprints folded into prompt_key/map_key) invalidates all existing cached sweeps in shared workdirs but is not logged in COORDINATION.md — only the plan's D6 correction records it.
  - scenario: After merge, the web agent's `va serve` path hits `va ask` on .va-shots; every previously cached deep-scan question silently re-runs a multi-minute VLM sweep, which the other agent can misread as a hang or perf regression. Safe path: append a COORDINATION.md log line noting the intentional one-time cache invalidation.

---

## Full review

Review complete. I read all three commits (PROV-1/2/3: the `role_fingerprint` identity helper, the `role_provenance` table + v2 migration + store, and the ingest stamping plus deep-scan cache-key fold), traced them through `configuration.py`, `registry.py`, `ingest.py`, `deep_scan.py`/`ask.py`, and the schema/migration runner, and checked the prior review ledger. (I could not re-run pytest — the command was denied — but the most recent ledger review records the suite green at 482 passed / 2 skipped on this branch.)

## What I verified and found sound

- **Fingerprint correctness**: conservative-by-exclusion fails toward false-stale (safe direction, documented). Nested-key namespacing (`load.model` vs the role model id), sorted vocab with `DEFAULT_INGEST_*` folded in, and stable JSON hashing are all right; the 16-test suite covers checkpoint/threshold/vocab salience and device/dtype/credential exclusion. The `"unknown"` fallback for unconfigured roles is documented, and every shipped config (`config/`, `run-siglip/`, `run-claude/`) sets all roles.
- **Failure tracking in ingest**: every best-effort `except` adds the role and its dependents (diarizer with STT, tracker with detector) to `failed`; failed roles are omitted from the stamp so absent = stale — no best-effort role can abort ingest, and `_record_provenance` itself is wrapped and traced.
- **Migration hygiene**: v2 rides the existing per-migration `BEGIN IMMEDIATE` runner; indexes build after migrations; `va remove`/`reingest` purge the new table (tested). The `test_migrations.py` edit *generalizes* the rollback test rather than weakening it.
- **Prior review's finding is addressed**: the missing cache-invalidation test now exists (`test_deep_scan_cache_busts_on_captioner_upgrade`), and it correctly monkeypatches the module attribute that deep_scan's call-time import resolves. I chased two mismatch suspicions — the reasoner fingerprint in `map_key` vs the reasoner actually passed by `ask.py`, and a fallback-`RuleReasoner` path — and both dissolve (the configured reasoner is what's passed; the rule/identity path never writes a mapping, so no wrongly-keyed cache entry can be created).
- **Commit messages** are self-sufficient plain descriptions with plan IDs as trailing references; the HEAD `need_agent_review:` subject is exempt anyway.

## Findings (both minor)

1. **`src/va/pipeline/ingest.py:106` — fingerprints are computed from a *fresh* `load_config()` after all roles have run, not from the config the roles actually ran under.** Real-model ingests run for minutes; a user who edits `roles.yaml`/the profile mid-ingest (e.g. switches the captioner model) gets rows produced by the old model stamped with the *new* fingerprint — `va stale` then reports the video current. That is a missed stale, the one failure the design declares forbidden. Narrow window, hence minor. Safe path: resolve one `Config` at the top of `_ingest_impl`, pass it to `role_fingerprint(role, cfg)` (the registry getters already accept `cfg` if you want to close the window fully).

2. **`src/va/pipeline/deep_scan.py:281` / `COORDINATION.md:305` — the observations-cache key format change invalidates every existing cached deep-scan sweep and normalization map, and this shared-surface behavior change is not logged in COORDINATION.md** (only the plan's D6 correction records it; the two new COORDINATION entries cover schema v2 and stamping). The first `va ask` per cached question on `.va-shots` after this merge silently re-runs a multi-minute VLM sweep — including through `va serve`, the other agent's layer, where it can be misread as a hang or perf regression. Safe path: append one COORDINATION log line noting the intentional one-time cache invalidation.

No critical or major findings, so the verdict is **approve**.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/ingest.py", "line": 106, "issue": "_record_provenance fingerprints roles from a fresh load_config() at the END of ingest, not the config the roles actually ran under, so a mid-ingest config edit stamps old-model rows with the new fingerprint.", "scenario": "During a minutes-long real-model ingest the user switches vlm_captioner in roles.yaml; captions were produced by the old model but the row is stamped with the new fingerprint, so `va stale` reports the video current — a missed stale, which the design forbids. Safe path: resolve one Config at _ingest_impl start and pass it to role_fingerprint (and optionally the registry getters)."}, {"severity": "minor", "file": "COORDINATION.md", "line": 305, "issue": "The deep-scan observations cache-key change (captioner+reasoner fingerprints folded into prompt_key/map_key) invalidates all existing cached sweeps in shared workdirs but is not logged in COORDINATION.md — only the plan's D6 correction records it.", "scenario": "After merge, the web agent's `va serve` path hits `va ask` on .va-shots; every previously cached deep-scan question silently re-runs a multi-minute VLM sweep, which the other agent can misread as a hang or perf regression. Safe path: append a COORDINATION.md log line noting the intentional one-time cache invalidation."}]}
```
