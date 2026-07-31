# Agent review — approve

date: 2026-07-30T18:37:58.688083
range: origin/main..HEAD
branch: feature/provenance-identity
findings: 1

- **minor** `src/va/pipeline/deep_scan.py:311` — The reasoner-fingerprint fold into the normalization-mapping cache key (map_key) has zero test coverage — the companion captioner-fold test guards only prompt_key, and the stub rule reasoner never produces a cacheable mapping, so the fold is exercised only in untested real-reasoner configs.
  - scenario: A refactor rebuilds map_key and drops the role_fingerprint('reasoner') term; the offline suite stays green. Later the reasoner is switched (e.g. qwen3-vl to claude-code) on the shared .va-shots workdir: deep-scan re-captions with the new captioner but silently reuses the OLD reasoner's cached label mapping for normalization — stale code-counted answers, the missed-stale class this commit was written to eliminate, invisible to va stale.

---

## Full review

Review complete. I read the full range (4 commits: PROV-1 fingerprint helper → PROV-2 table/migration/store → PROV-3 ingest stamping → PROV-4 `va stale`), verified the supporting code each new module leans on, and ran the offline suite: **487 passed, 2 skipped**.

**Suspicions I chased that dissolved on verification:**

- **Failed-role bookkeeping** (`ingest.py`): every one of the 10 `PROVENANCE_ROLES` is either covered by a `failed.add/update` handler or is a critical role (scene detector, visual embedder) whose failure aborts the ingest — in which case the video never reaches `done` and `stale_report` skips it. The counts dict keys match `PROVENANCE_ROLES` exactly, and all count variables are initialized before the stamp on every path.
- **Pinned-config race claim**: the registry getters really do self-`load_config()`, so the comment's "mid-ingest edit degrades to false stale, never missed stale" argument holds in both edit orderings. Note the actual ingest calls `get_ingest_actions()`/`get_ingest_classes()` *without* the pinned cfg while the fingerprint uses the pinned one — but that divergence is also only false-stale-safe.
- **Fingerprint semantics**: `Config.role()` folds profile globals + `models.<name>` into `load`, so the device/dtype/residency exclusions and the whisper-checkpoint (`load.model`, correctly namespaced so it can't clobber the role model id) tests match real behavior. All shipped profile-global keys are in `_NON_OUTPUT_KEYS`, so no spurious corpus-wide stale under the real configs. Role names in `PROVENANCE_ROLES` match `roles.yaml` exactly; query-time-only roles (reranker, vlm_verifier, reasoner) are correctly excluded per the plan's D6, with the reasoner's one persisted output handled via the deep-scan cache keys.
- **Migration**: v2 runner path is idempotent for fresh, v1, and newer-than-known DBs; the `test_migrations.py` edit generalizes the rollback test to the new migration count without weakening any assertion; `va remove` purges the new table with a test; FK enforcement is off, so the store tests' free-standing video_ids are valid.
- **Deep-scan test validity**: `get_vlm_captioner` and `role_fingerprint` are imported inside `deep_scan_video`, so the monkeypatched tests exercise the production path.
- **Prior review's minor** (fps footer referencing a note that pre-provenance videos never print): already fixed — `_cmd_stale` now branches on `unknown_fps` and explains the missing annotation.
- **Docs/contract/commit hygiene**: `va stale` in CLAUDE.md, schema bump + stamping + cache invalidation + read helper all logged in COORDINATION.md, PROV-4 scope cut (`va provenance <video>` deferred) recorded in the plan rather than silent, finalized commit subjects self-sufficient, HEAD is a provisional `need_agent_review:` (exempt).

**One finding survived** — a coverage gap on the second half of the cache-key fix:

**Minor — `src/va/pipeline/deep_scan.py:311` (test integrity / combination coverage).** The change folds *two* fingerprints into the observation caches: the captioner into `prompt_key` (guarded by `test_deep_scan_cache_busts_on_captioner_upgrade`) and the reasoner into `map_key` — which has no invalidation test at all. The `map_key` cache is only ever written when normalization yields a non-empty mapping (line 320), which the stub `rule` reasoner never produces, so the entire reasoner-fold path is exercised only in the real-reasoner combinations (`run-claude` / `run-qwen3vl`) that no offline test touches. Scenario: a later refactor rebuilds `map_key` and drops the `role_fingerprint('reasoner')` term; the whole offline suite stays green, and after the queued reasoner switch (qwen ↔ claude-code) `va ask` re-sweeps micro-captions with the new captioner but silently reuses the *old* reasoner's cached label mapping — the exact missed-stale class this commit exists to close. Safe path: add a sibling test that monkeypatches `normalize_observations` (or the reasoner) to return a non-empty mapping, asserts a cache hit on a stable key, then monkeypatches `role_fingerprint` for `"reasoner"` and asserts normalization re-runs.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/deep_scan.py", "line": 311, "issue": "The reasoner-fingerprint fold into the normalization-mapping cache key (map_key) has zero test coverage — the companion captioner-fold test guards only prompt_key, and the stub rule reasoner never produces a cacheable mapping, so the fold is exercised only in untested real-reasoner configs.", "scenario": "A refactor rebuilds map_key and drops the role_fingerprint('reasoner') term; the offline suite stays green. Later the reasoner is switched (e.g. qwen3-vl to claude-code) on the shared .va-shots workdir: deep-scan re-captions with the new captioner but silently reuses the OLD reasoner's cached label mapping for normalization — stale code-counted answers, the missed-stale class this commit was written to eliminate, invisible to va stale."}]}
```
