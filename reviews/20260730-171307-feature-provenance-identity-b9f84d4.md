# Agent review — request_changes

date: 2026-07-30T17:19:11.296219
range: origin/main..HEAD
branch: feature/provenance-identity
findings: 1

- **major** `src/va/provenance.py:46` — PROVENANCE_ROLES excludes Role 11 on the false claim it 'stores no rows' — deep-scan persists VLM micro-captions in the observations table under a cache key with no model identity, so upgraded-model staleness there is invisible to both the fingerprint layer and the planned va stale.
  - scenario: vlm_captioner is upgraded (e.g. qwen2.5-vl-7b -> qwen3-vl-30b); the plan-B selective reprocess re-runs captions and restamps provenance current, but deep_scan.py:281 still treats the old-model observations rows as a valid cache — va ask serves CODE-COUNTED answers computed from the old model forever, a missed stale the module's own design rule forbids. Fix: include the vlm_captioner fingerprint in deep_scan's prompt_key (busting the cache on upgrade), correct the comment, and record the decision in provenance-reprocess-plan.md.

---

## Full review

Review complete. I read the full commit range (PROV-1/2/3: fingerprint helper, `role_provenance` table + v2 migration + store, ingest stamping), verified the role names against all four config dirs, the migration runner, `manage.py` purge/reingest paths, and ran the offline suite (482 passed, 2 skipped — green). Most of my suspicions dissolved on inspection: role keys in `PROVENANCE_ROLES` match every shipped `roles.yaml`; `reingest` removes before re-ingesting so no ghost provenance rows; the failed-role omission is correctly conservative on every error path I traced (including the mid-batch detector failure and the transcript-store failure after successful diarization); the `test_migrations.py` edit generalizes the rollback test without weakening its assertions; and the "unknown model" fallback is an acknowledged, low-exposure limitation since all shipped configs set every role.

One finding survived verification:

**Major — the Role-11 exclusion from provenance rests on a false premise, leaving a missed-stale hole the feature's own rule forbids.** `src/va/provenance.py:46` justifies excluding the reasoner with "on-demand and stores no rows", but deep-scan (part of `va ask`) *does* store per-video VLM output: `deep_scan.py:275` writes micro-captions into the `observations` table — the very table this diff's own `manage.py` comment acknowledges purging. Its cache key is `sha1(prompt_version | intent | max_frames | fps)` with **no captioner model identity**, and `deep_scan.py:281` treats "any rows = valid cache". So after a `vlm_captioner` upgrade, cached observations from the old model keep feeding CODE-COUNTED answers, and neither the fingerprint layer nor `va stale` (PROV-4) can ever see them. Today a full `va reingest` happens to purge them (via `_ROLE_TABLES`), but the plan's selective reprocess (B) — which re-runs only the stale role's tables — would restamp the video current while the stale sweep survives: a *missed stale*, the failure mode this module's docstring declares forbidden, designed in on a wrong fact. Safe path (cheap): fold the `vlm_captioner` fingerprint into deep-scan's `prompt_key` so an upgrade busts the cache naturally, correct the `PROVENANCE_ROLES` comment, and record the decision in `provenance-reprocess-plan.md` so PROV-4/B don't inherit the blind spot.

Not reported after verification: per-role `load_config()` re-reads in `_record_provenance` (efficiency only), the diarizer being stamped when zero transcript lines short-circuit it (semantically defensible — there was nothing to diarize, and a model upgrade still flags it stale), and CLAUDE.md's storage-table list not naming `role_provenance` (that list already omits `observations`; COORDINATION.md and the plan document the change, and `va stale` docs belong to the uncommitted PROV-4 work).

```json
{"verdict": "request_changes", "findings": [{"severity": "major", "file": "src/va/provenance.py", "line": 46, "issue": "PROVENANCE_ROLES excludes Role 11 on the false claim it 'stores no rows' — deep-scan persists VLM micro-captions in the observations table under a cache key with no model identity, so upgraded-model staleness there is invisible to both the fingerprint layer and the planned va stale.", "scenario": "vlm_captioner is upgraded (e.g. qwen2.5-vl-7b -> qwen3-vl-30b); the plan-B selective reprocess re-runs captions and restamps provenance current, but deep_scan.py:281 still treats the old-model observations rows as a valid cache — va ask serves CODE-COUNTED answers computed from the old model forever, a missed stale the module's own design rule forbids. Fix: include the vlm_captioner fingerprint in deep_scan's prompt_key (busting the cache on upgrade), correct the comment, and record the decision in provenance-reprocess-plan.md."}]}
```
