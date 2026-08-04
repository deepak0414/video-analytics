# Agent review — approve

date: 2026-08-03T19:53:48.782277
range: origin/main..HEAD
branch: loop/ws2d-profile-knobs
findings: 1

- **minor** `COORDINATION.md:434` — The WS2.d coordination entry records 'Full suite 573 passed / 2 skipped' but the same commit's tree yields 575 passed / 2 skipped (the two review-round regression tests were amended in without updating the logged count).
  - scenario: Another agent or the finalize digest cross-checks the logged evidence against a local run, gets 575 vs 573, and must re-derive whether tests were added or dropped; fix the count in the entry when finalizing and let /verify emit the real 575 in the PR body.

---

## Full review

The `plan_reprocess` path returns `stale_report` rows unchanged, so the new `profile`/`source_type` fields ride through to `execute_reprocess` — consistent.

## Review — `loop/ws2d-profile-knobs` (origin/main..HEAD, 4 commits)

**Verdict: approve.** The suite is green at HEAD (575 passed / 2 skipped, 76s). Both minors from the previous review round (14f5ef8) are fixed in the amended HEAD and each carries a regression test: the enabled-gate now reads through `cfg.role()` so a string `enabled: "false"` in roles.yaml is interpreted identically by ingest and staleness (`test_string_false_enabled_in_roles_yaml_reads_disabled_everywhere`), and the `active_footage_profile` record==reality path is now tested end-to-end through ingest (`test_active_footage_profile_gates_roles_end_to_end`). The WS2.d "Done when" (knobs parse, validate, land on the config with defaults matching today) and the carried WS2.c round-8 skip-reason fix are both delivered and tested.

### What I probed and what dissolved

- **Gating correctness**: `_enabled` gate order (`diarizer_on`/`tracker_on` evaluated unconditionally — silent-video and zero-frame regressions both tested), purge-on-skip for every gated store, untracked-detections branch, and skipped-roles exclusion from provenance all hold. Caption purge is implicit-but-correct: `replace_segments` clears prior captions before the captioner gate.
- **Convergence**: stamped-and-disabled → stale → reprocess routes to `skipped` → reingest purges under the carried profile — tested end-to-end (`test_role_disabled_after_it_ran_reads_stale`, `test_reprocess_never_reruns_a_profile_disabled_role`). The mid-batch-edit race direction is safe (rebuilders load fresh, restamp uses the per-profile pin → worst case false stale, never missed stale).
- **Failure paths**: renamed profile yaml degrades per-item in stale (visible warning via logging lastResort) and per-role in reprocess; explicit `--profile` typo fails before any mutation on ingest and before the destructive removal on reingest — all tested.
- **`FootageSettings`**: `extra="forbid"`, positive-retention validator, the `deep_scan: off`-is-YAML-False trap — validated at load with the file named, parametrized tests cover each, and all four config dirs' shipped profiles parse (`test_all_shipped_profiles_parse` covers the config-dir combination axis).
- **Contracts/docs**: schema v3 migration guarded and asserted against `SCHEMA_VERSION`; the one breaking shape change (`execute_reprocess` skipped 3-tuples) has its only in-repo consumer (the CLI) updated in the same commit and is ⚠-flagged in COORDINATION.md; `--profile`, `videos.profile`, the knobs, and the embedder-override query-blindness caveat are all documented in CLAUDE.md in-range. No disputes in workflow-trust-plan.md touch WS2.
- Considered and dropped as practically unreachable: prior-attempt track rows surviving when the detector *fails* while the tracker is profile-disabled (a retryable state can't have written tracks — every critical step precedes track writes).

### Findings

**1. minor — `COORDINATION.md:434`** — the WS2.d entry claims "Full suite 573 passed / 2 skipped," but the tree at HEAD (which includes the round-2 review fixes amended into this same commit) yields 575 passed / 2 skipped, so the shared log other agents read understates the evidence for the very commit that carries it. Failure scenario: the web agent (or the finalize digest) cross-checks the logged count against a local run, sees a mismatch, and has to re-derive whether tests were added or lost. Safe path: correct the count in the entry (or note "count as of round 1; +2 review-round tests") when finalizing — the `/verify` evidence block for the PR must carry the real 575 either way.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "COORDINATION.md", "line": 434, "issue": "The WS2.d coordination entry records 'Full suite 573 passed / 2 skipped' but the same commit's tree yields 575 passed / 2 skipped (the two review-round regression tests were amended in without updating the logged count).", "scenario": "Another agent or the finalize digest cross-checks the logged evidence against a local run, gets 575 vs 573, and must re-derive whether tests were added or dropped; fix the count in the entry when finalizing and let /verify emit the real 575 in the PR body."}]}
```
