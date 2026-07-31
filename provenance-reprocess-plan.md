# Derived-data provenance, reprocess & vector-shard tagging (WS-1 §6-b)

*Working plan for the OPEN half of WS-1 (see `architecture-evolution-plan.md`). §6-a — the
schema-migration runner — is merged (PR #21). This doc owns the derived-data half: surviving
model/code changes on a real corpus without silent corruption or all-or-nothing re-processing.*

---

## 1. Why this exists

When the **code that produces derived data changes** (a better model, a new role, a changed
embedder), old videos need **re-running**, not an `ALTER` — you can't SQL-migrate a vector, you
re-embed. `user_version` (§6-a) guards the *relational* schema; it does nothing for derived data.
Three capabilities close that gap:

- **A — Provenance** (per video × role): record *what produced* each role's rows, so we can tell
  *exactly which videos are stale* on a model bump.
- **B — Batch reprocess**: re-run *role X* over *only the stale videos*, selectively.
- **C — Vector-shard tagging** (embedder id + dim): so a dimension/space change is **detected**,
  never silently mixing vector spaces (the stub-64 vs SigLIP-1152 trap in CLAUDE.md). *Highest value:
  it prevents silently-wrong search **today**, independent of corpus size.*

## 2. Current state (grounded in code)

| Concern | Today | Gap |
|---|---|---|
| Relational rows | `catalog.db`: `videos` + per-role tables, keyed by `video_id`. Schema-versioned. | No table records **which model** wrote any role's rows (only `videos.last_ingest_run_id`, a trace id). |
| One table, two roles | `segments` = scene (R1) **+** caption (R4); `transcripts` = STT (R8) **+** speaker (R9). | Provenance must key on **role**, not table. |
| Vector shards | per-video `vectors.npz`/`.json` (visual) + `text_vectors.npz` (semantic), via `NumpyFlatVectorStore`. | **No embedder/dim tag.** `add()` guards dim only *within* a store. |
| Cross-shard search | `ShardedVectorStore` globs `*/vectors.npz`, searches each, merges by score. | **No cross-shard guard**: same-dim-different-model mixes silently; different-dim **crashes** `vecs @ q`. |
| Model identity | `RoleConfig.model` = a string id (`siglip`, `qwen2.5-vl-7b`). No version field; `weights` in `load`. | "model/version" identity needs a stable definition. |
| Reprocess | `reingest_video` = remove all rows+shard+dir (keep media) → re-run the **whole** pipeline. | No "role X only", no "stale videos only". `text_index.py` rebuilds one shard standalone (a template). |

**Role → output store (the provenance surface):** R1→`segments`(boundaries); R2→`vectors.npz`;
R4→`segments.caption`; R5→`object_detections`; R6→`object_tracks`; R7→`action_events`;
R8→`transcripts`; R9→`transcripts.speaker`; R10→`ocr_results`; text_embedder→`text_vectors.npz`.
R11 (reasoner) is on-demand (no persistent rows) → out of scope.

---

## 3. Sub-problems (each ≈ one task-commit)

Order: **C first** (highest value, mostly independent, fixes the mixed-dim crash) → **A** (unblocks B)
→ **B**.

### C — Vector-shard tagging
- **TAG-1 · Shard `meta` storage.** Extend `NumpyFlatVectorStore` to carry a `meta` dict, persisted
  in the `.npz` (a second entry) and loaded back; untagged shards load `meta=None`. *Pure storage.*
- **TAG-2 · Stamp at write time.** `ingest.py` (visual) + `text_index.py` (text) write the shard
  `meta` = `{embedder: <model id>, dim: <D>}` from the active config. *(Bundled with TAG-1 as the
  first increment — a shard that stores but never records its identity is inert.)*
- **TAG-3 · Query-time guard.** `ShardedVectorStore` receives the current embedder identity, **skips**
  shards whose tag ≠ current (dim OR model), returns a skipped count. Replaces the mixed-dim crash /
  silent-mix with graceful degradation + a surfaced note.
- **TAG-4 · Legacy backfill — DISSOLVED (not needed).** TAG-3 already dim-guards untagged legacy
  shards (`_compatible` admits an untagged shard only on a dim match), so stamping them
  `{unspecified, dim}` changes nothing and can't recover the true embedder anyway. The real re-tag of
  a legacy shard is **`va reingest`** (re-embeds + tags via TAG-2). No backfill command.

### A — Provenance (per video × role)
- **PROV-1 · Identity/fingerprint helper** *(the general form; the shard tag uses the simpler
  `{model, dim}`).* `provenance.fingerprint(role, RoleConfig)` → `{model, hash(salient params)}`.
- **PROV-2 · Table + migration + store.** `role_provenance(video_id, role, model, fingerprint, fps,
  run_id, row_count, produced_at)` via a `SCHEMA_VERSION` bump; a `ProvenanceStore`. (No `dim` column —
  the vector-space dim lives on the TAG-2 shard tag; `fps` records the run-arg the fingerprint can't.
  `va remove` purges the table via `_ROLE_TABLES`.)
- **PROV-3 · Stamp during ingest.** Upsert a `(video, role)` row after each best-effort role step.
  Also record run-time args that change output but are **not** in `(role, cfg)` — notably the ingest
  sampling **`fps`** (which frames Roles 2/5/6/7 see) — as a column on the row, so PROV-4 can tell a
  corpus extended at a different fps apart. (Decide these `role_provenance` columns in PROV-2.)
- **PROV-4 · Report.** `va provenance <video>` / `va stale` — recorded vs current model per role.

### B — Batch reprocess *(depends on A; visual depends on C)*
- **RPRC-1 · Per-role re-run entry points** (phased: visual, text, caption first — they have
  standalone code; then leaf roles). *Give each embedder a `model_id` property so a reprocess shard
  is tagged with the embedder it actually used; until then an injected embedder without one tags
  `unknown` (honest — TAG-3 skips it — rather than a config-derived tag that could misdescribe it).*
- **RPRC-2 · Dependency-aware invalidation** (R1→R4/5/6/7; R5→R6; R8→R9).
- **RPRC-3 · Selection + orchestration.** `va reprocess --role X [--dry-run] [--all-stale]
  [--video V]`; resumable + per-video atomic (write rows THEN provenance); whole-video fallback.

### X — Cross-cutting
- **X-1 · Docs** (each commit, per the committer/reviewer doc-check): CLAUDE.md "two things that trip
  you up" #2 *is* the vector-space trap → note TAG-3 guards it; COORDINATION.md for shard-format + new
  table (shared with the web agent); this plan's status.
- **X-2 · Tests** (offline/stub): tag round-trip; mismatched shard skipped not crashed; provenance
  stamped on ingest; `--dry-run` lists exactly the stale set; reprocess updates provenance; backfill
  is dim-only.

## 4. Dependency graph & sequence
```
TAG-1+2 ─► TAG-3            (TAG-4 dissolved — untagged shards already dim-guarded)
PROV-1 ─► PROV-2 ─► PROV-3 ─► PROV-4 ─► RPRC-2 ─► RPRC-3
                    RPRC-1 ─────────────────────┘
```
Ship order: **TAG-1+2 → TAG-3** (corruption guard; TAG-4 dissolved) → **PROV-1 → PROV-2 → PROV-3 →
PROV-4** (stale report) → **RPRC-1/2/3** (selective reprocess).

## 5. Decisions (LOCKED to the recommended default; revisit trigger noted)

- **D1 — Provenance identity:** model-id **+ salient-params fingerprint** (embedders: weights+dim;
  captioner/action: model+vocab; **exclude** device/dtype/batch). *Revisit if the fingerprint proves
  too coarse/fine in practice.*
- **D2 — Shard tag storage:** **inside the `.npz`** (a second entry; atomic with the shard). *Revisit
  if we move off numpy shards (Milvus).* 
- **D3 — Query mismatch policy:** **skip mismatched shards + warn + surface a count** (also fixes the
  crash). *Revisit if silent partial results ever mislead — could escalate to hard-fail via a flag.*
- **D4 — Legacy backfill: DISSOLVED.** TAG-3's untagged dim-guard already covers legacy shards
  (dim-match, best-effort), so a `{unspecified, dim}` backfill is a no-op. A legacy shard gets a real
  tag only via **`va reingest`** (re-embed + tag). Residual honest gap: a same-dim different-model
  *legacy* shard can't be caught — inherent, documented.
- **D5 — Reprocess granularity:** **role-scoped for the 3 roles with standalone code (visual, text,
  caption) + whole-video `reingest` fallback for the rest.** *This is a SCOPE CAP: full per-role
  reprocess for all 10 roles is deferred until a real model change demands each.*
- **D6 — Provenance role scope:** roles 1,2,4,5,6,7,8,9,10 + text_embedder; reasoner excluded.
  *Correction: the reasoner (Role 11) is NOT row-free — deep-scan persists VLM micro-captions in the
  `observations` cache. It stays out of the provenance table, but its cache would otherwise go stale
  on a model upgrade (a missed stale), so `deep_scan.py` folds the **captioner + reasoner
  fingerprints** into its `prompt_key`/`map_key` — an upgrade re-runs the sweep instead of serving old
  code-counted answers. B's selective reprocess must therefore also purge `observations`, not just the
  role tables.*

## 6. Not building yet
No Postgres/ANN; no per-role reprocess for roles without a pending model change (YAGNI); one
canonical workdir (`.va-shots`), no cross-workdir provenance.

## 7. Status
- **2026-07-30:** plan landed. **C (shard tagging) COMPLETE** — TAG-1+2 (shards record
  `{embedder, dim}`; `tests/test_shard_tagging.py`) + TAG-3 (query-time guard skips mismatched
  shards, retrieval falls back to lexical with a surfaced note; `tests/test_shard_guard.py`).
  **TAG-4 dissolved** — TAG-3's untagged dim-guard made the backfill redundant; `va reingest` is the
  real re-tag.
- **2026-07-30:** **A (provenance) started — PROV-1 DONE.** `va.provenance.role_fingerprint(role, cfg)`
  computes a stable output-only identity `{model, fingerprint}` per role (model + `weights` override
  + object-detector/action-recognizer vocab; `device`/`dtype`/batch excluded); `tests/test_provenance.py`.
- **2026-07-30:** **PROV-2 DONE** — `role_provenance` table (`(video_id, role)` PK: model, fingerprint,
  fps, run_id, row_count, produced_at) via a schema **v2** migration on the §6-a runner +
  `ProvenanceStore.record()/.get()`; `tests/test_provenance_store.py`. The `fps` column captures the
  run-arg the fingerprint can't (review note).
- **2026-07-30:** **PROV-3 DONE** — ingest stamps `role_provenance` per role at the end of a
  successful run (`ingest._record_provenance`, driven by `provenance.PROVENANCE_ROLES` so write/read
  can't drift; best-effort). **Roles whose step FAILED are not stamped** — absent = stale, so a
  transient failure is reprocessed rather than masked as current. The fingerprint is computed from a
  **config pinned at role-launch time** (not a fresh `load_config()` at ingest end), so a mid-ingest
  `roles.yaml` edit degrades to a safe false-stale instead of stamping old-model rows with the new
  fingerprint (a missed stale). Deep-scan's `observations` cache keys also fold in the captioner +
  reasoner fingerprints, so a model upgrade re-runs the sweep. `tests/test_provenance_ingest.py`,
  `tests/test_deep_scan.py`. Next: **PROV-4** (`va stale` report).
- **2026-07-30:** **PROV-4 DONE — pillar A (provenance) COMPLETE.** `va stale [--role R]`
  (`pipeline/stale.py::stale_report`) lists DONE videos whose recorded fingerprint != the current
  config's per role (missing/unstamped = stale; non-done videos skipped — they need re-ingest, not a
  role reprocess). The report prints the active config dir/profile/embedder it compared against, so a
  forgotten `VA_CONFIG_DIR` (stub vs real) is self-evident before anyone acts on the `va reingest`
  remedy. Each stale row also surfaces its **recorded ingest fps** (`recorded_fps`): fps is a run arg
  with no config baseline, so it is REPORTED not compared (staleness stays fingerprint-only), but
  showing it lets a reprocess preserve the frame density Roles 2/5/6/7 saw — `va reingest` defaults to
  fps=1.0, so the remedy tells the user to pass `--fps <recorded>`. (Auto-preserving fps across a
  reprocess is pillar B's job.) `--role` is validated against `PROVENANCE_ROLES` at both the CLI (`choices`) and the library
  (`ValueError`) so an unstamped role (e.g. the reasoner) or a typo can't silently report every video
  stale. `tests/test_stale.py`. Read-only; drives pillar B's selective reprocess next.
  **SCOPE CUT (recorded, not silent):** the PROV-4 spec line names "`va provenance <video>` / `va
  stale`"; only the corpus-wide `va stale` was built. The per-video `va provenance <video>` inspector
  is DEFERRED (YAGNI until pillar B needs a single-video drill-down) — the data is already reachable
  via `ProvenanceStore.get(video_id)`; build the command when B does. Pillar B must NOT assume it exists.
- **2026-07-31:** **B (batch reprocess) STARTED — RPRC-3a: the dry-run selection front-end.**
  `va reprocess [--role R] (--all-stale | --video IDENT) [--dry-run]`
  (`pipeline/reprocess.py::plan_reprocess`) resolves the stale (video, role) work set a reprocess
  WOULD run — `stale_report` scoped by role/video, read-only. An explicit video scope is REQUIRED
  (`--all-stale` XOR `--video`, enforced at both argparse and the library) so a reprocess can never
  fan out across the whole corpus by omission; `--video` resolves idents via `lookup_video` (UUID /
  source_key / URL / path). **Execution is gated OFF:** without `--dry-run` the command prints the
  plan then refuses ("EXECUTION not implemented yet (RPRC-1) — NO changes made", rc=1) — never a
  silent no-op. The config-header foot-gun guard is shared with `va stale` (`cli._active_config_line`).
  `tests/test_reprocess.py`. Next: **RPRC-1** (per-role re-run entry points: visual/text/caption
  first) then **RPRC-2** (dependency-aware invalidation) to make the plan executable.
- **2026-07-31:** **RPRC-1a: the executor framework, wired for `text_embedder`.**
  `reprocess.py::execute_reprocess(workdir, plan)` runs each stale role that has a reprocessor and
  restamps its provenance — **rows/shard FIRST, provenance SECOND**, so a crash between them leaves the
  role stale (safe to retry), never falsely current. Resumable: a reprocessor that raises is recorded
  as failed (no restamp) and does not abort the batch; the restamp preserves the recorded ingest fps.
  `va reprocess` (no `--dry-run`) now executes: `text_embedder` re-runs in place
  (`text_index.backfill_text_index` rebuilds + re-tags the `text_vectors` shard); every other stale
  role is SKIPPED with a `va reingest` pointer (`reprocessable_roles()` is the wired set — D5 scope
  cap: only roles with standalone code). `tests/test_reprocess.py`. Next: **RPRC-1b** (visual: re-sample
  + re-embed + re-tag the `vectors` shard), **RPRC-1c** (caption: re-caption segments, then rebuild the
  text index AND purge the deep-scan `observations` cache — the D6 note), then **RPRC-2**
  (dependency-aware invalidation: R1→R4/5/6/7, R5→R6, R8→R9).
- **2026-07-31:** **RPRC-1b: `visual_embedder` wired.** `reprocess.reindex_visual(video, video_dir, fps)`
  re-samples frames at the video's RECORDED fps and re-embeds the `vectors` shard. Because visual
  embedding DEPENDS on the sampling density, the reprocessor reads the fps from provenance and REFUSES
  when it's unknown (→ `va reingest --fps <N>`) — never silently re-embeds at a different density. It
  embeds ONLY (no Role-5 detection), so it's a standalone re-embed, not an extraction of ingest's
  single decode+embed+detect pass. Durability: builds to a temp `vectors_rebuild` shard and
  `os.replace`-swaps it in only on full success, so a failed re-embed leaves the prior shard (visual
  search survives). `tests/test_reprocess.py`. Next: **RPRC-1c** (caption + `observations` purge) then
  **RPRC-2**.
- **2026-07-31:** **execute now requires `--yes`.** `va reprocess` OVERWRITES real data in place, so
  unlike read-only `va stale` its config header alone is too weak a guard: a forgotten `VA_CONFIG_DIR`
  would re-embed a whole real corpus with the stub (hours of GPU to recover). Execution now requires an
  explicit `--yes`; without it the command prints the plan (the review step) and refuses (rc=1). Also
  shard writes now put the `.npz` LAST (`NumpyFlatVectorStore.persist`; `reindex_visual` swaps `.json`
  first), so a `va serve` reader racing a rebuild can't cache an empty/torn shard under the final mtime.
  *Possible future hardening (deferred): refuse a stub-over-real-tagged shard overwrite from the shard
  tag, as defense-in-depth beyond `--yes` — deferred to avoid hardcoding which embedder ids are "stub".*
- **2026-07-31:** **RPRC-1c: `vlm_captioner` wired — RPRC-1 role reprocessors COMPLETE (text, visual,
  caption).** *(Sub-item DEFERRED: RPRC-1's "give each embedder a `model_id` property" is not done —
  config-driven reprocess tags shards correctly via `embedder_id`, but an INJECTED embedder without a
  `model_id` still tags `unknown` (honest — TAG-3 skips it — per `text_index.py`). Add the property when
  an injected-embedder reprocess path actually needs exact tags.)*
  `_reprocess_vlm_captioner` re-captions each segment's keyframe into `segments.caption`
  (caption-all-first, so a captioner failure overwrites nothing), then propagates to the two caption
  dependents so the new captions actually surface: **rebuilds the text index** (captions are a text
  modality — skipping it would leave text search on the OLD captions) and **purges the deep-scan
  `observations` cache** (`ObservationStore.purge`; its sweep uses the captioner — the D6 note). This is
  the first wired role WITH dependents, so it does the R4→text_embedder / R4→deep-scan propagation
  inline; RPRC-2 will formalize dependency-aware invalidation for the rest (R1→R4/5/6/7, R5→R6, R8→R9)
  and can dedupe the text-index rebuild when both caption and text_embedder are stale (today they'd each
  rebuild it — redundant but correct). `tests/test_reprocess.py`. Next: **RPRC-2**, then the pillar-B PR.
