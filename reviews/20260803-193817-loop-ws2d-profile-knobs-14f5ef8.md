# Agent review — approve

date: 2026-08-03T19:44:50.556550
range: origin/main..HEAD
branch: loop/ws2d-profile-knobs
findings: 2

- **minor** `src/va/pipeline/ingest.py:231` — Non-bool `enabled` is validated only in footage yamls; a string `enabled: "false"` in roles.yaml itself makes ingest's raw truthiness run+stamp the role while stale/reprocess's pydantic coercion treats it as disabled — a non-convergent stale loop.
  - scenario: Operator writes enabled: "false" under speech_to_text in roles.yaml; ingest runs and stamps STT, va stale reports it stale (stamped-and-disabled), va reprocess says reingest to purge, reingest runs and restamps it, and the cycle never converges. Fix: enforce the same boolean check on roles.yaml specs at load_config, or gate ingest via cfg.role().enabled so both consumers share one interpretation.
- **minor** `src/va/pipeline/ingest.py:154` — The branch where roles.yaml `active_footage_profile` determines the recorded videos.profile at ingest is only tested at the load_config layer, never end-to-end through ingest.
  - scenario: A refactor of the probe resolution records 'generic' while the pinned cfg runs the roles under the active profile; stale then compares every such video against the wrong overlay (record!=reality) with no test failing. Fix: one ingest test with active_footage_profile set in a temp roles.yaml asserting the recorded profile and the gated-role effect.

---

## Full review

The suite is green (573 passed / 2 skipped, matching the COORDINATION.md claim). I've traced the full range across all four commits, re-checked the prior review's two minors (both fixed in the amended HEAD: `generic.yaml` now says "EXAMPLE values", and the loop doc records the `Config.footage` placement decision), and adversarially probed the gating/staleness/reprocess convergence paths.

## Review — `loop/ws2d-profile-knobs` (origin/main..HEAD, 4 commits)

**Verdict: approve** — two minor findings, no correctness defects that survive verification.

### What I verified (and what dissolved)

- **Ingest gating**: the `_enabled` gate, unconditional evaluation of `diarizer_on`/`tracker_on` (silent-video and zero-frame cases both tested), purge-on-skip for every gated store, and the untracked-detections branch all hold up. Caption purge is implicit-but-correct: `replace_segments` deletes prior rows (`segments.py:44`) before the captioner gate runs, so a skipped captioner leaves no stale captions.
- **Record==reality**: ingest records the same resolved profile it pins (`explicit > active_footage_profile > source default`), `stale_report`/`execute_reprocess` compare and restamp per recorded profile via `config_for()`, fingerprints stamp the overlay-applied config on both sides. The stamped-and-disabled → stale → reprocess-skips → reingest-purges loop converges (tested).
- **Failure paths**: a broken/renamed profile yaml degrades per-item everywhere (dedup no-op survives, stale warns+skips via the logging lastResort handler so it's visible on stderr, reprocess emits per-role `failed` entries); an explicit typo'd `--profile` fails before any mutation, and `reingest_video` validates before the destructive removal.
- **Contracts**: schema v3 migration is guarded and counted; `Video.profile`/`ingest(profile=)` are additive (web `jobs.py` calls `ingest()` positionally-compatible); the one breaking shape change (`execute_reprocess` skipped 3-tuples) has the CLI as its only in-repo consumer, updated in the same commit, ⚠-logged in COORDINATION.md.
- **Combinations**: footage yamls are identical in all four config dirs and `test_all_shipped_profiles_parse` loads both profiles in each; the embedder-override query-blindness caveat is documented in CLAUDE.md. Docs parity for `--profile`, `videos.profile`, `active_footage_profile`, the WS2.d knobs, and the `"off"` YAML trap all land in this range.
- **Fingerprint interaction**: `role.enabled` folding into the fingerprint only produces false stales (safe direction), never missed ones — checked both directions of a profile edit.

### Findings

**1. minor — `src/va/pipeline/ingest.py:231`** — the non-bool `enabled:` validation guards only footage-profile yamls, so `enabled: "false"` (string) written directly in `roles.yaml` splits the consumers exactly the way the WS2.c validation was built to prevent: ingest's raw-dict truthiness (`spec.get("enabled", True)` → `"false"` is truthy) **runs and stamps** the role, while stale/reprocess read it through pydantic coercion (`cfg.role(r).enabled` → `False`) and treat it as disabled — stamped-and-disabled reads stale, reprocess routes it to "reingest purges its rows", and the reingest runs it again: a non-convergent stale loop. Safe path: apply the same must-be-a-real-boolean check to `roles.yaml` role specs at `load_config` (or have `_enabled` read via `cfg.role()` with the absent-role case handled), so both consumers see one truth.

**2. minor — `src/va/pipeline/ingest.py:154`** — the probe-resolution branch that makes `roles.yaml active_footage_profile` drive the *recorded* `videos.profile` has no end-to-end test: `test_roles_yaml_can_select_the_footage_profile` covers `load_config` only, and every ingest-path test passes `profile=` explicitly or falls through to `generic`. I traced the branch by hand and it is correct today, but a regression there (recording `generic` while the roles run under the active profile) would silently violate the WS2.c record==reality rule that stale/reprocess depend on. Safe path: one ingest test with `active_footage_profile: security` in a temp roles.yaml asserting `res.video.profile == "security"` and zero transcript rows.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/ingest.py", "line": 231, "issue": "Non-bool `enabled` is validated only in footage yamls; a string `enabled: \"false\"` in roles.yaml itself makes ingest's raw truthiness run+stamp the role while stale/reprocess's pydantic coercion treats it as disabled — a non-convergent stale loop.", "scenario": "Operator writes enabled: \"false\" under speech_to_text in roles.yaml; ingest runs and stamps STT, va stale reports it stale (stamped-and-disabled), va reprocess says reingest to purge, reingest runs and restamps it, and the cycle never converges. Fix: enforce the same boolean check on roles.yaml specs at load_config, or gate ingest via cfg.role().enabled so both consumers share one interpretation."}, {"severity": "minor", "file": "src/va/pipeline/ingest.py", "line": 154, "issue": "The branch where roles.yaml `active_footage_profile` determines the recorded videos.profile at ingest is only tested at the load_config layer, never end-to-end through ingest.", "scenario": "A refactor of the probe resolution records 'generic' while the pinned cfg runs the roles under the active profile; stale then compares every such video against the wrong overlay (record!=reality) with no test failing. Fix: one ingest test with active_footage_profile set in a temp roles.yaml asserting the recorded profile and the gated-role effect."}]}
```
