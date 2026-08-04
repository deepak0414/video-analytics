# Agent review — approve

date: 2026-08-03T17:08:26.237512
range: origin/main..HEAD
branch: loop/ws2a-footage-config-layer
findings: 2

- **minor** `src/va/configuration.py:105` — A footage-profile role entry with an empty YAML body (parses as None) crashes load_config with AttributeError in _deep_merge, because overlay.get(name, {}) returns the stored None rather than the default.
  - scenario: While drafting a profile, a user writes 'roles:\n  visual_embedder:\n' (params commented out); load_config raises AttributeError: 'NoneType' object has no attribute 'items' instead of a config error naming the file. Fix: overlay.get(name) or {} / normalize non-dict role values with a clear error.
- **minor** `src/va/configuration.py:104` — Overlay role names not present in the base roles.yaml mapping are silently dropped by the merge comprehension, so a typo'd or unknown role in a footage profile silently never applies.
  - scenario: A 'security' profile misspells object_detector; the vocab override silently no-ops, the profile appears active (footage_profile='security'), and the misconfiguration surfaces only as unexplained retrieval quality loss on real footage. Fix: validate overlay keys against base roles and raise/log on unknown names, with a test.

---

## Full review

The sandbox blocks scratch-file writes, but both edge cases are unambiguous from static inspection of `configuration.py:104-107` and `_deep_merge`:

- **Empty role body**: an overlay like `roles: {visual_embedder: null}` (natural YAML while drafting, e.g. commenting out the params under a role) makes `overlay.get(name, {})` return `None` (the key exists, so the default isn't used), and `_deep_merge(spec, None)` calls `None.items()` → `AttributeError` crash instead of a helpful config error.
- **Typo'd role name**: the merge comprehension iterates only the base `roles.items()`; an overlay key not present in `roles.yaml` (`visual_embeder`, or any role a given config dir doesn't define) is silently dropped — no error, no warning, the override just never applies.

I checked the rest of the surface: the `run-siglip/run-claude/run-qwen3vl` dirs have no `footage/` dir and no `active_footage_profile` key, so they ride the tolerated-missing-`generic` path, which is exactly what `test_generic_is_a_noop`'s "no footage dir at all" case covers. Provenance/staleness fingerprints consume the already-merged `cfg.roles`, so a footage-profile change correctly flows into stale detection. Docs parity is done in-change (CLAUDE.md three-layer description, `active_footage_profile`, the shape example in `generic.yaml`), and the COORDINATION.md entry's "530 passed" claim matches my fresh run (530 passed, 2 skipped).

## Review summary

**Scope:** one commit (`2e9db2b`), the WS2.a footage-profile third config layer. Verified against the WS2.a "Done when" in `architecture-evolution-loop.md`: (i) generic-is-a-no-op is proven three ways in `test_generic_is_a_noop`, (ii) exact-override-delta is proven in `test_override_changes_exactly_the_overridden_keys`, and the full offline suite is green. The default A-EV path is byte-for-byte unchanged (empty overlay short-circuits the merge entirely). COORDINATION.md is logged and CLAUDE.md updated in the same change. The commit subject is a provisional `need_agent_review:`, which is exempt from the clarity rule.

**Findings (both minor, both in the not-yet-exercised authoring path for real footage profiles):**

1. **minor — `src/va/configuration.py:105`** — A footage-profile role entry with an empty body (`visual_embedder:` → YAML `None`) crashes `load_config` with `AttributeError: 'NoneType' object has no attribute 'items'` inside `_deep_merge`, because `overlay.get(name, {})` returns the stored `None` rather than the default. Safe path: `overlay.get(name) or {}` (and/or normalize non-dict role values in `_load_footage_overlay` with a clear error naming the file), plus a test, before WS2.c starts authoring real profiles.

2. **minor — `src/va/configuration.py:104-107`** — Overlay role names absent from the base `roles:` mapping are silently discarded, so a typo'd role name (or a role the active config dir doesn't define) makes the profile silently not apply — precisely the "silent breakage in a non-default config nobody runs until real footage hits it" failure mode, e.g. a `security` profile whose object-detector vocab override never fires and nobody notices until retrieval quality is mysteriously bad. Safe path: validate overlay keys against the base roles and raise (or at minimum log) on unknown names; add a test asserting the error.

Neither finding affects the shipped default behavior or any existing config dir, and WS2.c is the natural place to harden them — but hardening should land before the first real (`security`) profile is authored.

```json
{"verdict": "approve", "findings": [
  {"severity": "minor", "file": "src/va/configuration.py", "line": 105, "issue": "A footage-profile role entry with an empty YAML body (parses as None) crashes load_config with AttributeError in _deep_merge, because overlay.get(name, {}) returns the stored None rather than the default.", "scenario": "While drafting a profile, a user writes 'roles:\\n  visual_embedder:\\n' (params commented out); load_config raises AttributeError: 'NoneType' object has no attribute 'items' instead of a config error naming the file. Fix: overlay.get(name) or {} / normalize non-dict role values with a clear error."},
  {"severity": "minor", "file": "src/va/configuration.py", "line": 104, "issue": "Overlay role names not present in the base roles.yaml mapping are silently dropped by the merge comprehension, so a typo'd or unknown role in a footage profile silently never applies.", "scenario": "A 'security' profile misspells object_detector; the vocab override silently no-ops, the profile appears active (footage_profile='security'), and the misconfiguration surfaces only as unexplained retrieval quality loss on real footage. Fix: validate overlay keys against base roles and raise/log on unknown names, with a test."}
]}
```
