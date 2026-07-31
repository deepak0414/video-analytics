# Agent review — approve

date: 2026-07-30T18:20:08.389537
range: origin/main..HEAD
branch: feature/provenance-identity
findings: 1

- **minor** `src/va/pipeline/stale.py:421` — The fps column was added (per the plan) specifically so PROV-4 could distinguish a corpus extended at a different fps, but stale_report neither compares nor returns fps, and this gap is not recorded as a scope cut.
  - scenario: A corpus ingested at fps=2.0 shows a video stale after a model upgrade; the user follows va stale's printed remedy `va reingest <video>`, which defaults to fps=1.0 — the video is re-stamped fully current at half the frame density Roles 2/5/6/7 previously saw, and no report can surface the drift afterward. Fix: include fps in the report rows (warn on mismatch with the reingest default) or record the deferral in provenance-reprocess-plan.md like the va-provenance cut.

---

## Full review

Review complete. I read the full diff (4 commits: PROV-1 fingerprint helper, PROV-2 table/migration/store, PROV-3 ingest stamping + deep-scan cache keys, PROV-4 `va stale`), then verified the load-bearing suspicions against `configuration.py`, `registry.py`, `ingest.py`, `manage.py`, `deep_scan.py`, and the catalog API, and ran the offline suite: **486 passed, 2 skipped**.

## What I checked and how it dissolved

- **Failure-attribution completeness in ingest**: every best-effort role's except path adds itself to `failed` (speech failure correctly also marks the diarizer; a mid-batch detection failure marks both detector and tracker and clears partial rows). Roles 1/2 aren't wrapped, but their failure aborts the ingest entirely, so the video never reaches `done` and `stale_report` skips it. Consistent.
- **Pinned-config race**: the pin at role-launch time can only make a stamp look *older* than the run (false stale), never newer — the safe direction, and it's tested (`test_stamp_uses_config_pinned_at_role_launch_not_ingest_end` verifies the pinned cfg flows into every `role_fingerprint` call).
- **Fingerprint symmetry**: `get_ingest_classes/actions` genuinely use the passed `cfg`, so stamp-time and stale-time fingerprints are computed identically. The `model="unknown"` asymmetry for an unconfigured role vs `embedder_id`'s `"hash"` fallback only produces a false stale and is documented in the module docstring.
- **Monkeypatch-based tests hit the real seams**: both `deep_scan.py` and `_record_provenance` import `role_fingerprint` function-locally at call time, so the tests' module-attribute patches exercise the production path, not a stale binding.
- **`test_migrations.py` edit**: legitimately generalized from hardcoded v1/v2 to migration-count-agnostic; nothing weakened.
- **Contract/docs hygiene**: schema v2 migration is registered and tested against a v1 DB; COORDINATION.md logs all three shared-surface changes (schema v2, ingest stamping, deep-scan cache invalidation with the one-time `.va-shots` re-sweep warning); `va stale` is in CLAUDE.md; `va remove` purges the new table (tested). The `va provenance <video>` scope cut is explicitly recorded in the plan.
- **Commit messages**: the three feat commits are self-describing with plan IDs trailing; the head `need_agent_review:` subject is exempt.

## The one finding

**minor — plan conformance, `src/va/pipeline/stale.py:421`.** The plan's PROV-2/PROV-3 text says the `fps` column exists "so PROV-4 can tell a corpus extended at a different fps apart," but `stale_report` neither compares nor even returns `fps` — PROV-4 as built cannot tell fps drift apart, and unlike the `va provenance` cut, this gap is not recorded as a scope cut. Concrete scenario: a corpus ingested at `fps=2.0` shows a video stale after a model bump; the user follows the printed remedy `va reingest <video>`, which defaults to `fps=1.0` — the video re-stamps as fully current at half the frame density Roles 2/5/6/7 saw before, and no report can ever surface it. Safe path: include `fps` in the report rows (and ideally warn when a video's recorded fps differs from the corpus mode or the reingest default), or record the deferral in provenance-reprocess-plan.md the way the `va provenance` cut was.

No critical or major findings, so the verdict is approve.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/stale.py", "line": 421, "issue": "The fps column was added (per the plan) specifically so PROV-4 could distinguish a corpus extended at a different fps, but stale_report neither compares nor returns fps, and this gap is not recorded as a scope cut.", "scenario": "A corpus ingested at fps=2.0 shows a video stale after a model upgrade; the user follows va stale's printed remedy `va reingest <video>`, which defaults to fps=1.0 — the video is re-stamped fully current at half the frame density Roles 2/5/6/7 previously saw, and no report can surface the drift afterward. Fix: include fps in the report rows (warn on mismatch with the reingest default) or record the deferral in provenance-reprocess-plan.md like the va-provenance cut."}]}
```
