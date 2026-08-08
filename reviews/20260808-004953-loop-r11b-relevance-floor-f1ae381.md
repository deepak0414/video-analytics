# Agent review — approve

date: 2026-08-08T00:57:01.111307
range: origin/main..HEAD
branch: loop/r11b-relevance-floor
findings: 2

- **minor** `src/va/pipeline/retrieval.py:167` — GateMap's class docstring and the gate:mixed-domain trace message both say the domains come from the candidate 'pool', while the shipped behavior is deliberately workdir-wide — the same drift the previous round fixed in CLAUDE.md, left in the two places nearest the code.
  - scenario: A later session reads the class docstring as authoritative and 'simplifies' gates_by_video to frozenset(by_domain); all tests stay green except the one buried-domain assertion, reintroducing the total-burial silence where A-EV frames take every top-k slot, no NVR clip enters the pool, no note is emitted, and the reasoner answers a security question off edited footage. An operator also sees a trace line claiming the pool spans two domains when the pool may hold only one.
- **minor** `COORDINATION.md:593` — The R11.b log entry documents the retrieval.py interface changes but omits the new public storage method Catalog.footage_domains(), breaking the file's convention of logging every new Catalog method (Catalog.list, set_start_epoch, set_camera).
  - scenario: The web agent, seeing no such method in the append-only log, adds its own workdir-domain helper without the ingest_status='done' restriction; the UI then labels a workdir mixed on failed-ingest rows the retriever deliberately ignores, and the two definitions of 'footage domain' silently diverge.

---

## Full review

# Review — `origin/main..HEAD` (`f1ae381`, R11.b per-footage-domain relevance floor)

**Scope:** 1 commit, 11 files — `src/va/pipeline/retrieval.py`, `src/va/storage/structured/catalog_sqlite.py`, four `profiles/footage/security.yaml` copies, `config/roles.yaml`, `tests/test_relevance_gate_profile.py`, plus CLAUDE.md / COORDINATION.md / `video-analytics-model-analysis.md`.

## Verified before reporting

- **Config-load safety across all four config dirs.** All three `run-*/config/roles.yaml` declare `retriever:` (line 57/57/58), so the new footage overlay clears `load_config`'s unknown-role check (`configuration.py:218`). `config/roles.yaml` deliberately leaves it commented and `config/profiles/footage/security.yaml` correctly omits the override — declaring it there *would* raise `KeyError`, and both files now carry cross-referencing NB comments. `_load_footage_overlay`'s `enabled`/`GATEABLE_ROLES` validations don't touch a `retriever` overlay. Only four `security.yaml` files exist; all four are accounted for.
- **The unresolvable-profile fallback is genuinely exercised.** `_load_footage_overlay` raises `FileNotFoundError` for any missing non-`generic` name (`configuration.py:72-78`), so `test_an_unresolvable_profile_falls_back_to_the_base_floors` really does drive the new `except` branch in `get_relevance_gate`, not a tolerated-missing-file path.
- **Key types line up.** `Catalog.get_many` keys on the stored id *string* and `_to_row` stores `str(v.id)`; `EvidenceItem.video_id` is `Optional[UUID]` (`evidence.py:35`), so `str(it.video_id)` matches the gate-map keys. Every `from_*` converter sets `video_id`, so the `None` entry is a true fallback.
- **`retriever` is not in `PROVENANCE_ROLES`** (`provenance.py:50-54`), so the new overlay key does not flip security-profile videos stale or trigger spurious `va reprocess` work.
- **No config caching.** `load_config` re-reads YAML every call and `_config_dir()` reads `VA_CONFIG_DIR` fresh, so `test_shipped_real_model_configs_carry_the_security_floor`'s `monkeypatch.setenv` loop genuinely checks three distinct dirs rather than silently re-asserting the first.
- **The `_gather` "KNOWN GAP" comment is accurate, not a rationalization.** `verify_visual_hits` (`verify.py:69-71`) passes below-`floor` hits through *unchecked* rather than dropping them, so the base-config floor makes SR.6 a no-op on A-LSSRVF instead of emptying the lane. Behavior unchanged from `main`.
- **No downstream string-matching on the changed note/attribute.** Nothing outside `retrieval.py` reads `attributes["fusion"]["gate"]` (checked `.py`/`.js`/`.html`/`.ts` repo-wide), and neither `ask.py` nor `deep_scan.py` matches on the gate note text — `deep_scan.py:403` falls back via `primary_video_id`, which is computed pre-gate. The single-gate note string is byte-identical to the old format.
- **Both prior-round minors are actually fixed**, and correctly: `footage_domains()` now filters `ingest_status = 'done'` (matching `IngestStatus.done.value`), with `test_a_failed_ingest_does_not_create_a_footage_domain` reproducing the original failure; CLAUDE.md now says "mixed **workdir**" with the don't-simplify-it-back rationale.
- **Test integrity.** No test deleted or weakened — the one removed line (`assert edited.id`) was filler displaced by splitting the failed-ingest case into its own test, and `test_a_mixed_domain_pool_is_flagged_in_the_evidence` still uses `edited` for the burial assertion. `test_security_footage_is_not_judged_by_the_a_ev_floor` fails against the old code (base floor 1.01 would drop every visual item), so it reproduces the bug rather than merely failing to import.
- **Repo-rule compliance.** `min_cosine: 0.0` is an explicitly FLAGGED magic value with its measurement, its rejected alternatives, and its revisit triggers recorded in three places; results are reported *alongside* the ground truth (13/25 at k=8, 0/8 emptied), not as a green-suite claim. Combination attestation in COORDINATION.md names both real-model golden runs and honestly states that *no* golden test reaches `retrieve()` on security footage, with the offline parity test named as the stopgap.
- **Not verified by execution:** pytest was denied approval in this session, so pass status rests on reading the tests plus the recorded suite run at the prior branch state; the delta since then is ~45 lines.

## Findings

**minor — `src/va/pipeline/retrieval.py:167`**

`GateMap`'s class docstring says the field is "the set of footage domains **the candidate pool** spans", and the trace event at `retrieval.py:548` emits `"pool spans N footage profiles"` — but the shipped semantics is workdir-wide, as the field's own inline comment, `gates_by_video`'s docstring, `Catalog.footage_domains()` and the user-facing note ("workdir spans N footage profiles") all state. This is the same drift the last round closed in CLAUDE.md, left behind in the two places closest to the code.

*Failure scenario:* a later session opens `retrieval.py`, reads the class docstring as authoritative, and "simplifies" `gates_by_video`'s return to `frozenset(by_domain)` — one fewer SQL query, and every test stays green except the single buried-domain assertion, which reads like an over-specified test. That reintroduces the total-burial silence: in a mixed workdir where A-EV frames (0.11–0.18) take every top-k slot, no NVR clip enters the pool, no note is emitted, and the reasoner answers a security question off edited footage with nothing saying the security clips were never considered. Separately, an operator debugging a live mixed-workdir warning sees a trace line claiming the *pool* spans two domains while the pool may contain only one.

*Safe path:* change the class docstring to "…the set of footage domains present in the WORKDIR" and the trace message to `workdir spans …`, matching the note text.

---

**minor — `COORDINATION.md:593`**

The R11.b log entry documents the `pipeline/retrieval.py` interface changes in detail but never names the new public storage method `Catalog.footage_domains()`. The file's own convention is to log every new `Catalog` method (`Catalog.list()` 2026-06-10, `Catalog.set_start_epoch` 2026-08-03, `Catalog.set_camera` 2026-08-xx), and `Catalog` is a row in the web-layer contract table.

*Failure scenario:* the web agent adds its own workdir-domain helper (or a differently-scoped `footage_domains`) because the append-only log shows no such method, and the two diverge on the `ingest_status = 'done'` restriction — the web UI then labels a workdir as mixed on rows the retriever deliberately ignores. Low blast radius since the addition is purely additive and unconsumed by `src/va/web/` today, which is why this is minor.

*Safe path:* add one clause to the existing R11.b entry: `Catalog.footage_domains() -> set[(profile, source_type)]` over `ingest_status='done'` rows only, additive, no schema change.

---

**Not reported** (checked and dropped): the `if not vids: return GateMap(gates)` early return also skips the workdir warning, but it fires only on a wholly empty candidate pool, which already emits its own louder signal; `applied_gates` being computed pre-gate is the correct reading of "floors that covered a candidate", and `applied_gates or [base]` correctly reports the explicit gate on an empty pool; `-inf` in the `fusion.gate` payload and the `trace(floors=[…])` kwarg are unchanged in kind from `main`; the per-retrieve `SELECT DISTINCT` full scan plus N+1 YAML reads are negligible against an LLM round-trip and no perf rule is in scope; `security`'s `min_cosine: 0.0` effectively disables visual gating for A-LSSRVF, but that is the measured, explicitly-flagged trade with precision deferred to R11.c and SR.6 (both recorded with numbers); the R11.b "Done when" is met with the shortfall reported alongside ground truth and the plan item still marked `[~]`; the commit subject is a provisional `need_agent_review:` (rule 8 exempt) and its body is self-sufficient prose with `(R11.b)` trailing only.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/retrieval.py", "line": 167, "issue": "GateMap's class docstring and the gate:mixed-domain trace message both say the domains come from the candidate 'pool', while the shipped behavior is deliberately workdir-wide — the same drift the previous round fixed in CLAUDE.md, left in the two places nearest the code.", "scenario": "A later session reads the class docstring as authoritative and 'simplifies' gates_by_video to frozenset(by_domain); all tests stay green except the one buried-domain assertion, reintroducing the total-burial silence where A-EV frames take every top-k slot, no NVR clip enters the pool, no note is emitted, and the reasoner answers a security question off edited footage. An operator also sees a trace line claiming the pool spans two domains when the pool may hold only one."}, {"severity": "minor", "file": "COORDINATION.md", "line": 593, "issue": "The R11.b log entry documents the retrieval.py interface changes but omits the new public storage method Catalog.footage_domains(), breaking the file's convention of logging every new Catalog method (Catalog.list, set_start_epoch, set_camera).", "scenario": "The web agent, seeing no such method in the append-only log, adds its own workdir-domain helper without the ingest_status='done' restriction; the UI then labels a workdir mixed on failed-ingest rows the retriever deliberately ignores, and the two definitions of 'footage domain' silently diverge."}]}
```
