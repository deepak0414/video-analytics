# Agent review — approve

date: 2026-08-08T00:46:35.283245
range: origin/main..HEAD
branch: loop/r11b-relevance-floor
findings: 2

- **minor** `src/va/storage/structured/catalog_sqlite.py:122` — footage_domains() counts videos rows of any ingest_status, so a failed ingest that produced no vectors still registers as a footage domain and permanently enables the mixed-domain warning.
  - scenario: A single failed `va ingest "nvr://..."` in .va-shots (get_or_create + set_profile run before fetch() fails) leaves a profile='security' row; every subsequent `va ask` in that workdir appends 'workdir spans 2 footage profiles (generic, security) ... may be under-represented or absent' to Evidence.notes, which reaches the CLI, the web job payload and the reasoner prompt, though no security frame is indexed and nothing is being buried.
- **minor** `CLAUDE.md:326` — CLAUDE.md says `retrieve` flags a mixed 'pool' while the shipped code deliberately flags a mixed WORKDIR — the exact distinction the final round of this branch introduced.
  - scenario: A later session reads CLAUDE.md, reverts gates_by_video to frozenset(by_domain) as a simplification, and reintroduces the total-burial silence: in a mixed workdir where A-EV frames (0.11-0.18) take every top-k slot, no NVR clip enters the pool, no note is emitted, and the reasoner answers a security question off edited-video footage with nothing saying the security clips were never considered.

---

## Full review

## Review — `origin/main..HEAD` (`3a8e542`, R11.b per-footage-domain relevance floor)

**Scope:** 1 commit, 11 files — `src/va/pipeline/retrieval.py`, `src/va/storage/structured/catalog_sqlite.py`, four `profiles/footage/security.yaml` copies, `config/roles.yaml`, `tests/test_relevance_gate_profile.py`, CLAUDE.md / COORDINATION.md / `video-analytics-model-analysis.md`.

**Verified before reporting**

- All three `run-*/config/roles.yaml` declare `retriever:`, so the new footage overlay passes `load_config`'s unknown-role check (`configuration.py:218`); `config/roles.yaml` deliberately does not, and the overlay-loader path confirms declaring it there would raise `KeyError` — the omission in `config/profiles/footage/security.yaml` is correct and the paired NB comments in both files now cross-reference each other.
- `_load_footage_overlay`'s extra validations (`enabled` type, `GATEABLE_ROLES`) don't reject a `retriever` overlay — it carries no `enabled` key.
- `retriever` is not in `PROVENANCE_ROLES` (`provenance.py:50`), so the new overlay key does not flip security-profile videos stale.
- `Catalog.get_many` keys on the stored id string and `_to_row` stores `str(v.id)`; `EvidenceItem.video_id` is a `UUID` — so `str(it.video_id)` matches the gate-map keys. Every `from_*` converter sets `video_id`, so the `None` fallback gate is genuinely a fallback.
- `workdir_domains` cannot be unbound at the final `return` (both statements sit inside the same `try`), and `default_footage_profile` is imported before use.
- The `_gather` SR.6 "KNOWN GAP" comment is accurate, not a rationalization: `verify_visual_hits` (`verify.py:68`) passes below-floor hits through **unchecked** rather than dropping them, so the base-config floor makes verification a no-op on A-LSSRVF rather than emptying the lane. Unchanged from `main`.
- The prior round's major finding (domains derived from the surfaced pool, silent under total burial) is genuinely fixed via `Catalog.footage_domains()` + the workdir-scoped `frozenset`, and `test_a_mixed_domain_pool_is_flagged_in_the_evidence` now forces the burial case explicitly rather than relying on the stub's score tie.
- Nothing outside `retrieval.py` reads `attributes["fusion"]["gate"]` (checked `.py`/`.js`/`.html`/web layer), so the dict→list shape change breaks no consumer; it is logged in COORDINATION.md along with the note-text change.
- No test deleted or weakened. Commit subject is a provisional `need_agent_review:` (rule 8 exempt); the body is self-sufficient prose with `(R11.b)` trailing only.
- The `6/26` vs `7/25` denominators across the docs are not an inconsistency — 26 is clip×query pairs over 9 gate-level queries, 25 is over the 8 end-to-end questions.

**Not verified:** I could not execute pytest (bash approval denied in this session), so test-pass status rests on reading the tests plus the prior round's recorded full-suite run on `de8557d`; the delta since then is ~30 lines.

---

### Findings

**minor — `src/va/storage/structured/catalog_sqlite.py:122`**

`footage_domains()` selects over every `videos` row regardless of `ingest_status`, but only `done` videos contribute vectors to a candidate pool. A row that never produced a frame therefore counts as a footage domain and permanently turns on the mixed-domain warning.

*Failure scenario:* a user runs `va ingest "nvr://1/2026-08-01T12:00:00/..."` once in `.va-shots` with `VA_NVR_HOST` unset. `NvrSource.resolve()` is pure URI parsing, so `catalog.get_or_create()` (`ingest.py:216`) and `set_profile(..., "security")` (`ingest.py:249`) both run *before* `fetch()` fails at `ingest.py:719`. The failed row persists with `profile='security'`, `source_type='nvr_recorded'`. From then on every `va ask` in that workdir appends `"workdir spans 2 footage profiles (generic, security); … lower-scoring footage may be under-represented or absent"` to `Evidence.notes`, which reaches the CLI, the web job payload, and the reasoner prompt (`prompts.py:132`) — while zero security frames are indexed and nothing is being buried. The only remedy is `va remove` on a video that was never ingested.

*Safe path:* add `WHERE ingest_status = 'done'` to the `SELECT DISTINCT` — a row that never finished contributed no vectors and so cannot bury anything — and extend `test_a_mixed_domain_pool_is_flagged_in_the_evidence` with a failed-status row that must **not** raise the flag. This errs in the safe direction today (over-warning, not under-warning), which is why it is minor rather than major.

---

**minor — `CLAUDE.md:326`**

The prose still says "`retrieve` flags a mixed **pool** in the evidence notes", but the shipped behavior is deliberately workdir-scoped — `gates_by_video`'s own docstring says "flags a mixed WORKDIR … deliberately the workdir and not the surfaced pool, since total burial would otherwise silence the warning". That distinction is exactly what the final round of this branch changed, and the note text itself reads "workdir spans N footage profiles".

*Failure scenario:* a later session reads CLAUDE.md, concludes the flag is pool-derived, and "simplifies" `gates_by_video` back to `frozenset(by_domain)` (one less SQL query, same tests green except the one buried-domain assertion) — reintroducing the silence in the total-burial case that this branch spent a review round closing.

*Safe path:* change "mixed pool" to "mixed workdir" in the same sentence, and optionally add the one clause that explains why ("…because the buried domain never reaches the pool").

---

**Not reported** (checked and dropped): the `if not vids: return GateMap(gates)` early return also skips the workdir warning, but it only fires on a wholly empty candidate pool, which already emits its own louder note; `applied_gates` being computed pre-gate is the correct reading of "floors that covered a candidate"; `-inf` in the `fusion.gate` payload and the `trace(floors=[…])` kwarg are both unchanged in kind from `main`; the `stype is None` and bad-UUID branches are defensive against a non-null enum column, not dead-in-a-harmful-way; the SR.6 base-config verify floor, `_minmax` noise amplification, action-lane flooding and `query_objects` word-matching are all pre-existing, measured, and backlogged with numbers; R11.b's "Done when" is met with the shortfall reported alongside (13/25 at k=8, 0/8 emptied) and the plan item still marked `[~]`; combination attestation names both real-model golden runs plus the offline shipped-config parity test, and honestly states that no golden test reaches `retrieve()` on security footage.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/storage/structured/catalog_sqlite.py", "line": 122, "issue": "footage_domains() counts videos rows of any ingest_status, so a failed ingest that produced no vectors still registers as a footage domain and permanently enables the mixed-domain warning.", "scenario": "A single failed `va ingest \"nvr://...\"` in .va-shots (get_or_create + set_profile run before fetch() fails) leaves a profile='security' row; every subsequent `va ask` in that workdir appends 'workdir spans 2 footage profiles (generic, security) ... may be under-represented or absent' to Evidence.notes, which reaches the CLI, the web job payload and the reasoner prompt, though no security frame is indexed and nothing is being buried."}, {"severity": "minor", "file": "CLAUDE.md", "line": 326, "issue": "CLAUDE.md says `retrieve` flags a mixed 'pool' while the shipped code deliberately flags a mixed WORKDIR — the exact distinction the final round of this branch introduced.", "scenario": "A later session reads CLAUDE.md, reverts gates_by_video to frozenset(by_domain) as a simplification, and reintroduces the total-burial silence: in a mixed workdir where A-EV frames (0.11-0.18) take every top-k slot, no NVR clip enters the pool, no note is emitted, and the reasoner answers a security question off edited-video footage with nothing saying the security clips were never considered."}]}
```
