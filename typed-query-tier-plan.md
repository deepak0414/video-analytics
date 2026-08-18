# Typed-Query-Tier Plan — deterministic aggregation queries with resolve-seams for Role 12

> **Terminology.** A-EV = edited videos · A-LSSRVF = one long static-scene raw feed (security cam) ·
> A-MCLSSRVF = multi-camera. "Role 12" is used loosely today — see §5 for the two distinct
> capabilities it actually refers to (ReID vs classification).

*Status: **draft for iteration, 2026-08-14.** Design note. Grounds every claim in current repo
code (`src/va/pipeline/objects.py`, `contracts/query_plan.py`, `contracts/evidence.py`,
`pipeline/timeline.py`, `storage/structured/schema.py`). Items marked **[inference]** are proposal,
not fact. Ties into: [chat-interface-plan.md](chat-interface-plan.md) (§3 "closed set of typed
actions", Tier B grounding), [architecture-evolution-plan.md](architecture-evolution-plan.md),
[CLAUDE.md](CLAUDE.md) ("the LLM narrates, code counts").*

---

## 0. TL;DR

- **What:** a small, fixed set of **typed aggregation operations** (`count_objects`, `list_events`,
  `timeline_histogram`) over Roles 5/6 rows, each with a declared signature and a structured,
  evidence-carrying return — callable by the Role-11 planner as **tools** (function calling).
- **Why now:** counting/aggregation questions ("how many vehicles this morning") have **no query
  path today** — `va ask` routes them to visual retrieval + a gated deep-scan and abstains. The
  data exists (object tracks); the tier that reads it doesn't. This is [chat-interface-plan.md]'s
  Tier B, which the whole chat design silently assumes already exists.
- **The centerpiece — two resolve-seams (§5):** the count path routes category names through
  `resolve_category()` and track instances through `resolve_identities()`. **Both ship as
  pass-through stubs now** and are the exact insertion points for **Role 12** later — so Role 12 is
  a stub-swap, not a rewrite. Crucially, **both seams already exist in embryonic form** in the repo
  (`_classes()` plural-stripping; `object_tracks.appearance_ref` → `appearance.npz`), so this is not
  a speculative abstraction.
- **Standard:** this is the industry tool/function-calling pattern (Anthropic tool use / MCP): each
  op is a JSON-Schema-described tool in a **registry**; new ops are additive, no planner rewrite.
- **Extensibility discipline:** a handful of **general, composable** ops + rich parameters — never
  one op per question (that way lies "op sprawl").

---

## 1. The problem this solves (grounded)

Measured 2026-08-14 on `.va-24h`: asking the web chat *"count vehicles Aug 11 before noon"* returned
"evidence insufficient." Cause: the planner set retrieval + deep-scan flags; deep-scan is gated off
by the `security` profile (correct — it counts scene cuts, not objects), and **no tier reads the 526
object tracks that actually answer it.**

What exists today (`pipeline/objects.py`):

```python
count_objects(text, workdir, min_frames=2) -> List[DistinctCount]   # per-class, WHOLE CORPUS
query_objects(text, workdir)               -> List[ObjectSummary]   # frame appearances
```

Gaps that make it unusable for the real question:
- **No time window** — it counts across the entire workdir, not "Aug 11 00:00–12:00 local".
- **No camera grouping** — can't say "ch2 73, ch1 36".
- **No timezone** — "before noon" is ambiguous. Hand-computing the answer gave **111 (local, PDT)
  vs 147 (UTC)**. A count with no tz is meaningless.
- **No evidence / method disclosure** — a bare number with no "which windows, deduped how".

The fix is a tier that takes those as **typed parameters** and returns a structured, evidenced result.

---

## 2. What "typed query tier" means (the standard)

**Typed** = a fixed menu of named operations, each with an explicit parameter signature and a
structured return contract — *not* free-form NL, *not* raw LLM-authored SQL. The LLM's only job is to
**map the fuzzy request onto the parameters**; **code runs the actual SQL and computes the answer.**
That is CLAUDE.md's load-bearing rule ("the LLM narrates, code counts") made mechanical.

| Approach | Verdict |
|---|---|
| NL → LLM answers directly | hallucinates the number — *this is what failed today* |
| LLM writes raw SQL | unsafe/unpredictable; wrong joins, invented columns, no tz guarantee, unvalidatable |
| **Typed ops (this plan)** | LLM fills validated arguments; code executes deterministically and returns evidence |

**This is tool/function calling.** Each op is described by a JSON Schema (its params); the planner
selects a tool from a **registry** and fills arguments; the runtime validates against the schema and
executes. Adding a query = register a new tool. In-repo precedent: `registry.py` (adapter registry),
`QueryPlan` tier flags + `params` bag (`contracts/query_plan.py`, already `extra="allow"`), and
[chat-interface-plan.md] §3's committed "closed set of typed actions". So this direction is already
chosen; this note specifies the aggregation half of it.

---

## 3. The op set (small, orthogonal, composable)

Keep it to a handful of **general** ops parameterized richly, not many narrow ones. Initial set
**[inference]**:

```python
# All times are UTC epoch seconds internally; `tz` governs how a wall-clock request is interpreted
# and how results are presented. window is REQUIRED to be explicit (no implicit "all time").

count_objects(
    category: str,                 # "vehicle", "car", "person", "dog" — resolved via §5.1
    window: TimeWindow,            # {start, end, tz}  (§6)
    cameras: list[str] | None = None,   # ["nvr-ch2", ...]; None = all
    dedup: DedupMode = "raw",      # "raw" | "instance"  (§5.2) — "instance" is a no-op stub today
    min_frames: int = 2,           # existing flicker filter (a NAMED heuristic — flag at review)
) -> CountResult

list_events(                       # "what vehicle events happened" — the rows behind a count
    category: str, window: TimeWindow, cameras=None, limit: int = 100,
) -> list[EventRow]                # one row per track: category, camera, first/last wall-clock, frames

timeline_histogram(               # "when were vehicles seen" — counts per bucket for a chart
    category: str, window: TimeWindow, bucket: str = "1h", cameras=None,
) -> list[Bucket]                 # [{bucket_start, count}], for a sparkline/day-narrative
```

Deferred until a driving query needs them: `filter_by_attribute(...)` (colour/size — needs Role-10
OCR or an attribute role), `co_occurrence(...)` (composites, §8).

**Why these three:** they cover "how many" (count), "which ones / show me" (list → snippet refs for
the chat bot), and "when / how does it trend" (histogram → day narrative). Everything the
chat-interface-plan's Tier B asks for decomposes into these + the existing retrieval tiers.

---

## 4. The return contract

Reuse the repo's evolution-tolerant idiom (`Evidence`/`EvidenceItem`, `ConfigDict(extra="allow")`,
an `attributes` bag). A dedicated result carries the **answer + how it was derived + its evidence**:

```python
class CountResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    total: int
    per_camera: dict[str, int]                # {"nvr-ch2": 73, ...}
    window: TimeWindow                         # echoed back, tz included
    resolution: ResolutionProvenance           # §5 — WHAT normalization/dedup actually ran
    caveats: list[str]                          # honest, human-readable
    evidence: list[EvidenceItem]                # the backing windows/tracks (entity manifest, §6 chat)

class ResolutionProvenance(BaseModel):
    categories_matched: list[str]              # what `category` expanded to (e.g. car, truck)
    category_source: str                       # "plural-strip" (today) | "taxonomy-registry" (Role 12)
    dedup_mode: str                            # "raw" (today) | "instance" (Role 12)
    dedup_source: str                          # "per-window tracks" | "cross-window ReID"
```

**The `resolution` + `caveats` fields are the anti-hallucination spine** (CLAUDE.md
"determinism ≠ correctness"): a count must always disclose what it did NOT do. Today's honest string:
*"raw per-window tracks; no category taxonomy, no cross-window/camera dedup; includes parked; 'crossed'
not distinguished."* When Role 12 lands, the same field just gets more confident — the caller sees the
difference, and no number ever ships without its method.

---

## 5. The resolve-seams (the point of this note)

Counting correctly needs **two independent kinds of normalization**, on two different axes. Both are
inserted as **explicit function calls in the count path today**, both are **pass-through stubs**, and
both are the exact plug points for Role 12. Critically, **each already exists in embryonic form** — so
these are seams we *widen*, not abstractions we *invent*.

```python
def count_objects(category, window, cameras, dedup, min_frames):
    categories, category_source = resolve_category(category)  # §5.1  axis: WHAT KIND
    tracks     = select_tracks(categories, window, cameras) # plain SQL over object_tracks + timeline
    entities   = resolve_identities(tracks, mode=dedup)     # §5.2  axis: SAME INSTANCE?
    return CountResult(total=len(entities), per_camera=_by_cam(entities),
                       resolution=..., caveats=..., evidence=[...])
```

### 5.1 `resolve_category()` — the classification / taxonomy axis ("what kind of thing")

- **Question:** is "car" the same as "vehicle" and "sedan"? Is a dog a "subject" (living), a car an
  "object" (non-living)? These must collapse to canonical categories or counts double/triple-count.
- **Today (stub):** the repo already has the primitive — `objects.py::_classes()` splits query words
  and strips plurals (`"birds" → ["birds","bird"]`). `resolve_category()` **is** that logic, promoted
  to a named seam that returns `categories_matched` for the provenance block. No behaviour change.
- **Later (Role 12 = classification):** swap the stub for a **label registry** — a canonical taxonomy
  (living/non-living; car↔vehicle↔sedan synonyms/hypernyms; species). `resolve_category("vehicle")`
  returns every detector class under that node. The registry is a small table
  (`categories(canonical, kind, parent, aliases)`) **[inference]**, populated from the detector
  vocabulary + curation.
- **Contract that makes the swap free:** signature `str -> (list[str], source: str)` is stable
  (as built in TQ1.b — the source string feeds `ResolutionProvenance.category_source`); only the
  body and the source value change.

### 5.2 `resolve_identities()` — the instance-dedup axis ("is this the same physical thing")

- **Question:** the same physical car appearing in three 40 s windows, or seen by both ch1 and ch2, is
  ONE vehicle — not three. This is **re-identification (ReID)**, the axis the repo *already* reserves
  as "Role-12 ReID schema insurance."
- **Today (stub):** `mode="raw"` → one track = one entity (with the existing `min_frames` flicker
  filter, i.e. today's `TrackStore.distinct_counts`). `mode="instance"` is **accepted but is a no-op
  that falls back to raw + a caveat** — so the parameter and the honest disclosure exist from day one.
- **Later (Role 12 = ReID):** the substrate is already in place — `object_tracks.appearance_ref` →
  `appearance.npz` (one crop embedding per track, `space: appearance-crop`, written at ingest, *"NOT
  searched by any query path yet"*). `resolve_identities(..., mode="instance")` clusters tracks by
  appearance-embedding similarity (within a time/space gate) into physical entities and returns the
  cluster count. **No schema migration** — the column and vectors are already written.
- **Contract that makes the swap free:** signature
  `resolve_identities(tracks, mode, min_frames=2) -> IdentityResolution` is stable (as built in
  TQ1.d — the result carries `entities` plus the `dedup_mode`/`dedup_source`/`caveats` that feed
  `ResolutionProvenance`); only the body and the provenance strings change — `dedup_source` flips
  from "per-window tracks" to "cross-window ReID".

### Why building the tier now *reduces* Role-12 cost (the direct answer to the question)

- **With the seams:** Role 12 (either flavour) is a **stub body swap** behind a fixed signature — the
  query ops, their schemas, the planner wiring, and the return contract are untouched. This is exactly
  the repo's proven "schema insurance" discipline (`appearance_ref` reserved-but-unused;
  `extra="allow"` contracts).
- **Without the seams** (hardcoding `COUNT(DISTINCT track_id) WHERE object_class='car'`): integrating
  Role 12 means **rewriting every op**. So the thing that makes Role 12 hard is *not* doing typed
  query now — it's doing it *without* these two named seams. Doing it thoughtfully now is the cheapest
  on-ramp.

---

## 6. Timezone & the time window (non-negotiable)

- `TimeWindow = {start: datetime, end: datetime, tz: str}`. **`tz` is required** — today's 111 (local)
  vs 147 (UTC) proves an unqualified count is ambiguous.
- Internally (as built, TQ1.c/e): the wall-clock window is converted to UTC epoch ONCE, in Python,
  by `TimeWindow.epoch_bounds()` (tz-mandatory, DST-aware); track membership is then the single
  per-track placement rule — absolute start = `videos.start_epoch + first_seen` (the
  `pipeline/timeline.py::absolute_time` formula) inside the half-open window — inlined
  number-to-number in `TrackStore.select_placed`'s SQL (NULL-epoch A-EV videos skipped by
  construction). **One placement rule:** every aggregation op MUST route through
  `select_tracks`/`select_placed` — never build a second membership path (e.g. via
  `wallclock_to_chunks` range-mapping, whose unknown-duration rel_end cap can disagree with the
  per-track rule). `wallclock_to_chunks` remains the primitive for callers that genuinely need
  chunk RANGES; this tier needs per-track placement.
- Presentation echoes local wall-clock (the camera-overlay time), per `Config.footage.time_model`.
- **The `evidence` list is the entity manifest** [chat-interface-plan.md] §6.2 wants — every count
  ships the windows/cameras/tracks behind it, so "those 5 cars" follow-ups resolve against real refs.

---

## 7. Planner integration

- Add a tier to `QueryPlan` — `needs_object_query` already exists; a counting/aggregation intent can
  reuse it plus a `params` entry, or add `needs_aggregation: bool` **[inference]**. The planner emits
  the op name + arguments; `retrieve()`/`ask()` dispatch to the registry.
- **Registry of tool schemas:** each op contributes a JSON Schema (name, params, types). The planner
  is handed the schema set and picks + fills — the standard tool-use loop. New op = new registry
  entry, **no planner code change**. This is the mechanical form of chat-interface-plan §3's "closed
  set of typed actions" (which is also the prompt-injection containment: the LLM can only *select*
  typed ops, never author SQL or shell).
- Fallback (as built, TQ1.h): if the planner produces missing/invalid args, dispatch degrades to
  an honest **"aggregation requested but not run" evidence note with NO count** — never a
  hallucinated total, and deliberately NOT the whole-corpus `count_objects` either (a corpus-wide
  number presented against a windowed question is exactly the silently-wrong answer this tier
  exists to prevent).

---

## 8. Composites ("human on bike") — reserve, defer

A composite is **relational** — a label about the co-occurrence of two detections, not a property of
one object. It is a step beyond *both* flat classification and ReID.

- **Reserve now:** let the taxonomy registry (§5.1) represent composite entities (a category whose
  definition references two child categories + a spatial/temporal relation). Schema shouldn't block them.
- **Defer the detector:** actually *populating* composites needs co-occurrence reasoning over
  detections/tracks (bbox overlap + shared trajectory over time) — its own work item, gated on a
  driving query. `co_occurrence(cat_a, cat_b, relation, window)` is the op stub it would fill.

---

## 9. Phasing — build now / stub now / defer

| Piece | Status in phase 1 |
|---|---|
| `count_objects` (windowed, per-camera, tz) + `CountResult` | **build** |
| `list_events`, `timeline_histogram` | **build** (cheap; unlock chat snippets + day narrative) |
| `resolve_category()` seam | **build as stub** = today's `_classes()` plural-strip |
| `resolve_identities()` seam | **build as stub** = raw tracks + `min_frames`; `mode="instance"`→caveat |
| `ResolutionProvenance` + `caveats` | **build** (honest disclosure from day one) |
| Planner tool + registry entry | **build** |
| Role-12 classification registry (`resolve_category` real body) | **defer** (own work) |
| Role-12 ReID clustering (`resolve_identities` real body) | **defer** — substrate (`appearance_ref`/`appearance.npz`) already exists |
| Composites detector | **defer** (schema reserved) |

---

## 10. Open decisions (for the user)

1. **Role 12 scope.** The repo reserves "Role 12" = **ReID** (identity). Your description is
   **classification/taxonomy + composites**. These are different axes (§5). Decide: is Role 12 ReID,
   classification, or *both as sub-roles* (12a ReID / 12b taxonomy)? This note supports either — the
   two seams are independent — but the architecture doc should be reconciled so the number isn't
   overloaded. *(I can pull up the architecture doc's official Role-12 definition to reconcile.)*
2. **Op granularity.** Confirm the "few general ops" stance (§3) vs. wanting specific ops per common
   question.
3. **Where the registry lives.** Extend `QueryPlan` + a new `pipeline/aggregate.py`, or a dedicated
   `tools/` registry module shared with the chat bot? [inference] `aggregate.py` mirroring the other
   query modules is the smallest step.
4. **Named heuristics to flag at review** (CLAUDE.md rule): `min_frames=2` (flicker filter — already
   in-repo), the ReID similarity threshold + time/space gate (when 5.2's real body lands), histogram
   default bucket. None are content; all are structure — but flag them explicitly, don't hardcode
   silently.

## 11. Honest caveats

1. **Phase-1 counts remain an upper bound** — `dedup="instance"` is a stub, so cross-window/camera
   re-appearances still inflate. The value is that the count is now *windowed, per-camera, tz-correct,
   and honestly labelled*, not that it's a unique-vehicle count. The `caveats` field must say so.
2. **Only wall-clock-anchored videos are windowable.** A video with `start_epoch` NULL (every
   standalone/A-EV ingest) cannot be placed on the clock, so windowed counts exclude ALL of its
   tracks. Disclosure is mandatory (batch review, 2026-08-17): a workdir with zero anchored done
   videos gets a leading NOT-APPLICABLE caveat (and the planner dispatch degrades to a note
   instead of emitting "CODE-COUNTED: 0"); matched tracks on un-anchored videos are counted and
   named in a caveat (and on the CODE-COUNTED line) whenever they exist.
3. **Window membership is by track START.** `select_tracks` counts a track when its absolute
   start (`start_epoch + first_seen`) falls in the half-open window — so adjacent windows
   partition tracks without double counting, but an object already being tracked when the window
   opens is NOT counted in it. The `caveats` field must disclose this alongside the raw-upper-bound
   caveat (added TQ1.c review r2).
4. **"Crossed" ≠ "present."** A track includes parked cars. Distinguishing crossings needs bbox-motion
   analysis (dwell/velocity, the [chat-interface-plan.md] §4.1 flags) — a separate op, not this tier.
5. **Category quality is only as good as the detector vocabulary** until the Role-12 registry exists;
   the plural-strip stub won't unify "sedan"/"SUV" under "vehicle" — it just avoids plural misses.
6. **Mixed-workdir hazard unchanged** — aggregation is per-workdir SQL; keep A-LSSRVF chunks in their
   own workdir (CLAUDE.md retrieval-floor note).

---

*Next step if approved: turn §3–§6 into `pipeline/aggregate.py` + `CountResult`/`TimeWindow`
contracts + the two stub seams + a planner tool entry, behind the trust-gate lifecycle. This note is
currently untracked (like the other `*-plan.md` docs); committing it is a separate `/task-commit`.*
