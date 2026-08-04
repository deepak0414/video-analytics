# Agent review — approve

date: 2026-08-03T17:48:10.474210
range: origin/main..HEAD
branch: loop/ws2b-per-ingest-profile
findings: 1

- **minor** `src/va/pipeline/manage.py:110` — The pre-removal profile validation is skipped when target_profile is None, but ingest()'s own load_config probe (which resolves roles.yaml active_footage_profile) can still raise after remove_video has already destroyed the video's index — violating the stated validate-before-destroy guarantee.
  - scenario: roles.yaml sets active_footage_profile to a name whose yaml was renamed/deleted; `va reingest <pre-profile-video>` (no --profile) skips validation because target_profile is None, remove_video deletes the catalog row, role rows, and vector shard, then ingest's load_config(footage_profile=None) raises FileNotFoundError. Fix: call load_config(footage_profile=target_profile) unconditionally (it resolves None exactly as ingest will), and add a test for the broken-active_footage_profile + no-flag reingest combination.

---

## Full review

Test execution was denied (read-only review session), so I rely on code reading; COORDINATION.md records the suite at 538 passed / 2 skipped for this HEAD.

## Review: `origin/main..HEAD` (WS2.a footage-profile config layer + WS2.b per-ingest profile recording)

**What I verified and it holds up:**

- **Prior round's findings are genuinely fixed.** The recorded profile now follows the runtime's own resolution (explicit arg > roles.yaml `active_footage_profile` > source default) via the `load_config` probe, with `test_roles_yaml_active_footage_profile_is_what_gets_recorded` covering the combination the last review flagged. `reingest_video` now validates the target profile before `remove_video`, with `test_reingest_with_bad_profile_leaves_the_video_intact`.
- **Migration discipline** — `_m3_videos_profile` uses the idempotent `add_column` recipe, `SCHEMA_VERSION` bump matches the migration-count assert, and tests cover both fresh-DB and in-place v2→v3 migration with NULL read-back. `_COLS`, the row mapper, and the `Video` contract all gained `profile` together; the field is additive with a default, so the web layer's contract is unbroken, and both COORDINATION.md entries are logged.
- **WS2.a/WS2.b "Done when" items** each map to a test (generic no-op identity three ways; override changes exactly the overridden keys; migration; flag/no-flag recording; NULL unchanged).
- **Dissolved suspicions:** the unknown-profile-on-fresh-ingest path leaving a non-failed row is deliberate and test-asserted; the top-level `roles:`-must-be-a-mapping gap is recorded as a WS2.c carry-over in the loop file (tracked, not re-reported); the provenance stamp intentionally fingerprinting the base config is reasoned and logged in COORDINATION.md; the `test_provenance_ingest` double change is an extension, not a weakening; both commit subjects are provisional `need_agent_review:` and exempt from the clarity rule.

**One finding survived:**

**minor — `src/va/pipeline/manage.py:110` — the validate-before-destroy guard is skipped exactly when `target_profile` is None, but the post-removal probe inside `ingest()` can still raise.** `if target_profile is not None: load_config(...)` skips validation for a pre-profile video reingested without `--profile`. But `_ingest_impl` then runs `load_config(footage_profile=None)`, which resolves roles.yaml `active_footage_profile` and raises `FileNotFoundError` if that names a missing/renamed yaml — *after* `remove_video` has deleted the catalog row, all role rows, and the vector shard. Scenario: roles.yaml gains `active_footage_profile: nvr`, `nvr.yaml` is later renamed, then `va reingest <pre-profile-video>` → index destroyed, then the probe fails (media is kept, so it's recoverable by re-ingesting after fixing config, but the guarantee the comment states — "fail here with the video's data intact" — is violated). Safe path: call `load_config(footage_profile=target_profile)` unconditionally — it handles `None` by resolving the same way ingest will, so the pre-removal probe exercises exactly the config path that would fail post-removal; drop the `is not None` guard and add a test with a broken `active_footage_profile` and no explicit `--profile`.

Not blocking, worth knowing: an explicit `--profile X` that differs from roles.yaml `active_footage_profile` still records X while roles self-load and run under the roles.yaml selection — but this is the explicitly documented "recorded-but-not-role-consumed until WS2.c" limitation (code comment, COORDINATION.md, CLAUDE.md), only reachable once a non-no-op profile exists, and WS2.c is the tracked item that closes it. Not re-reported as a finding.

Verdict: **approve** (no critical/major findings).

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/manage.py", "line": 110, "issue": "The pre-removal profile validation is skipped when target_profile is None, but ingest()'s own load_config probe (which resolves roles.yaml active_footage_profile) can still raise after remove_video has already destroyed the video's index — violating the stated validate-before-destroy guarantee.", "scenario": "roles.yaml sets active_footage_profile to a name whose yaml was renamed/deleted; `va reingest <pre-profile-video>` (no --profile) skips validation because target_profile is None, remove_video deletes the catalog row, role rows, and vector shard, then ingest's load_config(footage_profile=None) raises FileNotFoundError. Fix: call load_config(footage_profile=target_profile) unconditionally (it resolves None exactly as ingest will), and add a test for the broken-active_footage_profile + no-flag reingest combination."}]}
```
