# Agent review — request_changes

date: 2026-08-08T00:33:38.200312
range: origin/main..HEAD
branch: loop/r11b-relevance-floor
findings: 2

- **major** `src/va/pipeline/retrieval.py:531` — The new mixed-domain warning triggers on `gmap.domains`, which is derived only from videos present in the candidate pool — but cross-domain burial is precisely what removes the weaker domain from that pool, so the note goes silent in the total-burial case it was added to cover.
  - scenario: `va watch` into `.va-shots` (already holding A-EV videos), then `va ask "was anyone at the door at 12:30?"` with a visual-only plan: at k=5 every visual slot goes to edited-video frames (0.11-0.18) and no NVR clip (0.020-0.077) becomes an EvidenceItem, so gmap.domains == {"generic"}, `len(...) > 1` is false, and the CLI/web/reasoner see no note at all while the answer is built entirely on the wrong footage. test_a_mixed_domain_pool_is_flagged_in_the_evidence cannot catch it — the stub scores both synthetic clips identically, so burial structurally cannot occur there.
- **minor** `COORDINATION.md:631` — Committed docs and all three shipped run-*/config security profiles cite files that are not tracked in git — `architecture-evolution-loop.md` (the backlog for the four known gaps) and `security_footage_golden_truth_august_1_2026_noon_to_2pm` (the ground truth behind the FLAGGED min_cosine: 0.0).
  - scenario: Someone re-measures the floor after a visual-embedder swap (the entry's own revisit trigger (a)), follows the profile comment to the named ground-truth file, and finds nothing — the magic value's provenance is unverifiable and the deferred-gap list invisible to anyone who clones the repo.

---

## Full review

## Review — `origin/main..HEAD` (`de8557d`, R11.b per-footage-domain relevance floor)

**Scope:** 1 commit, 10 files — `src/va/pipeline/retrieval.py`, four `profiles/footage/security.yaml` copies, `config/roles.yaml`, `tests/test_relevance_gate_profile.py`, CLAUDE.md / COORDINATION.md / `video-analytics-model-analysis.md`.

**Verified before reporting**
- Full offline suite green here: `712 passed, 2 skipped in 106s` (checked `pgrep` first — nothing else live). Targeted: `test_relevance_gate_profile.py` + `test_retrieval_e2e.py` = 18 passed.
- All three run-`*`/config `roles.yaml` really do declare `retriever:`, so the new footage overlay passes `load_config`'s unknown-role check; `config/roles.yaml` deliberately does not, and `_load_footage_overlay` confirms declaring it there would raise — the omission in `config/profiles/footage/security.yaml` is correct.
- `retriever` is not in `PROVENANCE_ROLES`, so the new overlay key does not flip security-profile videos stale.
- `Video.source_type` is a `str`-Enum (`.value` is real, the `stype is None` branch is defensive, not dead); `Catalog.get_many` keys on the stored `id` string, so `str(vid)` matches `str(it.video_id)`.
- `ask.py:222` calls `retrieve()` with no explicit gate, so the per-video path really does engage in the live path; nothing outside `retrieval.py` reads `attributes["fusion"]["gate"]`, so the dict→list shape change breaks no consumer.
- The regression test genuinely reproduces the original failure (base floor `1.01` above the stub's achievable `1.0`), and the corrupt-catalog test really trips the `except` (non-sqlite bytes).
- No test deleted or weakened. Commit subject is a provisional `need_agent_review:` (exempt from rule 8); the body is plain description with `(R11.b)` trailing.
- The prior round's findings (`reviews/…-bd788de.md`) are addressed: the CLAUDE.md `min_cosine: 0.0` claim is now scoped to `run-*/config`, and the domain-blind gather/fusion major was answered via its safe-path option 2 (narrow the claim + document). My finding below is about the *new* mitigation added in that round, not a repeat.

---

### Findings

**major — `src/va/pipeline/retrieval.py:531`** (root cause at `gates_by_video`, `:202` / `:237`)

The new mixed-domain warning triggers on `gmap.domains`, and `gmap.domains` is built only from videos that appear in `ev.items` — the post-gather candidate pool. But burial *removes* the weaker domain from that pool. So the warning is loudest when the problem is mild (both domains partially represented) and completely silent in the total-burial case, which is the exact scenario it was added to cover and which `gates_by_video`'s own LIMIT docstring describes: "A-EV frames (relevant 0.11-0.18) outrank every A-LSSRVF frame (0.020-0.077), so in a mixed workdir the static-camera clips are buried BEFORE the gate."

This matters because the note is a real signal channel, not just a comment: `Evidence.notes` reaches the CLI (`cli.py:44`), the web job payload (`web/jobs.py:356`), and the reasoner prompt (`adapters/reasoner/prompts.py:132`). A user who has seen the note fire will read its absence as "single-domain pool, fine."

*Failure scenario:* `va watch` into `.va-shots` (which already holds the A-EV videos `va serve` is pointed at), then `va ask "was anyone at the door at 12:30?"` with a visual-only plan (`needs_caption_search` false). At `k=5` all visual slots go to edited-video frames at 0.11–0.18; no NVR clip becomes an `EvidenceItem`; `gmap.domains == {"generic"}`; `len(...) > 1` is false; no note is emitted, and the reasoner answers a security question off YouTube footage with nothing in the evidence saying the security clips were never considered. `test_a_mixed_domain_pool_is_flagged_in_the_evidence` cannot catch this — the stub scores both synthetic clips identically, so burial structurally cannot occur there.

*Safe path* (pick one):
1. Derive the domain set from the **workdir**, not the pool. `gates_by_video` already has a `Catalog` open — one extra `SELECT DISTINCT profile, source_type FROM videos` (or `catalog.list()`) gives the workdir's domains; flag when the workdir spans domains, and say something stronger when the workdir spans domains but the pool does not ("N footage profiles in this workdir; only *generic* reached the candidate pool"). Add a test that forces burial by scoring one video's frames below the other's rather than relying on the stub's tie.
2. If keeping the pool-scoped trigger, stop letting it read as a mixed-workdir guard: reword the note to describe only what it observed, drop the "keep domains in separate workdirs" advice from a check that cannot see the workdir, and state in CLAUDE.md that the flag detects a mixed *pool* and is silent when one domain is fully buried — with the burial case named in the R11.b known-gaps list alongside the other measured gaps.

---

**minor — `COORDINATION.md:631`** (also `run-{siglip,claude,qwen3vl}/config/profiles/footage/security.yaml:38`, `video-analytics-model-analysis.md:86`)

Three committed artifacts cite files that are not in the repo. `git ls-files` matches neither `architecture-evolution-loop.md` (where COORDINATION.md says the four known gaps are "backlogged … each with its measurement") nor `security_footage_golden_truth_august_1_2026_noon_to_2pm` (cited in all three shipped profile comments and in the model-analysis decision as the ground truth behind the FLAGGED `min_cosine: 0.0`). Both are untracked in the working tree.

*Failure scenario:* someone clones the repo to re-derive or re-measure the floor after a visual-embedder swap (revisit trigger (a) in the model-analysis entry). The profile comment tells them the value was measured against a named ground-truth file; the file does not exist for them, so the magic value's provenance is unverifiable and the deferred-gap list is invisible — which defeats the purpose of the flag-don't-hide rule the comment is otherwise complying with.

*Safe path:* commit both (or a redacted summary of the ground truth, if the annotations of real home-security footage are deliberately kept out of git — that is a reasonable reason, and the prior NVR work already noted these notes files as an untracked-hygiene gap). If privacy is the reason, say so at the citation site ("ground truth kept out of the repo; ask the maintainer") rather than pointing at a path that resolves for nobody. This is a placement question worth putting to the human in the digest, not a blocker on its own.

---

**Not reported** (checked and dropped): `fusion.gate` dict→list has no external consumer and is logged in COORDINATION.md; `trace(floors=[…])` breaks no trace assertion; the SR.6 base-config verify floor is unchanged from `main`, flagged in-code and backlogged with numbers; `stype is None` and bad-UUID paths are defended; `test_an_unresolvable_profile_falls_back_to_the_base_floors` does reach the `except` (a non-`generic` missing overlay raises `FileNotFoundError`); the commented-out `config/roles.yaml` recipe now carries the paired cross-reference the previous round asked for; and R11.b's "Done when" is met with numbers reported alongside the shortfall (13/25 at k=8), with the item still marked `[~]`.

**Verdict: request_changes** — one major.

```json
{"verdict": "request_changes", "findings": [{"severity": "major", "file": "src/va/pipeline/retrieval.py", "line": 531, "issue": "The new mixed-domain warning triggers on `gmap.domains`, which is derived only from videos present in the candidate pool — but cross-domain burial is precisely what removes the weaker domain from that pool, so the note goes silent in the total-burial case it was added to cover.", "scenario": "`va watch` into `.va-shots` (already holding A-EV videos), then `va ask \"was anyone at the door at 12:30?\"` with a visual-only plan: at k=5 every visual slot goes to edited-video frames (0.11-0.18) and no NVR clip (0.020-0.077) becomes an EvidenceItem, so gmap.domains == {\"generic\"}, `len(...) > 1` is false, and the CLI/web/reasoner see no note at all while the answer is built entirely on the wrong footage. test_a_mixed_domain_pool_is_flagged_in_the_evidence cannot catch it — the stub scores both synthetic clips identically, so burial structurally cannot occur there."}, {"severity": "minor", "file": "COORDINATION.md", "line": 631, "issue": "Committed docs and all three shipped run-*/config security profiles cite files that are not tracked in git — `architecture-evolution-loop.md` (the backlog for the four known gaps) and `security_footage_golden_truth_august_1_2026_noon_to_2pm` (the ground truth behind the FLAGGED min_cosine: 0.0).", "scenario": "Someone re-measures the floor after a visual-embedder swap (the entry's own revisit trigger (a)), follows the profile comment to the named ground-truth file, and finds nothing — the magic value's provenance is unverifiable and the deferred-gap list invisible to anyone who clones the repo."}]}
```
