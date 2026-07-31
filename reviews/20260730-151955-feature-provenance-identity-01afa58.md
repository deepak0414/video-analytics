# Agent review — approve

date: 2026-07-30T15:23:02.450672
range: origin/main..HEAD
branch: feature/provenance-identity
findings: 3

- **minor** `src/va/provenance.py:61` — The salient-by-default invariant covers only profile `load` keys; roles.yaml role-level keys are dropped by Config.role() and reach the fingerprint only via the two hardcoded classes/actions special cases.
  - scenario: A future role-level output knob following the established classes/actions pattern (e.g. a scene_detector threshold in roles.yaml) changes stored output but not the fingerprint — a missed stale, silently mixing old and new derived data, the exact failure the module documents as impossible. Safe path: hash all of cfg.roles[role] except backend/endpoint (namespaced), keeping the special cases only for the unset-vocab defaults fold.
- **minor** `src/va/provenance.py:37` — Credential keys (pyannote's `token`/`hf_token`) are not in the exclusion set, so they are treated as output-affecting and hashed into a fingerprint PROV-2 will persist.
  - scenario: A profile that carries the HF token (instead of huggingface_hub.login) has its token rotated: the Role-9 fingerprint flips, every diarized video is marked stale, and a needless corpus-wide diarization reprocess is triggered. Safe path: add token/hf_token (auth keys) to _SPEED_ONLY_LOAD_KEYS or strip credential-shaped keys before hashing.
- **minor** `src/va/provenance.py:65` — The documented default-vocab fold path (DEFAULT_INGEST_* caught when classes/actions are unset or the role is unconfigured) has no test; all vocab tests set the vocab explicitly.
  - scenario: A refactor gates the vocab fold on the role being configured or on the key being set; the suite stays green, and a later DEFAULT_INGEST_ACTIONS edit no longer changes the Role-7 fingerprint — a missed stale on the default-config path. Safe path: add a test that an unset-vocab config's fingerprint tracks the DEFAULT_INGEST_* lists.

---

## Full review

Review complete. The commit under review is `01afa58` (PROV-1): a new `src/va/provenance.py` with `role_fingerprint(role, cfg)`, 13 offline tests in `tests/test_provenance.py`, and a plan-status update in `provenance-reprocess-plan.md`.

**Overall assessment: sound.** The conservative-by-exclusion design (hash everything in `load` except a small speed-only set) is the right direction for D1, it matches the plan's locked decision, the limitations are honestly documented in the module docstring, the plan status was updated in the same change, and the tests are pure-Config offline tests covering both stub-shaped and real-model-shaped configs (whisper checkpoint-in-`load.model`, yolo `conf`, pyannote `min_speakers`, vocab order-independence, speed-key invariance). No contract or schema changes, so no COORDINATION.md entry is owed yet (the table in PROV-2 will owe one). I could not execute the test suite (sandbox denies pytest); tests were verified by reading.

Three minor findings, none blocking:

1. **The "unknown knobs are salient by default" invariant only holds for profile `load` keys, not roles.yaml role-level keys** (`src/va/provenance.py:61-74`). `Config.role()` (configuration.py:43-57) extracts only `backend`/`model`/`endpoint`/`load` and drops every other role-level key — and roles.yaml is exactly where output-affecting knobs already live by established pattern (`classes:`, `actions:`, handled here only by two hardcoded special cases). A third role-level knob added later (e.g. a scene-detector threshold or a new vocab key) would be silently invisible — a *missed* stale, the one failure mode the module promises never happens. Safe path: fold all of `cfg.roles[role]` except `backend`/`endpoint` into the blob (namespaced, e.g. `role.<key>`), keeping the special cases only for the defaults-fold when vocab is unset.

2. **Auth keys are treated as output-affecting** (`src/va/provenance.py:37-39`). `pyannote_inproc` reads `load.get("token")`/`hf_token`; if a profile ever carries the HF token, rotating it flips the Role-9 fingerprint and marks every diarized video stale — needless reprocess churn — and feeds secret material into a value PROV-2 will persist. Low exposure today (auth goes through `huggingface_hub.login`), but the exclusion set is the designed home for it: add `token`/`hf_token` as auth keys, or strip credential-shaped keys before hashing.

3. **The claimed default-vocab path has zero coverage** (`src/va/provenance.py:65-66`). The comment asserts a `DEFAULT_INGEST_*` edit is caught "even when the role leaves `classes`/`actions` unset (and even unconfigured)", but every vocab test sets the vocab explicitly, and the unconfigured-role test uses `speech_to_text` (no vocab branch). A regression gating the vocab fold on `role in cfg.roles` would pass the suite. Safe path: one test asserting `role_fingerprint("object_detector", _cfg({}))` differs when `DEFAULT_INGEST_CLASSES` is monkeypatched (or that unset-vocab equals default-vocab configs).

Suspicions checked and dissolved: `retriever.min_rerank`/`min_cosine` are query-time-only and the retriever isn't a provenance role (D6); dim is derivable from model+weights and stored separately per PROV-2; profile-global keys folding into every role's `load` only fails toward false-stale; `load["model"]` vs role model id collision is correctly namespaced; lazy registry imports avoid a circular import with `configuration`.

Verdict: **approve** — minor findings only.

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "src/va/provenance.py", "line": 61, "issue": "The salient-by-default invariant covers only profile `load` keys; roles.yaml role-level keys are dropped by Config.role() and reach the fingerprint only via the two hardcoded classes/actions special cases.", "scenario": "A future role-level output knob following the established classes/actions pattern (e.g. a scene_detector threshold in roles.yaml) changes stored output but not the fingerprint — a missed stale, silently mixing old and new derived data, the exact failure the module documents as impossible. Safe path: hash all of cfg.roles[role] except backend/endpoint (namespaced), keeping the special cases only for the unset-vocab defaults fold."},
{"severity": "minor", "file": "src/va/provenance.py", "line": 37, "issue": "Credential keys (pyannote's `token`/`hf_token`) are not in the exclusion set, so they are treated as output-affecting and hashed into a fingerprint PROV-2 will persist.", "scenario": "A profile that carries the HF token (instead of huggingface_hub.login) has its token rotated: the Role-9 fingerprint flips, every diarized video is marked stale, and a needless corpus-wide diarization reprocess is triggered. Safe path: add token/hf_token (auth keys) to _SPEED_ONLY_LOAD_KEYS or strip credential-shaped keys before hashing."},
{"severity": "minor", "file": "src/va/provenance.py", "line": 65, "issue": "The documented default-vocab fold path (DEFAULT_INGEST_* caught when classes/actions are unset or the role is unconfigured) has no test; all vocab tests set the vocab explicitly.", "scenario": "A refactor gates the vocab fold on the role being configured or on the key being set; the suite stays green, and a later DEFAULT_INGEST_ACTIONS edit no longer changes the Role-7 fingerprint — a missed stale on the default-config path. Safe path: add a test that an unset-vocab config's fingerprint tracks the DEFAULT_INGEST_* lists."}
]}
```
