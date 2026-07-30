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
- **TAG-4 · Legacy backfill.** Stamp existing untagged shards with **dim** (certain) + **model =
  unspecified** (honest); dim alone still catches 64-vs-1152. `va reprocess --retag` or auto-on-load.

### A — Provenance (per video × role)
- **PROV-1 · Identity/fingerprint helper** *(the general form; the shard tag uses the simpler
  `{model, dim}`).* `provenance.fingerprint(role, RoleConfig)` → `{model, hash(salient params)}`.
- **PROV-2 · Table + migration + store.** `role_provenance(video_id, role, model, fingerprint, dim,
  run_id, produced_at, rows)` via a `SCHEMA_VERSION` bump; a `ProvenanceStore`.
- **PROV-3 · Stamp during ingest.** Upsert a `(video, role)` row after each best-effort role step.
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
TAG-1+2 ─► TAG-3 ─► TAG-4
PROV-1 ─► PROV-2 ─► PROV-3 ─► PROV-4 ─► RPRC-2 ─► RPRC-3
                    RPRC-1 ─────────────────────┘
```
Ship order: **TAG-1+2 → TAG-3 → TAG-4** (corruption guard) → **PROV-1 → PROV-2 → PROV-3 → PROV-4**
(stale report) → **RPRC-1/2/3** (selective reprocess).

## 5. Decisions (LOCKED to the recommended default; revisit trigger noted)

- **D1 — Provenance identity:** model-id **+ salient-params fingerprint** (embedders: weights+dim;
  captioner/action: model+vocab; **exclude** device/dtype/batch). *Revisit if the fingerprint proves
  too coarse/fine in practice.*
- **D2 — Shard tag storage:** **inside the `.npz`** (a second entry; atomic with the shard). *Revisit
  if we move off numpy shards (Milvus).* 
- **D3 — Query mismatch policy:** **skip mismatched shards + warn + surface a count** (also fixes the
  crash). *Revisit if silent partial results ever mislead — could escalate to hard-fail via a flag.*
- **D4 — Legacy backfill honesty:** **dim-only, model=unspecified** (can't know a legacy shard's
  model; guessing risks a false "matches current"). *Revisit never — honesty is the point.*
- **D5 — Reprocess granularity:** **role-scoped for the 3 roles with standalone code (visual, text,
  caption) + whole-video `reingest` fallback for the rest.** *This is a SCOPE CAP: full per-role
  reprocess for all 10 roles is deferred until a real model change demands each.*
- **D6 — Provenance role scope:** roles 1,2,4,5,6,7,8,9,10 + text_embedder; reasoner excluded.

## 6. Not building yet
No Postgres/ANN; no per-role reprocess for roles without a pending model change (YAGNI); one
canonical workdir (`.va-shots`), no cross-workdir provenance.

## 7. Status
- **2026-07-30:** plan landed. **C (shard tagging)** in progress: **TAG-1+2 DONE** — shards record
  `{embedder, dim}` via `NumpyFlatVectorStore.set_meta`/`.meta`, stamped at ingest (visual) +
  text-index (text); `tests/test_shard_tagging.py`. Next: **TAG-3** (query-time guard that skips
  mismatched shards), then **TAG-4** (legacy backfill).
