# Typed-Query Tier — LOOP FILE

> **What this is.** The loop-consumable companion to
> [typed-query-tier-plan.md](typed-query-tier-plan.md). That document holds the rationale,
> the op contracts, the two resolve-seams, and the Role-12 reasoning; **this file holds the
> execution state**. A loop iteration reads this file, does one item, updates this file, and
> exits. Do not copy rationale here — link to the plan section instead.
>
> **Who runs this.** A fresh executor agent (no prior session context). Everything you need is
> either in this file, the plan, or the repo docs cited in §Operating-context. If something is
> genuinely undecidable from those, escalate (see §Operating-context → Escalation) rather than
> guessing — the CLAUDE.md rule "never introduce hardcoded content / magic values silently"
> is binding.

---

## Loop protocol (re-read every iteration)

1. **Pick** the first item marked `[ ]` whose listed deps are all `[x]` **or `[R]`** and which
   is not marked `DECIDE`. Skip `DECIDE` items — they are human-only gates. A dep that is only
   `[R]` (review-ready, unmerged) is satisfied **only via the stacking rule** in step 5.
2. **Mark it `[~]`** and append a `START` line to the Progress log before doing anything, so a
   crashed iteration is visible.
3. **Implement** only that item. Scope creep = stop and split the item instead.
4. **Verify** against the item's **Done when** line. All done-conditions are *in addition to*
   the standing floor: `.venv/bin/pytest -q` fully green, **and the A-EV / default-stub path
   stays byte-for-byte unchanged** (this tier is additive — it must not alter any existing
   query path's output).
5. **Commit** via `/task-commit` up to the `need_agent_review` + digest stage. Do **not** create
   `.commit-approved` (human-only). Approvals are **batched** — mark the item `[R]`, **add a row
   to the Branch registry**, log it, and continue to the next item on a fresh branch.
   - **Branch naming:** `loop/<item-id>-<short-slug>` (e.g. `loop/tq1a-aggregate-contracts`).
   - **Base selection (stacking rule):** if all deps are `[x]`, branch from `main`. If a dep is
     only `[R]`, branch from **that dep's branch** (the deepest unmerged dep in the chain).
     Record the base in the registry; registry order = merge order.
   - Never stack on a `[~]` or `[!]` branch.
6. **Update this file**: flip the marker, append a dated Progress-log line (item id, what
   happened, evidence pointer).
7. **Stop the loop** when: (a) no eligible `[ ]` items remain, (b) the next eligible item is
   `DECIDE`, or (c) an item is blocked — mark it `[!]`, write the blocker under the item, and
   stop rather than guessing. **On stop, return a batch report** (registry rows ready for the
   approval session, blockers, decisions needed) to the spawning agent.

**Status markers:** `[ ]` open · `[~]` in progress · `[R]` review-ready (branch in registry) ·
`[x]` done (merged) · `[!]` blocked · `DECIDE` human decision required.

**Approval session (human, batched):** walk the Branch registry **top-to-bottom** (stacking
rule guarantees a base precedes its dependents, so row order is a valid merge order). For each
row: review digest → approve/finalize (`.commit-approved`) → run the golden gate if the PR
touches `src/va/adapters` or `src/va/pipeline` → apply labels → merge → retarget child PRs to
`main`. After the session, flip merged items `[R]`→`[x]` and mark registry rows merged.

---

## Standing guardrails (apply to EVERY item — this tier's whole reason for existing)

- **Determinism ≠ correctness (CLAUDE.md, load-bearing here).** Every counting/aggregation item
  MUST validate output against a **hand-derived ground truth** in its test and cite that number
  in the digest — not just assert stable output. The plan exists because a *stable* wrong count
  (the "70–99 dress changes vs truth ~12–15" case) is the failure mode.
- **No hardcoded domain content.** `resolve_category` ships as a **pure structural** stub
  (plural-strip only — §5.1 of the plan). Do **NOT** silently add a synonym table
  (`vehicle → {car,truck,bus}`, etc.) — that is exactly the canned content CLAUDE.md forbids
  without a flagged item + human sign-off. If the driving "vehicles" query needs expansion, that
  is item **TQ1.b2** (flagged, human-gated), not a quiet edit.
- **Timezone is mandatory, and epoch math is INTEGER math.** `TimeWindow.tz` is required (the
  measured 77-local vs the UTC-window difference proves an unqualified count is meaningless).
  **The bug that motivated a tested op:** `strftime('%s', …)` returns **text**, and SQLite ranks
  any number below any text, so `(start_epoch + first_seen) >= strftime('%s',…)` is *always
  false* → a silent `0`. Build epoch bounds in **Python** (or `CAST(... AS INTEGER)`), and pin a
  regression test that **fails on the text-comparison form**.
- **Per-workdir only.** Aggregation is per-workdir SQL. Do not count across footage domains in a
  mixed workdir (CLAUDE.md retrieval-floor note); surface the mixed-workdir caveat, don't
  silence it.
- **Honest caveats travel with the number.** Phase-1 counts are a RAW UPPER BOUND: no
  cross-window/camera ReID (`dedup="instance"` is a stub), includes parked objects, "crossed" ≠
  "present". The `CountResult.caveats` / `resolution` fields must say so on every call.
  ALSO (batch review 2026-08-17): only wall-clock-anchored videos are windowable — a workdir
  with zero anchored done videos must read NOT APPLICABLE (planner dispatch degrades to a note,
  never "CODE-COUNTED: 0"), and matched tracks on NULL-`start_epoch` (A-EV) videos must be
  counted and named as EXCLUDED in the caveats and on the CODE-COUNTED line.
- Anything touching a shared interface → log it in `COORDINATION.md`.

---

## Operating context (fresh executor — read once, then act)

**Repo trust-gate lifecycle (full spec: `CLAUDE.md` "Commit & review lifecycle" +
`workflow-trust-plan.md`).**
- Commit subjects: `need_agent_review: <desc>` (work complete → post-commit hook spawns a fresh
  read-only reviewer; verdict + findings land in `reviews/`), `wip:`/`checkpoint:` (unfinished),
  or plain subject (only to finalize an approved commit). Use `/task-commit` — it runs the full
  procedure (scope check, combination check, doc check, review loop, four-section digest, STOP
  for the human's `.commit-approved`, finalize).
- **`.commit-approved` and `.guard-override` are HUMAN-ONLY — never create them.** The
  `AGENT_REVIEW=skip` / `ALLOW_*` family and review labels + `gh pr merge` are human-only too.
- **Review-firing gotcha (carried from this branch's history):** the human's `!`-shell has a
  ~2-minute cap that kills a synchronous post-commit review and its process tree. Fire review
  amends in the **background** (`run_in_background: true`) so the tool timeout can't kill them.
  If `.git/.review-approved` is stale, run the full `bash .githooks/post-commit` to write the
  marker before finalize.
- **Spawn review agents** via the post-commit hook (automatic on `need_agent_review:`) and/or the
  `code-reviewer` agent type directly. You may also spawn `general-purpose` / `Explore` sub-agents
  for research or to split a large item — keep sub-agent context out of your own where possible.
- **CI gates (GitHub, beyond local override):** `offline-tests` (full offline suite — triggers on
  pull_request `opened/synchronize/reopened`, **NOT `labeled`**, so if only labels changed and it
  shows a stale red, close+reopen the PR to re-trigger), `evidence` (PR body must carry real
  pytest counts — run `/verify` to generate the block; a hand-written body fails the literal-marker
  grep), `critical-paths` (paths in `scripts/critical_paths.txt` need `human-reviewed` and/or
  `golden-verified` labels — `src/va/cli.py` + `src/va/pipeline/ingest.py` + `src/va/contracts/`
  = human-reviewed; `src/va/adapters/` + `src/va/pipeline/` + `config/` = golden-verified).

**Test-suite hygiene (carried lessons — violating these has cost days):**
- A turn-end Stop-gate spawns its own `pytest`; never run a suite while another is live. Poll with
  a pattern that can't match its own command line — put the poll+pytest in a **script file** (so
  the process command line is `bash <file>`, no `pytest` substring), e.g. the existing
  `wait_and_test.sh` pattern. Do not write `pytest` (or `[p]ytest` in an echo) on the same command
  line you poll from.
- `pytest | tail` returns tail's exit code — never gate a `&&` chain on a pipeline.
- A test that never CONSTRUCTS its scenario (or passes on the broken code) is decoration. For each
  regression test, **run it against the pre-fix code and confirm it fails first.**

**Golden fixtures (required — the user's explicit bar: "all changes must pass golden fixtures").**
- The golden gate is GPU + workdir gated and cannot run in CI:
  `RUN_GOLDEN=1 VA_CONFIG_DIR=run-claude/config GOLDEN_WORKDIR=.va-shots .venv/bin/pytest -m golden`
  (A-EV) and the NVR golden set. The **human** runs it on the Spark at the approval session and
  applies `golden-verified`. Your job: (a) keep offline green, (b) do not change any existing
  query path's output (this tier is additive), (c) where an item adds a planner/retrieval path
  that golden would exercise, add/adjust the fixture so the gate actually covers it.
- **Known gap you may close as a stretch (arch-loop backlog):** there is no NVR golden **ask**
  fixture, so a security-footage count path isn't golden-covered. If TQ1.h lands the count tool
  into `ask`, adding an NVR `ask_questions:` fixture with a ground-truth count is the honest way
  to make "all golden pass" *mean* something for this feature. Flag it if you defer it.

**Data & config facts (grounded — verify against current code before relying on them).**
- Real 24 h workdir: **`.va-24h`** (264 windows, ~526 tracks, ~9187 detections), ingested with
  **SigLIP** (1152-dim) → any real-model command needs `VA_CONFIG_DIR=run-claude/config`. Tests
  use the **stub** backends + synthetic clips (`media/synth.py`) — no GPU/network.
- `object_tracks(id, video_id, object_class, track_confidence, first_seen, last_seen,
  frame_count, appearance_ref)` — `first_seen`/`last_seen` are **video-relative seconds**.
- `object_detections(id, video_id, timestamp, track_id, object_class, bbox_*, confidence)`.
- `videos(… camera_id ('nvr-ch<n>'), start_epoch (UTC epoch of t=0; NULL for A-EV),
  profile, duration_seconds)`; `cameras(id, name, location, last_processed_epoch)`.
- Absolute time of a row = `videos.start_epoch + relative`. The translation primitive already
  exists: **`pipeline/timeline.py`** — `absolute_time(video, relative)` and
  `wallclock_to_chunks([t0,t1] UTC-epoch)` (maps a UTC range to per-chunk relative ranges,
  skipping NULL-epoch A-EV videos). **Reuse it — no new time math.**
- The seam substrate already exists: `objects.py::_classes()` (plural-strip → `resolve_category`
  stub); `object_tracks.appearance_ref` → per-video `appearance.npz` (`space: appearance-crop`,
  written at ingest, unsearched → `resolve_identities` real body later).
- Contracts to reuse: `contracts/query_plan.py` (`QueryPlan`, `needs_object_query` flag, `params`
  bag, `extra="allow"`), `contracts/evidence.py` (`Evidence`/`EvidenceItem`/`Answer`).
- **Detector vocab is narrow** under the `security` profile — current track classes are
  `car`(385), `person`(130), `truck`(10), `dog`(1). There is **no** `vehicle` class, so the
  plural-strip stub cannot answer "vehicles" — that's TQ1.b2 (flagged) / Role-12 territory, not a
  silent synonym map.

**Pre-resolved default decisions (low-stakes; documented so you are not blocked — the human may
override any of these at the approval session):**
- **Registry / module location:** new `src/va/pipeline/aggregate.py` for the ops; new
  `src/va/contracts/aggregate.py` for `TimeWindow`/`CountResult`/… (plan §10.3 [inference], the
  smallest step mirroring the other query modules).
- **Op granularity:** a few **general, composable** ops (`count_objects`, `list_events`,
  `timeline_histogram`), richly parameterized — never one op per question (plan §3).
- **Named heuristics** (`min_frames=2`, histogram default bucket) are STRUCTURE, allowed, but
  **must be flagged in the digest** (CLAUDE.md rule), not buried.

**Escalation (how to get unstuck without burning the main session's context).**
- Prefer to resolve from this file, the plan, and the repo. If you hit a **genuine unknown or a
  real design fork** the plan doesn't settle, return with a crisp, specific question in your batch
  report; the spawning agent answers or relays to the human. Do **not** invent a hardcoded answer
  to keep moving.
- `DECIDE` items are hard stops for the human — do not implement past one.

---

## Phase 0 — foundations *(already in repo; recorded so deps resolve)*

- [x] **TQ0.a** Roles 5/6 rows + provenance: `object_detections` / `object_tracks`, populated and
  `va stale`=0 on `.va-24h`. *Evidence:* PR #41 (merged `322eb51`).
- [x] **TQ0.b** Time translation primitive `wallclock_to_chunks` / `absolute_time`.
  *Evidence:* `pipeline/timeline.py`, `tests/test_timeline*.py` (WS3.b).
- [x] **TQ0.c** Category-candidate primitive `_classes()` (plural-strip).
  *Evidence:* `pipeline/objects.py`.
- [x] **TQ0.d** Appearance store (`appearance_ref` → `appearance.npz`) — ReID substrate for the
  deferred `resolve_identities` real body. *Evidence:* WS4.d (PR #30).

## Phase 1 — the typed aggregation tier *(plan §3–§7, §9 "build now")*

- [R] **TQ1.a** Aggregation contracts: `TimeWindow{start,end,tz}` (tz REQUIRED),
  `CountResult{total, per_camera, window, resolution, caveats, evidence}`,
  `ResolutionProvenance{categories_matched, category_source, dedup_mode, dedup_source}`,
  `EventRow`, `Bucket` — in `contracts/aggregate.py`, evolution-tolerant idiom
  (`ConfigDict(extra="allow")`, defaults everywhere), reusing `EvidenceItem`.
  *Deps:* none (base `main`). *Done when:* models validate; a missing/blank `tz` is rejected;
  round-trip preserves an unknown extra field; full suite green.
  *NB:* `contracts/` is a `human-reviewed` critical path.
- [R] **TQ1.b** `resolve_category(category) -> list[str]` seam (stub) — promote `_classes()`
  plural-strip to a named function returning `(categories, source="plural-strip")` for the
  provenance block. **Pure structural, no synonym content.**
  *Deps:* TQ1.a. *Done when:* parity with `_classes()` on a table of inputs; provenance source
  string set; unit test; suite green.
- [ ] **TQ1.b2** *(flagged, may be `DECIDE`)* Minimal category expansion for the driving
  "vehicles" query. This introduces domain content (which detector classes constitute a
  "vehicle") → **must be human-approved per the no-hardcoded-content rule**, or deferred to the
  Role-12 taxonomy (TQ2.a). Do not fold silently into TQ1.b.
  *Deps:* TQ1.b. *Default:* deferred; mark `DECIDE` and carry the question in the batch report.
- [R] **TQ1.c** `select_tracks(categories, window, cameras) -> list[Track]` — SQL over
  `object_tracks ⋈ videos`, filtered by absolute time (`start_epoch + first_seen` within the
  window's UTC-epoch bounds) and optional camera set; skips NULL-epoch videos. Reuse
  `timeline.py` for the wall-clock→UTC conversion.
  *Deps:* TQ1.a. *Done when:* a synthetic ingest with tracks at KNOWN epochs returns exactly the
  in-window tracks; **a regression test pins the integer-epoch comparison and FAILS on the
  `strftime('%s')` text-comparison form** (the silent-0 bug); tz conversion covered (a track at
  07:00 UTC lands inside "Aug-11 00:00–12:00 local, tz=America/Los_Angeles"); suite green.
- [R] **TQ1.d** `resolve_identities(tracks, mode) -> list[Entity]` seam (stub) — `mode="raw"`:
  one track = one entity, applying the `min_frames` flicker filter (today's
  `TrackStore.distinct_counts` semantics); `mode="instance"`: **accepted but falls back to raw +
  a caveat** ("cross-window/camera ReID not yet available").
  *Deps:* TQ1.a. *Done when:* raw mode reproduces `distinct_counts` on a fixture; instance mode
  returns the same entities plus the caveat; `dedup_source` provenance set; suite green.
- [R] **TQ1.e** `count_objects(category, window, cameras=None, dedup="raw", min_frames=2) ->
  CountResult` — compose TQ1.b→c→d; fill `total`, `per_camera`, `window` (echoed), `resolution`,
  `caveats` (raw-upper-bound / parked / no-ReID / "crossed"≠"present"), and `evidence` (backing
  windows/tracks as `EvidenceItem`s).
  *Deps:* TQ1.b, TQ1.c, TQ1.d. *Done when:* end-to-end over a synthetic ingest **matches a
  hand-counted ground truth cited in the test** (determinism-≠-correctness); per-camera split
  correct; caveats non-empty; mixed-workdir caveat present when applicable; suite green.
- [R] **TQ1.f** `list_events(...)` (one row per track: category, camera, first/last wall-clock,
  frames) and `timeline_histogram(..., bucket="1h")` (counts per bucket).
  *Deps:* TQ1.c, TQ1.e. *Done when:* list rows match the tracks behind a count for the same
  window (cross-check); histogram bucket counts sum to the count; ground-truth-checked; suite green.
- [R] **TQ1.g** CLI surface — a `va aggregate count/events/histogram` subcommand (or a
  windowed/tz extension of `va count`) that invokes the ops and prints the per-camera table +
  caveats. Decide the smaller-diff shape at implementation and record it.
  *Deps:* TQ1.e, TQ1.f. *Done when:* CLI runs against `.va-24h` and reproduces the hand-computed
  Aug-11-local-morning car table (ch2 55 / ch1 22 / 77 total, `frame_count≥2`) — recorded in the
  digest as the ground-truth check; help text documents the tz requirement + caveats; suite green.
  *(Update 2026-08-19: that 77 was the PRE-repair count. The `.va-24h` data-integrity repair removed
  contaminated foreign-camera/stale heads; the repaired workdir now reads 72 (ch2 51 / ch1 21), which
  the golden fixture `nvr24h_aggregate.yaml` pins — see `va-24h-data-integrity-investigation.md`.)*
  *NB:* `cli.py` is a `human-reviewed` critical path.
- [R] **TQ1.h** Planner integration — a **tool/registry entry** for the aggregation ops
  (JSON-schema-described) that the Role-11 planner selects and fills; `retrieve()`/`ask()`
  dispatch to `count_objects` when the intent is a windowed count; degrade to a caveat (never a
  hallucinated total) if args are missing/invalid. Add `needs_aggregation` to `QueryPlan` (or
  reuse `needs_object_query` + params — pick and document).
  *Deps:* TQ1.e. *Done when:* the original failing question — *"how many cars/vehicles on Aug 11
  before noon"* — returns a **CountResult-backed, tz-correct, evidenced** answer through `ask`
  (leading with the code-counted line, per the R11.a deep-scan display discipline); an **offline
  stub-planner test** exercises the dispatch (not only the gated real-model path); suite green.
  *NB:* `contracts/query_plan.py` (human-reviewed) + `pipeline/ask.py` (golden-verified) — this
  item needs the golden gate at approval, and is the natural place to add the NVR golden **ask**
  fixture (see Operating-context → Golden).

## Phase 2 — Role-12 real bodies *(deferred; gated on the scope DECIDE — does NOT block Phase 1)*

- **TQ2.DECIDE** — *Role 12 scope.* The repo reserves "Role 12" = **ReID** (identity /
  `resolve_identities`). The user's framing adds **classification/taxonomy** (`resolve_category`)
  + **composites**. These are independent axes (plan §5, §10.1). Decide: is Role 12 ReID,
  classification, or **both as sub-roles** (12a ReID / 12b taxonomy)? Reconcile
  `video-analytics-solution-architecture.md`'s Role-12 definition so the number isn't overloaded.
  *Human-only. Phase 1 ships without it — the seams are stubs regardless.*
- [ ] **TQ2.a** *(deferred)* Real `resolve_category` body = a canonical taxonomy registry
  (`categories(canonical, kind, parent, aliases)`), populated from the detector vocab + curation;
  `resolve_category("vehicle")` returns every class under that node. *Deps:* TQ2.DECIDE.
- [ ] **TQ2.b** *(deferred)* Real `resolve_identities` body = appearance-embedding clustering over
  `appearance_ref`/`appearance.npz` within a time/space gate; `dedup_source` flips to
  "cross-window ReID". No schema migration (substrate exists). *Deps:* TQ2.DECIDE, TQ0.d.
- [ ] **TQ2.c** *(deferred)* Composites (`human on bike`): taxonomy represents a composite entity
  (two child categories + relation); a `co_occurrence(cat_a, cat_b, relation, window)` op
  populates it via bbox/trajectory reasoning. *Deps:* TQ2.a.

---

## Branch registry (append on `[R]`; merge top-to-bottom)

| Item | Branch | Base | PR | Status |
|---|---|---|---|---|
| TQ1.a | `loop/tq1a-aggregate-contracts` | `main` | — | review-ready (approve, 0 findings r2; needs `human-reviewed` label — touches `contracts/`) |
| TQ1.b | `loop/tq1b-resolve-category` | `loop/tq1a-aggregate-contracts` | — | review-ready (approve, 0 findings r2; needs `golden-verified` label — touches `pipeline/`) |
| TQ1.c | `loop/tq1c-select-tracks` | `loop/tq1b-resolve-category` | — | review-ready (approve r2, 1 minor carried to TQ1.d/e — see log; needs `golden-verified` label — touches `pipeline/` + storage) |
| TQ1.d | `loop/tq1d-resolve-identities` | `loop/tq1c-select-tracks` | — | review-ready (approve r1, 1 minor doc-drift carried to TQ1.e — see log; needs `golden-verified` label — touches `pipeline/`) |
| TQ1.e | `loop/tq1e-count-objects` | `loop/tq1d-resolve-identities` | — | review-ready (approve r1, 1 minor doc-drift carried to TQ1.f — see log; needs `golden-verified` label — touches `pipeline/`) |
| TQ1.f | `loop/tq1f-events-histogram` | `loop/tq1e-count-objects` | — | review-ready (approve, 0 findings r2; needs `golden-verified` label — touches `pipeline/`) |
| TQ1.g | `loop/tq1g-cli-aggregate` | `loop/tq1f-events-histogram` | — | review-ready (approve, 0 findings r1; needs `human-reviewed` label — touches `cli.py`; ground truth 77/55/22 reproduced on `.va-24h`) |
| TQ1.h | `loop/tq1h-planner-aggregation` | `loop/tq1g-cli-aggregate` | — | review-ready (approve, 0 findings r5; needs BOTH labels — `human-reviewed` (contracts/query_plan.py) + `golden-verified` (pipeline/ask.py, retrieval.py, adapters/); golden: run `-m golden -k nvr24h` with GOLDEN_WORKDIR=.va-24h for the new aggregate ask fixture, plus the A-EV set for no-regression) |

---

## Progress log (append-only, one line per event)

- 2026-08-17 — Loop file created from `typed-query-tier-plan.md` by the spawning session,
  modeled on `architecture-evolution-loop.md`. Phase 0 marked done from merged work (PR #41 +
  WS3.b/WS4.d). Low-stakes design decisions pre-resolved (module = `pipeline/aggregate.py`;
  few-general-ops; named-heuristics-flagged). Role-12 scope left as `TQ2.DECIDE`, structured so
  Phase 1 does not depend on it. Executor: fresh Fable-5 agent.
- 2026-08-17 — TQ1.a START (executor). Branch `loop/tq1a-aggregate-contracts` off `main@322eb51`;
  the two untracked design docs (plan + this loop file) ride on this branch per repo convention.
- 2026-08-17 — TQ1.a `[R]` @ `1e89593`. Contracts + 17 tests; suite 754 passed / 2 skipped
  (baseline 737/2). Review r1 approve w/ 2 minor (DST-gap misdiagnosis; plan §4 "instance-reid"
  string drift) — both fixed, gap tests verified failing pre-fix; r2 approve 0 findings
  (`reviews/20260817-144724-loop-tq1a-aggregate-contracts-1e89593.md`). Loop-file `[R]` flip
  rides on the TQ1.b branch (stacking rule).
- 2026-08-17 — TQ1.b START. Branch `loop/tq1b-resolve-category` off `loop/tq1a-aggregate-contracts`.
- 2026-08-17 — TQ1.b `[R]` @ `4327c3d`. resolve_category seam + `_classes` delegation; suite 770
  passed / 2 skipped. r1 approve w/ 3 minor (comment typo; tautological parity → outputs now pinned
  by hand; plan §5.1 signature drift) — all fixed; r2 approve 0 findings
  (`reviews/20260817-150153-loop-tq1b-resolve-category-4327c3d.md`).
- 2026-08-17 — TQ1.c START. Branch `loop/tq1c-select-tracks` off `loop/tq1b-resolve-category`.
  TQ1.b2 skipped per its own text (flagged/human-gated; default deferred — carried to the batch
  report as a DECIDE question).
- 2026-08-17 — BATCH-REVIEW FIX (post-squash, `feature/typed-query-tier`): the combined-commit
  review found a major the per-item reviews missed — a windowed count silently excluded all
  tracks on NULL-`start_epoch` (A-EV) videos, so a pure A-EV workdir shipped a confident bare 0
  / "[CODE-COUNTED: 0]". Fixed: `TrackStore.window_anchoring` coverage probe; `count_objects`
  leads its caveats with NOT APPLICABLE when zero anchored done videos exist and names how many
  matched tracks were excluded whenever un-anchored ones exist (numbers also in
  `CountResult.attributes["window_anchoring"]`); `dispatch_aggregation` DEGRADES to the honest
  note on un-windowable workdirs (no CODE-COUNTED item) and appends the exclusion NB to the
  CODE-COUNTED line otherwise; CLI/CLAUDE.md help + plan §11 documented. 6 new tests, all
  verified failing pre-fix.
- 2026-08-17 — TQ1.c `[R]` @ `00cea83`. `TrackStore.select_placed` + `pipeline.aggregate.
  select_tracks`; suite 779 passed / 2 skipped. r1 approve w/ 1 minor (cameras=[] falsy guard →
  fixed, pinning test verified failing pre-fix); r2 approve w/ 1 minor
  (`reviews/20260817-152419-loop-tq1c-select-tracks-00cea83.md`): start-only window membership
  not yet in the canonical caveat surface — DISPOSITION: plan §11 gains the bullet and
  `count_objects` (TQ1.e) emits the caveat string, both inside this same review range on the
  next stacked branches (re-reviewed there). Restart note: SSH drop killed the executor
  mid-TQ1.c at ~15:06; resumed with context intact, no state lost.
- 2026-08-17 — TQ1.d START. Branch `loop/tq1d-resolve-identities` off `loop/tq1c-select-tracks`.
  Carries the TQ1.c r2 doc fix (plan §11 start-only-membership bullet).
- 2026-08-17 — TQ1.d `[R]` @ `dd783db`. resolve_identities seam (raw + honest instance fallback,
  Entity/IdentityResolution); suite 785 passed / 2 skipped; distinct_counts parity pinned
  (hand-derived car=2/person=2). r1 approve w/ 1 minor
  (`reviews/20260817-153316-loop-tq1d-resolve-identities-dd783db.md`): plan §5.2 signature line
  still shows the pre-build shape — DISPOSITION: fixed at the top of the TQ1.e branch (same
  review range, re-reviewed there). TQ1.c's carried §11 caveat bullet landed here as planned.
- 2026-08-17 — TQ1.e START. Branch `loop/tq1e-count-objects` off `loop/tq1d-resolve-identities`.
  Carries the TQ1.d r1 doc fix (plan §5.2 as-built signature).
- 2026-08-17 — TQ1.e `[R]` @ `483ace7`. Windowed count op; suite 794 passed / 2 skipped;
  hand-counted fixture truth 4 cars (ch1=2/ch2=2; flicker a4 dropped at min_frames=2, admitted
  at 1 -> 5). r1 approve w/ 1 minor
  (`reviews/20260817-154208-loop-tq1e-count-objects-483ace7.md`): plan §6 still mandates
  wallclock_to_chunks routing vs the as-built inline `start_epoch + first_seen` SQL —
  DISPOSITION: §6 rewritten as-built at the top of the TQ1.f branch, and TQ1.f's histogram
  buckets from the SAME select_placed path (no second time-placement rule). TQ1.d's carried
  §5.2 fix landed here as planned.
- 2026-08-17 — TQ1.f START. Branch `loop/tq1f-events-histogram` off `loop/tq1e-count-objects`.
  Carries the TQ1.e r1 doc fix (plan §6 as-built).
- 2026-08-17 — TQ1.f `[R]` @ `902779c`. list_events + timeline_histogram on the ONE shared
  selection path (`_entities_for`); suite 804 passed / 2 skipped; hand-derived hourly histogram
  [1,2,0…,1] sums to the count (cross-checked at 4 bucket widths). r1 request_changes w/ 1 major
  (integer-ceiling idiom under-allocated buckets on fractional-second spans → IndexError) —
  fixed with math.ceil, regression test verified failing pre-fix; r2 approve 0 findings
  (`reviews/20260817-155552-loop-tq1f-events-histogram-902779c.md`). TQ1.e's carried §6
  as-built fix landed here as planned.
- 2026-08-17 — TQ1.g START. Branch `loop/tq1g-cli-aggregate` off `loop/tq1f-events-histogram`.
- 2026-08-17 — TQ1.g `[R]` @ `ccf9b4b`. `va aggregate {count,events,histogram}` (new subcommand
  group — smaller diff than retrofitting `va count`; decision recorded in COORDINATION.md);
  suite 812 passed / 2 skipped; r1 approve 0 findings
  (`reviews/20260817-160417-loop-tq1g-cli-aggregate-ccf9b4b.md`). GROUND-TRUTH CHECK (done-when):
  real CLI on `.va-24h` reproduced the hand-computed Aug-11 00:00–12:00 PDT car table EXACTLY —
  nvr-ch2 55 / nvr-ch1 22 / total 77 (frame_count>=2). CLAUDE.md commands block updated.
- 2026-08-17 — TQ1.h START. Branch `loop/tq1h-planner-aggregation` off `loop/tq1g-cli-aggregate`.
- 2026-08-17 — TQ1.h `[R]` @ `464eea3`. Planner-integrated aggregation: `QueryPlan.
  needs_aggregation` + `params["aggregation"]`, `AGGREGATION_TOOLS` JSON-schema registry,
  `dispatch_aggregation` (validate → run → ONE CODE-COUNTED EvidenceItem; every bad-arg shape
  degrades to an honest note — 10 parametrized cases), retrieve()/assemble() dispatch (post-gate:
  code-counted facts are never relevance-dropped), ask() leads with the aggregate line,
  PLANNER_PROMPT rendered from the registry (drift-guarded), REASONER_PROMPT do-not-recount for
  aggregate_count, golden harness `modality:` key + `nvr24h_aggregate.yaml` (total==77,
  provenance hand-sql-crosscheck). Suite 833 passed / 2 skipped. Five review rounds: r1 4 minors
  (fixture provenance label; truncated-total lead; caveats on all ops; cameras=[] falsy) → fixed;
  r2 2 minors (limit TypeError escape; README doc gap) → fixed; r3 4 minors (CLI every-op
  honesty + untruncated total; reasoner-prompt recount rule; planner-prompt fabrication license;
  'people' help example) → fixed; r4 1 minor (plan §7 fallback line as-built) → fixed; r5
  approve 0 findings (`reviews/20260817-164822-loop-tq1h-planner-aggregation-464eea3.md`).
  Behavioral fixes verified failing pre-fix (truncation, caveat, null-limit, cameras=[] tests).
- 2026-08-17 — **PHASE 1 COMPLETE**: TQ1.a–TQ1.h all `[R]` (8 stacked branches, merge order =
  registry order). TQ1.b2 remains DECIDE (deferred, per its default). Loop STOPPED per protocol
  step 7(a); batch report returned to the spawning agent for the human approval session.
