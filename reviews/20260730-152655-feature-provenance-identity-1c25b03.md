# Agent review — approve

date: 2026-07-30T15:30:56.842173
range: origin/main..HEAD
branch: feature/provenance-identity
findings: 0


---

## Full review

Review complete. I examined the single commit in `origin/main..HEAD` (PROV-1: new `src/va/provenance.py`, `tests/test_provenance.py`, plan-doc status update), read `configuration.py`, `registry.py`, all four shipped config dirs, the covering plan, and COORDINATION.md, and ran the offline suite.

**Verdict: approve.** The offline suite is green (469 passed, 2 skipped, including the 16 new provenance tests). No correctness, contract, or coverage findings survived verification.

What I checked and how the suspicions resolved:

- **Fingerprint vs. real configs.** `Config.role()` folds every profile-global key into each role's `load`; the only globals in all shipped profiles are `device`/`dtype`/`residency`, all in `_NON_OUTPUT_KEYS`, so the stamped roles fingerprint identically across `run-siglip`/`run-claude`/`run-qwen3vl` where their models are identical — no spurious cross-config staleness for the shared workdir. The one asymmetric key (`claude-code: timeout_seconds`) only affects the reasoner, which decision D6 excludes from provenance scope.
- **Namespace collisions.** `load.*` / `role.*` prefixes prevent whisper's `models.whisper.model: base` from clobbering the role model id, and the explicit-vocab keys (`classes`/`actions`) are skipped in the role-level loop before being folded via `get_ingest_classes`/`get_ingest_actions` — no double-count, and the unset-equals-default equivalence is tested.
- **Unconfigured-role `"unknown"` degradation.** The registry actually resolves an omitted role to a concrete stub (e.g. `sidecar`, and `embedder_id` falls back to `"hash"`), so `"unknown"` would misdescribe what ran — but the module docstring names exactly this limitation, the failure direction is a safe false-stale, and every shipped config sets every role. Documented, safe-direction, accepted by the author — not a finding.
- **Plan conformance.** D1's "embedders: weights+dim" — `dim` is absent from the fingerprint, but it is a runtime property carried as its own column in the PROV-2 table schema, and the stub/SigLIP case already differs by model id; the signature drift from the plan's sketch (`Config` instead of `RoleConfig`) is required for the vocab-default fold and is recorded in the updated status entry. No gap.
- **Docs/COORDINATION.** No new env var, CLI flag, or config key; the plan status was updated in the same change; X-1 assigns the COORDINATION.md entry to the new table (PROV-2), not this helper. The `need_agent_review:` subject is exempt from the message-clarity rule.

One forward-looking note for PROV-2 (not a finding on this diff): when the fingerprint is persisted, the `unknown`-vs-registry-default divergence becomes visible in `va stale` output — worth resolving the default backend there (as `embedder_id` does) or keeping the docstring's rationale next to the report code.

```json
{"verdict": "approve", "findings": []}
```
