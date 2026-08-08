# Agent review — request_changes

date: 2026-08-07T23:34:16.486760
range: origin/main..HEAD
branch: loop/r11b-relevance-floor
findings: 2

- **major** `src/va/pipeline/retrieval.py:261` — The gate is per-footage-domain but the candidate gather and fusion above it are not: `_gather` takes ONE global top-k from `visual_query` and `_fuse` min-maxes `score` across the whole visual lane, both on the cosine scale this commit proves is domain-incomparable — so in the mixed workdir the docstring and CLAUDE.md advertise, A-LSSRVF frames never reach the gate at all.
  - scenario: Run `va watch` into a workdir that already holds A-EV videos (e.g. the .va-shots the web UI serves), then `va ask "was anyone at the door?"`. Per this commit's own measurements (A-EV relevant 0.11-0.18 vs A-LSSRVF spread 0.020-0.077), all k=5 visual slots go to edited-video frames, `gates_by_video` never applies min_cosine 0.0 to any NVR clip, and the answer is built on the wrong video. Not covered by test_gates_by_video_maps_each_video_to_its_own_domain, where the stub scores both videos ~1.0 so the burial cannot appear, nor by the .va-nvr measurements, which are single-domain.
- **minor** `CLAUDE.md:321` — "`security` sets `min_cosine: 0.0`" holds only in run-*/config; the default `config/` dir's security profile has no `retriever:` block and cannot have one, so the sentence describes a setting a reader will not find where the surrounding prose points them.
  - scenario: Someone running the default stub config reads this line, assumes security footage is gated at 0.0, and debugs an unrelated cause for unexpected results when that config dir performs no thresholding whatsoever.

---

## Full review

## Review — `origin/main..HEAD` (`bd788de`, R11.b relevance floor)

**Scope reviewed:** 1 commit, 10 files (`src/va/pipeline/retrieval.py`, 4 config dirs, 1 new test file, CLAUDE.md / COORDINATION.md / model-analysis).

**Verified before reporting:**
- Full offline suite green here: `710 passed, 2 skipped in 103s` (no other pytest was live — checked `pgrep` first).
- `test_relevance_gate_profile.py` + `test_retrieval_e2e.py`: 16 passed. The R11.b regression test genuinely reproduces the original failure (base floor `1.01` > the stub's achievable `1.0`, so only per-video resolution saves the items), and the corrupt-catalog test really trips the `except` (non-sqlite bytes, not just a missing dir).
- The three minors from `reviews/…-5ab61bb.md` and the three from `…-f3f305b.md` are all genuinely fixed at this HEAD (keyword-only params, base-floor fallback + warn trace on an unresolvable profile, the `config/roles.yaml` cross-reference, the A-EV golden attestation). I re-checked each rather than assuming.
- `_load_footage_overlay` really does `raise KeyError` for an overlay role absent from `roles.yaml` — so omitting `retriever:` from `config/profiles/footage/security.yaml` is correct, not an oversight.
- `verify.py:69` confirms the author's `_gather` comment is accurate (`h.score < floor` → passes through unchecked), and `stale.py:42` uses a fixed `PROVENANCE_ROLES` list, so adding `retriever` to a footage overlay does not flip videos stale.
- COORDINATION.md's self-reported coverage gap is truthful: `test_golden_queries.py:111-116` calls `visual_query()` directly and never reaches `retrieve()`, and `ask_questions:` appears only in the two A-EV fixtures, not any NVR one.
- No test was deleted or weakened; no consumer outside `retrieval.py` reads `attributes["fusion"]["gate"]`, and the shape change is logged in COORDINATION.md.
- Commit body is plain description with `(R11.b)` trailing — subject is a provisional `need_agent_review:` (exempt).
- `plan.md`'s SR.5 row is stale w.r.t. this change, but `git log -- plan.md` shows it has been untouched since the initial commit — that's the repo's convention, not a gap in this diff. Not reported.

---

### Findings

**major — `src/va/pipeline/retrieval.py:261`** (with `_fuse` at `:442-445`, docstring claim at `:171-174`, CLAUDE.md:318-321)

The gate is now per-domain, but everything *upstream* of it still treats one cosine scale as global. `_gather` pulls visual candidates with a single `visual_query(terms, workdir=workdir, k=k)` — one top-k over the whole sharded index — and `_fuse` then min-maxes `it.score` across the entire visual lane, all videos together. Both operate on exactly the quantity this commit proves is not comparable across footage domains.

The consequence lands precisely on the configuration `gates_by_video`'s own docstring invokes as the motivation ("one workdir can hold both domains — `va watch` writes A-LSSRVF chunks beside whatever else is there") and that CLAUDE.md:318-321 now advertises ("one workdir may hold A-EV and A-LSSRVF videos and each is judged on floors calibrated for its own footage"). The gate half of that sentence is true; the retrieval half is not.

*Failure scenario:* run `va watch` into `.va-shots` (the workdir the memory-documented `va serve` default already holds A-EV videos in), then `va ask "was anyone at the door?"`. Using this commit's own measured distributions — A-EV relevant frames 0.11–0.18, A-LSSRVF per-query spread 0.020–0.077 — all `k=5` (ask's default) visual slots are taken by edited-video frames. The NVR clips never become `EvidenceItem`s, so `gates_by_video` never gets to apply `min_cosine: 0.0` to them, and the answer is built entirely on the wrong video. Even at larger `k`, `_fuse`'s shared min-max normalizes every A-LSSRVF frame toward 0 against an A-EV maximum, so `MAX_ITEMS`/keyframe selection buries whatever does get through.

This is not a regression against `main` (pre-R11.b those items were dropped by the 0.10 floor anyway), which is why it's major rather than critical — but it is an unmeasured combination presented as working. All numbers in the commit, the profile comments, and `video-analytics-model-analysis.md` come from `.va-nvr`, a single-domain workdir; the only mixed-domain coverage is `test_gates_by_video_maps_each_video_to_its_own_domain`, where the stub scores both videos identically (~1.0) so cross-domain burial structurally cannot appear. That is the pattern rule 6 exists to catch: behavior that varies by combination, exercised only where the stub makes the combination invisible.

*Safe path:* pick one —
1. Gather per video or per footage domain (a per-video `k`, or a domain quota in `_gather`) so each domain contributes candidates before the shared rank/gate, and min-max `native_norm` within a domain rather than across the whole visual lane; or
2. If that is out of scope for this item (defensible — it is a recall change, not a gate change), narrow the claim: say in the `gates_by_video` docstring and CLAUDE.md that per-domain floors are measured on single-domain workdirs and that mixed-domain *retrieval* is still cross-domain-normalized, and add "cross-domain gather/rank uses one cosine scale" to the R11.b known-gaps list in the loop plan alongside the other four, with the same measurement rigor.

---

**minor — `CLAUDE.md:321`**

"(`security` sets `min_cosine: 0.0`; measured, see the profile comment)" is only true in `run-*/config`. In the default `config/` dir the `security` profile has no `retriever:` block at all and *cannot* have one (base `roles.yaml` omits the role, so declaring it raises `KeyError` at load). A reader following the surrounding prose — which is about `config/profiles/footage/<name>.yaml` — opens that file and finds no such setting.

*Failure scenario:* someone operating the default stub config reads CLAUDE.md, assumes A-LSSRVF footage is being judged at 0.0, and debugs an unrelated cause when in fact no thresholding happens at all in that config dir. The NB inside `config/profiles/footage/security.yaml` does eventually explain this, but only if they get there.

*Safe path:* one clause — "(`security` sets `min_cosine: 0.0` **in `run-*/config`**; the stub config does no thresholding at all)".

---

**Verdict: request_changes** — one major.

```json
{"verdict": "request_changes", "findings": [{"severity": "major", "file": "src/va/pipeline/retrieval.py", "line": 261, "issue": "The gate is per-footage-domain but the candidate gather and fusion above it are not: `_gather` takes ONE global top-k from `visual_query` and `_fuse` min-maxes `score` across the whole visual lane, both on the cosine scale this commit proves is domain-incomparable — so in the mixed workdir the docstring and CLAUDE.md advertise, A-LSSRVF frames never reach the gate at all.", "scenario": "Run `va watch` into a workdir that already holds A-EV videos (e.g. the .va-shots the web UI serves), then `va ask \"was anyone at the door?\"`. Per this commit's own measurements (A-EV relevant 0.11-0.18 vs A-LSSRVF spread 0.020-0.077), all k=5 visual slots go to edited-video frames, `gates_by_video` never applies min_cosine 0.0 to any NVR clip, and the answer is built on the wrong video. Not covered by test_gates_by_video_maps_each_video_to_its_own_domain, where the stub scores both videos ~1.0 so the burial cannot appear, nor by the .va-nvr measurements, which are single-domain."}, {"severity": "minor", "file": "CLAUDE.md", "line": 321, "issue": "\"`security` sets `min_cosine: 0.0`\" holds only in run-*/config; the default `config/` dir's security profile has no `retriever:` block and cannot have one, so the sentence describes a setting a reader will not find where the surrounding prose points them.", "scenario": "Someone running the default stub config reads this line, assumes security footage is gated at 0.0, and debugs an unrelated cause for unexpected results when that config dir performs no thresholding whatsoever."}]}
```
