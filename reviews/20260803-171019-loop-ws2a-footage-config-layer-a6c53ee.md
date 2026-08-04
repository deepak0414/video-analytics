# Agent review — approve

date: 2026-08-03T17:13:40.508408
range: origin/main..HEAD
branch: loop/ws2a-footage-config-layer
findings: 1

- **minor** `src/va/configuration.py:54` — A footage-profile yaml whose top-level `roles:` value is not a mapping (e.g. a YAML list, or a whole-document list) crashes load_config with a bare AttributeError instead of the clear per-file ValueError the code raises for per-role shape errors.
  - scenario: A user drafts `roles:` as a list of one-role mappings ('roles:\n  - visual_embedder: {model: siglip}'); load_config raises AttributeError: 'list' object has no attribute 'items' with no file name, instead of a config error pointing at the malformed profile. Fix: validate doc and doc['roles'] are mappings in _load_footage_overlay with the same ValueError style, plus a test, before WS2.c authors the first real profile.

---

## Full review

## Review summary

**Scope:** one commit (`a6c53ee`), the WS2.a footage-profile third config layer — `configuration.py` gains `_deep_merge`/`_load_footage_overlay`, `load_config(footage_profile=)`, `Config.footage_profile`, a no-op `generic.yaml`, 8 new tests, plus CLAUDE.md and COORDINATION.md updates.

**What I verified:**

- **Done-when conformance (WS2.a in `architecture-evolution-loop.md`):** (i) generic-is-a-no-op proven three ways in `test_generic_is_a_noop`; (ii) exact-override-delta proven in `test_override_changes_exactly_the_overridden_keys` (changed keys, surviving sibling keys, and untouched roles all asserted). Full offline suite green at HEAD: **533 passed, 2 skipped**. The default A-EV path is byte-for-byte unchanged — an empty overlay short-circuits the merge entirely.
- **Prior-review follow-through:** this HEAD is the amended version of the commit the earlier review (`reviews/20260803-170447-...-2e9db2b.md`) approved with two minors. Both are fixed here — the None-role-body crash (`overlay.get(name) or {}`, covered by `test_empty_role_body_is_tolerated`) and the silently-dropped unknown role (now a `KeyError`, covered by `test_unknown_role_in_overlay_raises`). Good loop closure.
- **Combination coverage:** `run-siglip`/`run-claude`/`run-qwen3vl` config dirs have no `footage/` dir and no `active_footage_profile` key, so they ride the tolerated-missing-`generic` path, which the tests exercise directly. Provenance and stale-detection consume the already-merged `cfg.roles`, so a future non-generic profile correctly flows into staleness fingerprints; under the default nothing changes.
- **Contract + docs parity:** COORDINATION.md logged (additive signature change, affected dirs named); CLAUDE.md documents the three-layer merge, `active_footage_profile`, and `load_config(footage_profile=...)` in the same change. No test deletions or weakening. Commit subject is a provisional `need_agent_review:`, exempt from the clarity rule.

**One new finding (minor):** `_load_footage_overlay` validates that each *role's* value is a dict (nice `ValueError` naming the file), but not that the top-level `roles:` value is a mapping. A footage yaml written in list form —

```yaml
roles:
  - visual_embedder: {model: siglip}
```

— parses `doc["roles"]` as a truthy list, and `overlay.items()` at `src/va/configuration.py:55` raises a bare `AttributeError: 'list' object has no attribute 'items'` with no file name, while a whole-document list makes `doc.get` itself crash at line 54. Safe path: the same shape check already used per-role — `if not isinstance(doc, dict) or (doc.get("roles") is not None and not isinstance(doc["roles"], dict)): raise ValueError(...)` naming the path, plus a one-line test. Natural home is the WS2.c hardening pass before the first real `security` profile is authored; it does not affect any shipped config.

**Non-finding note:** the COORDINATION.md entry says "530 passed" — accurate when written, now 533 after the amend added the three hardening tests. The final digest/`/verify` evidence block will carry the current count, so I'm not raising it as a finding.

Verdict: **approve** — no critical or major findings.

```json
{"verdict": "approve", "findings": [
  {"severity": "minor", "file": "src/va/configuration.py", "line": 54, "issue": "A footage-profile yaml whose top-level `roles:` value is not a mapping (e.g. a YAML list, or a whole-document list) crashes load_config with a bare AttributeError instead of the clear per-file ValueError the code raises for per-role shape errors.", "scenario": "A user drafts `roles:` as a list of one-role mappings ('roles:\\n  - visual_embedder: {model: siglip}'); load_config raises AttributeError: 'list' object has no attribute 'items' with no file name, instead of a config error pointing at the malformed profile. Fix: validate doc and doc['roles'] are mappings in _load_footage_overlay with the same ValueError style, plus a test, before WS2.c authors the first real profile."}
]}
```
