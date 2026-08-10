# Agent review — approve

date: 2026-08-08T01:05:39.783077
range: origin/main..HEAD
branch: loop/r11b-relevance-floor
findings: 1

- **minor** `src/va/pipeline/retrieval.py:167` — GateMap's class docstring says the domains field spans the candidate 'pool', contradicting the shipped workdir-wide semantics stated by the field's own inline comment, gates_by_video's docstring, Catalog.footage_domains(), the evidence note, and the trace message.
  - scenario: A later session reads the class docstring as authoritative and 'simplifies' gates_by_video's return to frozenset(by_domain); all tests stay green except the one buried-domain assertion, which reads as over-specified and gets adjusted, reintroducing the total-burial silence where A-EV frames take every top-k slot, no NVR clip enters the pool, no note is emitted, and the reasoner answers a security question off edited footage.

---

## Full review

I reviewed the single commit in `origin/main..HEAD` (`d83de5a`, R11.b per-footage-domain relevance floor) — 11 files, `src/va/pipeline/retrieval.py` + `catalog_sqlite.py` + four `security.yaml` copies + a new 344-line test file + three docs.

## Verified before reporting (suspicions that dissolved)

- **Config-load safety across all four config dirs.** All three `run-*/config/roles.yaml` declare `retriever:` (lines 57/57/58), so the new footage overlay clears `load_config`'s unknown-role check (`configuration.py:218-223`). `config/roles.yaml` leaves it commented and `config/profiles/footage/security.yaml` correctly omits the override — declaring it there *would* raise; both files carry cross-referencing notes. `_load_footage_overlay`'s `enabled`/`GATEABLE_ROLES` validation doesn't touch a `retriever` overlay. Only two footage profiles exist (`generic`, `security`) in each of the four dirs; `generic.yaml` is `roles: {}`, so A-EV resolution is unchanged.
- **No `active_footage_profile` is set in any config dir**, so `gates[None]` (the base/fallback gate) really is the generic-profile gate — the "fall back to BASE floors, never permissive" comment at `retrieval.py:149-157` holds.
- **Key types line up.** `_to_row` stores `str(v.id)`; `get_many` keys on that string; `EvidenceItem.video_id` is `Optional[UUID]` and every `from_*` converter sets it (`evidence.py:61,72,82,95,110,122,139,159,169`), so `str(it.video_id)` matches and the `None` entry is a true fallback, not a silent catch-all.
- **`retriever` is not in `PROVENANCE_ROLES`** (`provenance.py:50-54`), so the new overlay key does not flip security-profile videos stale or trigger spurious reprocess work.
- **Resource/exception handling in `gates_by_video`** is correct: a raise in `Catalog(...)` leaves nothing open, a raise in `get_many`/`footage_domains` still hits the inner `finally: catalog.close()`, and both degrade to the base gate.
- **Blast radius of the changed contract.** `retrieve()` has exactly one production caller (`ask.py:222`); nothing outside `retrieval.py` reads `attributes["fusion"]["gate"]` or matches the gate note text, so the dict→dict-or-list shape change is contained — and it *is* logged in COORDINATION.md along with `Catalog.footage_domains()`.
- **Repo rules.** `min_cosine: 0.0` is an explicitly FLAGGED magic value with its measurement, its rejected alternatives (median+margin / max−margin / p75, each with recall cost), and revisit triggers recorded in three places; results are reported *alongside* ground truth (7/25 → 13/25 at k=8, 3/8 → 0/8 emptied), not as a green-suite claim. The `_gather` "KNOWN GAP" comment is accurate, not a rationalization — `verify_visual_hits` passes below-floor hits through *unchecked* (`verify.py:44-47`), so the base-config floor makes SR.6 a no-op on A-LSSRVF rather than emptying the lane, and that behavior is unchanged from `main`.
- **Test integrity.** Nothing deleted or weakened; the diff is additive. `test_security_footage_is_not_judged_by_the_a_ev_floor` uses a base floor of 1.01 that the stub can never clear, so it fails against the old single-gate code — it reproduces the bug rather than merely failing to import. The corrupt-DB test correctly notes that a *missing* workdir wouldn't exercise the except branch.
- **Combination coverage.** COORDINATION.md names both real-model golden runs (`.va-shots` 85/25/1, `.va-nvr -k nvr` 17 passed / 8 xfailed) and honestly states what they do *not* prove — no golden test reaches `retrieve()` on security footage — with `test_shipped_real_model_configs_carry_the_security_floor` named as the stopgap. That test hardcodes the three `run-*/config` names, but `tests/test_footage_settings.py:65` already does the same, so it follows an established reviewed convention, not a new gap.
- **Not verified by execution:** pytest was denied approval in this session, so pass status rests on reading.

## Finding

**minor — `src/va/pipeline/retrieval.py:167`** — `GateMap`'s class docstring still says the field is "the set of footage domains **the candidate pool** spans", contradicting the field's own inline comment (`WORKDIR`, "Do not 'simplify' this to `frozenset(by_domain)`"), `gates_by_video`'s docstring, `Catalog.footage_domains()`, the user-facing note, and the trace message — all of which were corrected to say *workdir*. The class docstring is the one place the fix didn't land.

*Failure scenario:* a later session opens `retrieval.py`, reads the class docstring as authoritative, and replaces the extra `SELECT DISTINCT` with `frozenset(by_domain)`. Every test stays green except the single buried-domain assertion at `test_relevance_gate_profile.py:202`, which reads like an over-specified test and gets adjusted. That restores the total-burial silence: in a mixed workdir A-EV frames (0.11–0.18) take every top-k slot, no NVR clip enters the pool, no note is emitted, and the reasoner answers a security question off edited footage with nothing saying the security clips were never considered.

*Safe path:* change the first line to "…plus the set of footage domains present in the WORKDIR", matching the note text and the inline comment two lines below.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/retrieval.py", "line": 167, "issue": "GateMap's class docstring says the domains field spans the candidate 'pool', contradicting the shipped workdir-wide semantics stated by the field's own inline comment, gates_by_video's docstring, Catalog.footage_domains(), the evidence note, and the trace message.", "scenario": "A later session reads the class docstring as authoritative and 'simplifies' gates_by_video's return to frozenset(by_domain); all tests stay green except the one buried-domain assertion, which reads as over-specified and gets adjusted, reintroducing the total-burial silence where A-EV frames take every top-k slot, no NVR clip enters the pool, no note is emitted, and the reasoner answers a security question off edited footage."}]}
```
