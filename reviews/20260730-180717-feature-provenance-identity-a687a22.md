# Agent review — approve

date: 2026-07-30T18:12:19.166192
range: origin/main..HEAD
branch: feature/provenance-identity
findings: 2

- **minor** `provenance-reprocess-plan.md:100` — PROV-4 completion log drops `va provenance <video>` (named in the PROV-4 spec line) without recording the scope cut
  - scenario: Pillar B work later assumes a per-video provenance inspection command exists because the plan item names it and the log says PROV-4 is DONE; the gap surfaces only when someone tries to use it.
- **minor** `src/va/cli.py:125` — `va stale` output never states which config/profile it compared against, yet instructs `va reingest` as the remedy
  - scenario: User runs `va stale` on .va-shots without VA_CONFIG_DIR=run-siglip/config; every video reports stale for all roles, and following the printed `va reingest` advice under the stub config would overwrite real-model rows and vectors with stub output.

---

## Full review

Review complete. I read the full range (four commits, PROV-1 through PROV-4 of the provenance pillar), verified the new `va stale` delta against `stale.py`, `cli.py`, `provenance.py`, `configuration.py`, `registry.py`, the config dirs, and the covering plan, and ran the offline suite: **486 passed, 2 skipped — green**.

The prior in-loop review (e7d807d) already dissected PROV-1/2/3 in depth and I re-verified its dismissals rather than repeating them — the fingerprint's conservative-by-exclusion direction, the config-pinned-at-role-launch race reasoning, the failed-role-not-stamped paths (every best-effort `except` adds to `failed`; critical-path failures never reach the stamp), the migration recipe, the `manage.py` purge, and the deep-scan cache-key mechanism (both `role_fingerprint` call sites are call-time imports, so the invalidation test genuinely exercises it). All hold. `reasoner`, `text_embedder`, and every stamped role are configured in all shipped config dirs, so the `"unknown"` fingerprint degradation has the low exposure the docstring claims.

On the fresh PROV-4 delta, the test coverage is genuinely strong (clean-after-ingest, changed fingerprint, missing provenance = stale-everywhere, role filter, non-done skip, unknown-role raises at both CLI and library). Two minor findings survived:

1. **Plan conformance (minor)** — the plan's PROV-4 item specifies "`va provenance <video>` / `va stale`", but only `va stale` was built, and the completion log entry declares "PROV-4 DONE — pillar A COMPLETE" without recording that the per-video provenance-inspection report was dropped. If the "/" meant "either," the log should say so; silent scope narrowing is what the log exists to prevent. Safe path: one line in the plan log recording the cut (or build the per-video report).

2. **Config-dir foot-gun in the stale report (minor)** — `stale_report` compares against whatever config `load_config()` resolves (VA_CONFIG_DIR or the stub default), and neither the report nor the CLI output states which. Running `va stale` on `.va-shots` while forgetting `VA_CONFIG_DIR=run-siglip/config` reports every video stale for every role and prints the remediation "run `va reingest <video>`" — obeying which would overwrite real-model data with stub outputs. This is the same class as CLAUDE.md gotcha #2, but this command is exactly the tool reached for during model switches, when config-dir confusion peaks. Safe path: print the active profile/config dir in the report header so a mismatch is self-evident (or extend gotcha #2 to name `va stale`).

Neither is major; the verdict is approve.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "provenance-reprocess-plan.md", "line": 100, "issue": "PROV-4 completion log drops `va provenance <video>` (named in the PROV-4 spec line) without recording the scope cut", "scenario": "Pillar B work later assumes a per-video provenance inspection command exists because the plan item names it and the log says PROV-4 is DONE; the gap surfaces only when someone tries to use it."}, {"severity": "minor", "file": "src/va/cli.py", "line": 125, "issue": "`va stale` output never states which config/profile it compared against, yet instructs `va reingest` as the remedy", "scenario": "User runs `va stale` on .va-shots without VA_CONFIG_DIR=run-siglip/config; every video reports stale for all roles, and following the printed `va reingest` advice under the stub config would overwrite real-model rows and vectors with stub output."}]}
```
