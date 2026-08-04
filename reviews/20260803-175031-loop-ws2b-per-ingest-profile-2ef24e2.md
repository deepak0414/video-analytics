# Agent review — approve

date: 2026-08-03T17:53:37.234475
range: origin/main..HEAD
branch: loop/ws2b-per-ingest-profile
findings: 0


---

## Full review

Review complete. Everything I chased dissolved on closer reading — verdict: **approve**, no findings.

## Review: `origin/main..HEAD` (WS2.a footage-profile config layer + WS2.b per-ingest profile recording, rebuilt HEAD `2ef24e2`)

**The prior round's finding is genuinely fixed.** Review `ab4b75c` flagged that the pre-removal profile validation in `reingest_video` was skipped when `target_profile` was None, letting a broken `active_footage_profile` destroy the index before failing. Current code (`src/va/pipeline/manage.py:355`) calls `load_config(footage_profile=target_profile)` unconditionally — a None target resolves exactly as ingest's own probe will — and the new test `test_reingest_of_pre_profile_video_with_broken_active_profile_leaves_it_intact` reproduces the exact scenario (NULL recorded profile + roles.yaml naming a missing yaml) and asserts the video survives.

**What I verified and it holds up:**

- **Config layering** (`configuration.py`): `_deep_merge` is non-mutating; empty/None role bodies tolerated; unknown role names in an overlay raise `KeyError`; non-dict role specs raise `ValueError`; `generic` tolerates a missing file so `run-siglip/`, `run-claude/`, `run-qwen3vl/` config dirs (which have no `footage/` dir) keep working — tested via the "no footage dir at all" identity case. Resolution precedence (explicit arg > roles.yaml `active_footage_profile` > `generic`) is tested both ways.
- **Migration discipline** (`schema.py`): v3 uses the idempotent `add_column` recipe, `SCHEMA_VERSION` bump matches the migration-count assert, `Catalog.__init__` → `connect()` applies migrations on every open, and `_COLS` / `_to_row` / `_from_row` / the `Video` contract all gained `profile` together (additive, defaulted — web layer unbroken). Fresh-DB and in-place v2→v3 with NULL read-back both tested.
- **Recorded name matches runtime reality where possible**: the ingest probe records the same resolution roles' self-loaded config applies, with the source-derived default only as final fallback; `test_roles_yaml_active_footage_profile_is_what_gets_recorded` covers the combination. The residual gap — an explicit `--profile X` differing from the roles.yaml selection records X while roles run under the yaml selection — is the explicitly documented "recorded-but-not-role-consumed until WS2.c" limitation (code comment, COORDINATION.md, CLAUDE.md), only reachable once a non-no-op profile exists, and WS2.c is the tracked closer. Not a new finding.
- **Plan conformance**: WS2.a done-when (generic identical three ways; override changes exactly the overridden keys) and WS2.b done-when (migration test; with/without-flag recording; pre-profile rows read back NULL unchanged) each map to a passing test. The WS2.a carry-over (top-level `roles:` shape validation) is tracked under WS2.c in the loop file — tracked, not re-reported.
- **Dissolved suspicions**: the unknown-profile fresh ingest leaving a non-`done` stub row is deliberate and test-asserted (`test_ingest_with_unknown_profile_fails_before_any_work`); the dedup path validates but doesn't overwrite the recorded profile, with a clear CLI notice pointing at `va reingest --profile`; the `test_provenance_ingest` double change is an extension to accept the new kwarg, not a weakening; provenance intentionally stamps the base config (stamping the overlay before roles consume it would stamp models that didn't run) — reasoned in-code and logged; both commit subjects are provisional `need_agent_review:` and exempt from the clarity rule; both COORDINATION.md entries are logged; CLAUDE.md documents the new layer, `--profile`, and `videos.profile` semantics in the same change.

Test execution was denied in this session (read-only review, consistent with prior rounds); COORDINATION.md records the full suite at 538 passed / 2 skipped for this work.

```json
{"verdict": "approve", "findings": []}
```
