# Solution Code Hike — Video Analytics ("Ctrl-F for Video")

*A guided, detailed walkthrough of everything built so far: the code, the directory
structure, the 11-role pipeline, the stub/real backend split, the retrieval + reasoning
layers, traceability, and how it all runs on the DGX Spark.*

> **Updated: 2026-07-20** — reflects the current codebase: **10 of the 11 roles** implemented
> (only Role 3, cross-modal audio, remains), layout v2 (one shared catalog DB + per-video
> shards), the retrieval layer (semantic text index + reranker + VLM verification), the Role 11
> reasoning/`ask` pipeline, the VA_TRACE traceability layer, a FastAPI web UI, and the
> performance work (decode-once, SQLite WAL, vector-shard cache). ~**8,785 LOC**, **157 tests**.
>
> *Originally written 2026-06-06 for Milestone M1 (Role 2 only, 34 tests). The two dated
> addenda at the end (§16 Role-11, §17 Role-10) are preserved as point-in-time findings.*

*Companion to: [CLAUDE.md](CLAUDE.md) (current commands + gotchas — the source of truth),
[plan.md](plan.md), [video-analytics-solution-architecture.md](video-analytics-solution-architecture.md),
[video-analytics-model-analysis.md](video-analytics-model-analysis.md) (per-role model choices),
[traceability-plan.md](traceability-plan.md), [performance-and-productization-plan.md](performance-and-productization-plan.md).*

---

## Table of Contents

1. [What we built (one paragraph)](#1-what-we-built-one-paragraph)
2. [The machine & environment](#2-the-machine--environment)
3. [The big picture: two data flows over shared stores](#3-the-big-picture-two-data-flows-over-shared-stores)
4. [Directory structure (annotated)](#4-directory-structure-annotated)
5. [The 11 roles: interface → stub → real backend](#5-the-11-roles-interface--stub--real-backend)
6. [The hosting-agnostic design (the spine)](#6-the-hosting-agnostic-design-the-spine)
7. [Storage: one correlation DB + sharded vectors](#7-storage-one-correlation-db--sharded-vectors)
8. [The retrieval layer (semantic search, rerank, verify)](#8-the-retrieval-layer-semantic-search-rerank-verify)
9. [Role 11: the `ask` reasoning pipeline](#9-role-11-the-ask-reasoning-pipeline)
10. [Traceability (VA_TRACE)](#10-traceability-va_trace)
11. [Performance work](#11-performance-work)
12. [The web UI](#12-the-web-ui)
13. [How models get downloaded & loaded on the GPU](#13-how-models-get-downloaded--loaded-on-the-gpu)
14. [Testing approach](#14-testing-approach)
15. [Validation on real footage (Role 2 canonical example)](#15-validation-on-real-footage-role-2-canonical-example)
16. [What is NOT done yet](#16-what-is-not-done-yet)
17. [How to reproduce](#17-how-to-reproduce)
18. [Addendum (2026-06-10): a model-verification finding worth remembering](#18-addendum-2026-06-10-a-model-verification-finding-worth-remembering)
19. [Addendum (2026-06-12): Role 10 (OCR) and the runtime-vs-model distinction](#19-addendum-2026-06-12-role-10-ocr-and-the-runtime-vs-model-distinction)

---

## 1. What we built (one paragraph)

A working **"Ctrl-F for Video"**: ingest a video (YouTube URL *or* local file) once, then
search *what's in it* with natural language — visually ("red sports car"), by speech ("the
budget", filterable by speaker), by scene caption, by on-screen text (OCR), by recognized
action, by object appearance/count, or by asking a **reasoned, cited question** ("what color
is the car?"). Ten of the eleven planned roles are implemented — **Role 1** (scene detection),
**2** (visual embedding), **4** (VLM captioner), **5** (object detector), **6** (object
tracker), **7** (action recognizer), **8** (speech-to-text), **9** (speaker diarizer), **10**
(OCR), and **11** (reasoning/planner) — only **Role 3** (cross-modal audio) remains. Every role
follows the same pattern: a dependency-free **stub** so the whole pipeline + **157 tests** run
offline with no GPU/network, plus a **real** backend behind an optional extra. It runs on the
DGX Spark's GB10 GPU with the real models, and ships a FastAPI web UI (ingest, play,
click-to-seek search). ~**8,785 LOC** of small, independently testable Python.

---

## 2. The machine & environment

| Property | Value |
|---|---|
| Machine | **DGX Spark** — NVIDIA **GB10** (Grace-Blackwell), **128 GB unified** memory (≈119 GB usable, zero-copy CPU↔GPU) |
| CPU | 20 cores (10 Cortex-X925 + 10 A725) |
| Architecture | **aarch64** (ARM64) — matters for wheel selection |
| OS / Python | Linux, **Python 3.12** |
| Isolation | A project-local **`.venv`** (editable install) |
| GPU available to torch | **Yes** — `torch.cuda.is_available() == True`, device `NVIDIA GB10` |

**Key installed versions (verified 2026-07-20):**

| Package | Version | Role it backs |
|---|---|---|
| torch | **2.12.0+cu130** | all real GPU models |
| transformers | **5.10.2** | SigLIP, Qwen2.5-VL, BGE (v5 API differs from v4 — see §18/gotchas) |
| numpy | 2.3.5 | vector math + flat index |
| pydantic | 2.13.4 | all data contracts |
| yt-dlp | 2026.3.17 | YouTube acquisition |
| imageio-ffmpeg | 0.6.0 (bundles **ffmpeg 7**) | frame decode + yt-dlp merge (no system ffmpeg) |
| openai-whisper | 20250625 | Role 8 speech-to-text |
| pyannote.audio | 4.0.4 | Role 9 diarization (4.x required — see gotchas) |
| ultralytics | 8.4.64 | Role 5 YOLO-World |
| rapidocr / onnxruntime | 3.8.3 / 1.26.0 | Role 10 OCR (PP-OCR on onnxruntime, not paddle — see §19) |
| fastapi | 0.136.3 | web UI |
| threadpoolctl | 3.6.0 | diarizer OpenBLAS deadlock workaround (§11) |

**Dependency tiers** — core stays tiny so tests never need the heavy stack. `pip install -e .`
gets numpy/pydantic/pyyaml/pillow/imageio/imageio-ffmpeg/yt-dlp — enough to run the *entire*
pipeline on stub backends. Each real model is its own extra: `[siglip]`, `[scenedetect]`,
`[text-embed]`, `[rerank]`, `[whisper]`, `[diarize]`, `[qwenvl]`, `[yolo]`, `[track]`,
`[action]`, `[ocr]`, `[web]`, `[dev]`.

---

## 3. The big picture: two data flows over shared stores

Everything is still **two pipelines over shared stores**, joined by `video_id` — but ingest now
fans out to nine roles and query has many entry points unified by a planner.

### Ingest (write path, `pipeline/ingest.py`)
```
URL / path
   │  resolve_source(uri) → VideoSource.resolve()  (cheap; stable source_key for dedup)
   ▼
Catalog.get_or_create()          ← idempotency point (skip if already 'done')
   │  source.fetch()             (yt-dlp ≤480p, or locate local file) → probe metadata
   ▼
SceneDetector.detect()           → segments table            (Role 1 — temporal backbone)
   ▼
VLMCaptioner.caption() per seg   → segments.caption           (Role 4, best-effort)
   ▼
SpeechToText.transcribe()        → TranscriptStore            (Role 8, best-effort)
   └─ SpeakerDiarizer.diarize() → assign_speakers() joins turns onto lines (Role 9)
   ▼
OcrReader.read()                 → ocr_results                (Role 10, best-effort)
   ▼
ActionRecognizer.recognize()     → action_events             (Role 7, best-effort)
   ▼
── decode the file ONCE at N fps, fan the frame stream to: ──
   VisualEmbedder.embed_image()  → per-video vector shard     (Role 2, critical)
   ObjectDetector.detect()       → frames_dets                (Role 5, best-effort)
   ▼
ObjectTracker.track()            → object_tracks + object_detections (Role 6, best-effort)
   ▼
index_text()                     → per-video text_vectors shard (semantic index over
   ▼                                caption/transcript/OCR/action text)
Catalog.set_status(done, run_id) ← marks done; stamps the ingest's trace run_id
```
Every role after Role 1 is **best-effort**: it's wrapped so a failure records a trace warning
and continues, never aborting the ingest (only Role 2 embedding is critical). The single decode
pass shared by Role 2 + Role 5 is the "decode-once" perf win (§11).

### Query (read paths)
There are several typed query paths, each its own CLI verb and pipeline module:

| CLI | Module | What it searches | Role |
|---|---|---|---|
| `va query` | `query.py` | visual frame embeddings (cosine) | 2 |
| `va caption` | `caption.py` | scene captions | 4 |
| `va transcript` | `transcript.py` | speech (`--speaker` filters) | 8 / 9 |
| `va ocr` | `ocr.py` | on-screen text | 10 |
| `va actions` | `actions.py` | recognized actions | 7 |
| `va objects` / `va count` | `objects.py` | object appearances / distinct instances | 5 / 6 |
| `va textsearch` | `text_search.py` | semantic index across all text modalities | retrieval layer |
| `va ask` | `ask.py` | **reasoned, cited answer** unifying all tiers | 11 |

The visual crux is unchanged: images and text embed into the **same** vector space, so search is
cosine nearest-neighbor between a text vector and pre-computed frame vectors. `va ask` (§9) is
the planner that unifies the tiers.

---

## 4. Directory structure (annotated)

`src/va/` is ~**8,785 LOC**. Grouped by layer (key files with LOC; `__init__` and tiny stubs
elided):

```
src/va/
├── cli.py                          (405)  18 subcommands (ingest/query/…/ask/serve/trace/bench)
├── configuration.py                 (65)  roles.yaml + active profile → RoleConfig
├── registry.py                     (337)  config → the right adapter, for all 13 roles+support
│
├── contracts/                            ── pydantic data contracts ──
│   ├── video.py (86) segment.py (28) embedding.py (33) transcript.py (20)
│   ├── detection.py (23) track.py (31) action.py (20) ocr.py (25) diarization.py (22)
│   └── query_plan.py (55) evidence.py (176)   ← the Role-11 runtime contracts
│
├── runtime/                              ── model lifecycle + observability ──
│   ├── device.py (31)   resolve cuda→cpu fallback; dtype
│   ├── manager.py (63)  ModelManager singleton: load-once cache + unload (GPU mem)
│   └── trace.py (327)   VA_TRACE-gated tracer → one readable .trace file per run
│
├── sources/            base.py (34) youtube.py (101) local.py (39) fixtures.py (29)
├── media/              frames.py (83) synth.py (101) audio.py (46)
│
├── roles/                                ── 14 Protocol interfaces (one per role) ──
│   scene_detector, visual_embedder, vlm_captioner, object_detector, object_tracker,
│   action_recognizer, speech_to_text, diarizer, ocr, reasoner,   (the 10 built roles)
│   text_embedder, reranker, vlm_verifier                          (retrieval-layer support)
│
├── adapters/<role>/                      ── interchangeable backends: *_inproc (stub|real) ──
│   scene_detector/    histogram (stub) · pyscenedetect (real)
│   visual_embedder/   hash (stub)      · siglip (real, 1152-d)
│   vlm_captioner/     color (stub)     · qwen (Qwen2.5-VL-7B)
│   object_detector/   color (stub)     · yolo_world (real)
│   object_tracker/    iou (default)    · bytetrack (real)
│   action_recognizer/ motion (stub)    · xclip (real, zero-shot)
│   speech_to_text/    sidecar (stub)   · whisper (real)
│   speaker_diarizer/  sidecar (stub)   · pyannote (real)
│   ocr/               sidecar (stub)   · rapidocr (real)
│   reasoner/          rule (stub)      · qwen · claude_cli (claude-code) · claude_api (placeholder) + prompts.py
│   text_embedder/     hash (stub)      · transformers (BGE-M3)
│   reranker/          wordoverlap (stub) · cross_encoder (BGE-reranker)
│   vlm_verifier/      passthrough (stub) · qwen (SR.6)
│
├── storage/
│   ├── structured/                       ── one SQLite correlation DB (catalog.db) ──
│   │   schema.py (203)  8 tables + connect() (WAL) + additive migrations
│   │   catalog_sqlite (200) segments (109) transcripts (89) ocr (102)
│   │   actions (83) detections (138) tracks (95) observations (42)
│   └── vector/
│       numpy_flat.py (77)  brute-force cosine index (one shard)
│       sharded.py   (73)  many per-video shards as one logical index + mtime cache
│
├── pipeline/                             ── the two pipelines + support ──
│   ingest.py (319)  the write path above
│   query/caption/transcript/ocr/actions/objects.py   the typed read paths
│   text_index.py (98) text_search.py (54) retrieval.py (413)   the retrieval layer
│   ask.py (280) deep_scan.py (396) evidence.py (98) verify.py (157)  Role 11
│   diarize.py (34) trace_links.py (50) paths.py (67)
│   bench.py (170) manage.py (102) migrate.py (87)   ops
│
└── web/                app.py (268)  FastAPI: ingest/play/search    jobs.py (201)  job queue
```

Config + tests:
```
config/                    default: every role = its STUB (tests/CI, no GPU/network)
run-siglip/config/         real SigLIP + Whisper (a separate dir so tests keep the stub)
run-claude/config/         all real models + reasoner=claude-code (the full appliance config)
config/profiles/dgx-spark.yaml   per-model load params (device/dtype/weights)
tests/                     41 files, 157 tests (offline) + golden_queries/ (gated, real-model)
```

---

## 5. The 11 roles: interface → stub → real backend

Every role is three seams (Protocol in `roles/`, backends in `adapters/<role>/`, chosen by
`registry.py` from `config/roles.yaml`). Backends today:

| # | Role | Protocol | Stub (default) | Real backend (extra) |
|---|---|---|---|---|
| 1 | Scene detection | `scene_detector.py` | `histogram` (content-aware, dep-free) | `pyscenedetect` `[scenedetect]` |
| 2 | Visual embedding | `visual_embedder.py` | `hash` (color-aware, 64-d) | `siglip` SO400M, 1152-d `[siglip]` |
| 3 | Cross-modal audio | — | — | **not built** (last role) |
| 4 | VLM captioner | `vlm_captioner.py` | `color` (dominant color) | `qwen2.5-vl-7b` `[qwenvl]` |
| 5 | Object detector | `object_detector.py` | `color` (color regions) | `yolo-world` open-vocab `[yolo]` |
| 6 | Object tracker | `object_tracker.py` | `iou` (dep-free assoc.) | `bytetrack` `[track]` |
| 7 | Action recognizer | `action_recognizer.py` | `motion` (pixel-diff) | `xclip` zero-shot `[action]` |
| 8 | Speech-to-text | `speech_to_text.py` | `sidecar` (`.transcript.json`) | `whisper` `[whisper]` |
| 9 | Speaker diarizer | `diarizer.py` | `sidecar` (`.diarization.json`) | `pyannote` `[diarize]` |
| 10 | OCR | `ocr.py` | `sidecar` (`.ocr.json`) | `rapidocr` (PP-OCR/ONNX) `[ocr]` |
| 11 | Reasoning/planner | `reasoner.py` | `rule` (regex/heuristic) | `qwen2.5-vl-7b`, `claude-code`, `claude-api`* |

*`claude-api` is a placeholder pending the ANTHROPIC_API_KEY decision; it raises with guidance.

**Retrieval-layer support roles** (not in the canonical 11 — they power semantic search + `ask`):
`text_embedder` (`hash` stub / `BAAI/bge-m3`), `reranker` (`word-overlap` stub /
`BAAI/bge-reranker-v2-m3`), `vlm_verifier` (`passthrough` stub / `qwen2.5-vl-7b`, SR.6).

**Backend caveats worth knowing** (see CLAUDE.md for the full list):
- **Scene detector default is `histogram`**, which merges montage cuts (measured 6 vs 71
  segments on one clip); `run-*/config` uses `pyscenedetect`.
- **`iou` tracker over-counts** fast objects at 1 fps (no motion model) — Ferrari clip: iou 38
  "cars" vs bytetrack 6. Use `bytetrack` for real footage.
- **X-CLIP scores a fixed ingest-time vocabulary** and always picks the least-bad label; an
  abstention foil (`NO_ACTION`) parks probability when nothing fits. Arbitrary-action queries
  need query-time recognition (not built).

---

## 6. The hosting-agnostic design (the spine)

The architecture's hardest constraint is unchanged: *the DGX Spark may not hold every model, so
any role must run locally OR remotely without changing caller code.* Three seams per role
enforce it:

1. **Role interface** — `roles/<role>.py` is a `Protocol`; callers depend only on it.
2. **Adapters** — `adapters/<role>/*_inproc.py` are interchangeable backends (in-process today;
   `http_client` / cloud clients slot in identically).
3. **Registry** — `registry.py` reads config and returns the right adapter. Swapping a backend is
   a **one-line edit** in `config/roles.yaml`; no pipeline code changes.

`configuration.py` folds `roles.yaml` (which backend+model per role) with the active
`config/profiles/<name>.yaml` (per-model device/dtype/weights) into one `RoleConfig`.
`VA_CONFIG_DIR` overrides the config dir — which is exactly how `run-siglip/config` and
`run-claude/config` select real models without touching the stub config the tests rely on.
`runtime/ModelManager` (singleton `MANAGER`) loads each model once and caches it — so, e.g., the
Role-4 captioner, the Role-11 Qwen reasoner, and the SR.6 verifier all share **one** Qwen2.5-VL
bundle with no extra VRAM.

---

## 7. Storage: one correlation DB + sharded vectors

**Layout v2** (per workdir):
```
<workdir>/
├── catalog.db                        ONE shared SQLite DB for ALL videos (the correlation store)
├── videos/<key16>-<slug>/            per-video artifacts
│   ├── media.<ext>                   the downloaded/located file
│   ├── vectors.npz                   visual embedding shard (Role 2)
│   ├── text_vectors.npz              semantic text shard (retrieval layer)
│   └── keyframes/                    extracted frames for the reasoner
├── cache/                            transient downloads
└── traces/                           VA_TRACE .trace files (§10)
```

**The structured store** (`storage/structured/`) is one SQLite file whose schema
(`schema.py`) has **8 tables**, all keyed by `video_id`: `videos` (catalog/dedup) + one per role
group — `segments` (Role 1, + `caption` for Role 4), `object_tracks` + `object_detections`
(Roles 5/6), `action_events` (Role 7), `transcripts` (Roles 8/9, with `speaker`), `ocr_results`
(Role 10), and `observations` (the deep-scan cache, §9). All tables are created up front so
complex questions can correlate roles via temporal SQL joins on `video_id` + time. `connect()`
opens with **WAL + synchronous=NORMAL** (readers and the ingest writer no longer block) and
ensures the schema once; additive columns (e.g. `last_ingest_run_id`) are backfilled onto
pre-existing DBs via a guarded `ALTER`.

**Vectors** live separately. `numpy_flat.py` is the brute-force cosine index (L2-normalize on
insert → search is one matrix-vector dot + top-k). `sharded.py` presents the per-video
`vectors.npz` shards as **one logical index** (search spans all videos) and caches each loaded
shard keyed by file mtime, so repeat queries don't re-read from disk. Everything is behind
interfaces so Postgres / Milvus / an ANN engine can swap in later.

---

## 8. The retrieval layer (semantic search, rerank, verify)

Beyond the per-modality searches, a retrieval layer (the "SR" tiers) powers `va textsearch` and
feeds `va ask`:

- **Write (`text_index.py`)** — at ingest, embeds the caption / transcript / OCR / action text
  into the per-video `text_vectors.npz` shard, tagged with its `source_role` so a hit knows which
  modality it came from.
- **Read (`retrieval.py`, `text_search.py`)** — `retrieve()` gathers candidates across tiers
  (visual, semantic-text, structured object/count), **fuses** them (a rerank-weighted score),
  and applies a **relevance gate** (drops low-cosine/low-rerank items). The cross-encoder
  **reranker** re-orders by true relevance; the **VLM verifier** (SR.6) re-checks the top-k
  frames for attribute/negation/composition the embedding can't handle (Qwen re-checks
  SigLIP/YOLO results). Verification is selective — blanket verification erodes recall — and is
  auto-enabled by the Role-11 planner (`needs_visual_verification`) or manually via
  `va query --verify`.

---

## 9. Role 11: the `ask` reasoning pipeline

`va ask "<question>"` (`pipeline/ask.py`) produces a reasoned, cited answer with hyperlinked
timestamps (YouTube `&t=` deep links):

```
plan (LLM call 1)  → QueryPlan (which tiers, search terms, deep-scan?)
  └ rule-floor: a closed regex heuristic ORs deep_scan into any weak/offline plan
retrieve()         → fused, ranked, gated Evidence bundle (§8)
[deep scan]        → Tier 5b: exhaustive per-frame micro-captions cached in `observations`
collect keyframes  → per-video keyframes/ at the top moments
reason (LLM call 2, sees the images)  → Answer
  └ self-escalation: an insufficient sparse answer triggers ONE deep-scan re-run
render             → hyperlinked, cited text (leads with a code-counted line when a deep scan ran)
```

Deep-scan triggers are defense-in-depth: the LLM planner (primary) + a closed regex floor (for
weak/offline planners) + self-escalation. Reasoner backends share the ModelManager: `rule`
(stub/fallback), `qwen2.5-vl-7b` (shares the Role-4 model — no extra VRAM), `claude-code`
(headless `claude -p` on the local subscription), `claude-api` (placeholder). LLM JSON is parsed
tolerantly (`prompts.parse_json_block`, `coerce_timestamp` — Qwen really emits `"3.5s"`);
unparseable output falls back to the rule reasoner. **Determinism ≠ correctness:** the deep-scan
was once reproducibly counting *camera cuts* (70–99) as "dress changes" (truth ~12–15), so
counting outputs are validated against ground truth, not just stability.

---

## 10. Traceability (VA_TRACE)

`runtime/trace.py` is a leaf observability sink, **gated by `VA_TRACE`, default OFF** — a normal
run does nothing and writes no files. When on, `traced_run()` brackets an ingest/query/ask run
and writes **one human-readable `.trace` file** per run (a header + one block per event; verbatim
reasoner input/output and tracebacks as real-newline blocks). What's instrumented:

- **Ingest** — a success event per role, and a visible **warn-with-traceback** when a best-effort
  role's swallowed `except` fires (a degraded ingest is now diagnosable, not silent).
- **Query/ask** — the plan, per-tier gather/verify/fuse/gate, keyframes, and the **verbatim**
  reasoner input + raw output.
- **Ingest↔query link** — each video's catalog row stores the ingest's `run_id`; a query/ask
  trace emits a `link/ingest_runs` event naming the ingests behind its data, so
  `va trace <run_id>` jumps from a surprising answer to the ingest (and any degradations) that
  produced it.

`va trace list / <id> / --last / prune` reads and renders them; the rendered view leads with a
degradation banner if any stage warned. Traces are local, gitignored artifacts; prune is opt-in.

---

## 11. Performance work

Current-code (pure-Python) wins, each measured with `va bench` (isolated cleared workdir, 5-run
averaged, across all corpus videos):

- **Decode-once fan-out** — Role 2 (embed) and Role 5 (detect) now share **one** decode pass
  instead of two over identical frames. **1.32× total ingest** (every video 1.18–1.53× faster),
  outputs identical.
- **SQLite WAL + `connect()` hardening** — a shared connect helper (WAL, synchronous=NORMAL,
  schema-ensured-once) + batched `get_many()`. Shipped honestly as a **concurrency foundation**,
  not a throughput claim (the DB isn't the bottleneck on NVMe): measured 7/400 concurrent reads
  blocked without WAL → 0 with (matters for the web server / multi-tenant appliance).
- **Vector-shard cache** — per-video shards cached by mtime; multi-video query p50
  **12.6 → 0.33 ms (≈39×)** on an 8-shard corpus.
- **Diarizer OpenBLAS deadlock workaround** — real ingests intermittently hung forever loading
  the pyannote pipeline (small-matrix `inv`/`eigh` in VBx setup deadlocking inside OpenBLAS under
  thread-pool oversubscription: `threadpool_info()` showed four all-cores pools — two OpenBLAS +
  two libgomp — on 20 cores). Fixed with a **scoped** `threadpool_limits(1, "blas")` around only
  the model load — no global serialization; the rest of the pipeline keeps full parallelism.

Deferred as future/scale-gated: the ANN vector-engine swap, batched/quantized serving, and the
Go/Rust control-plane + camera edge (see `performance-and-productization-plan.md`). A separate
`parallelization-analysis.md` (CPU/GPU overlap + a thread-budget primitive) is scoped but parked.

---

## 12. The web UI

`va serve` (`web/app.py`, FastAPI + `web/jobs.py` job queue) serves a browser UI on the LAN:
ingest a URL/file (async job), list/play videos, and search-with-click-to-seek across all four
text modalities plus visual. Normalized hit shape `{video_id, t, score, label}` so the player
seeks to `t`. Run it with the real backends: `VA_CONFIG_DIR=run-claude/config va serve --port 8080`
(`--trace` sets VA_TRACE for the served runs). See `web-frontend-plan.md`.

---

## 13. How models get downloaded & loaded on the GPU

The machinery that turns "code" into "AI", model-agnostically:

1. **Install the extra** — e.g. `pip install -e '.[siglip]'` pulls torch 2.12.0+cu130 (a CUDA
   build for the GB10's aarch64) + transformers.
2. **Resolve where to load** — `runtime/device.py` returns `cuda` when available (GB10) else
   `cpu`; the profile supplies `dtype` and the HF weights id.
3. **Download once** — first `_build()` fetches weights into `~/.cache/huggingface/hub/…`
   (SigLIP SO400M ≈3.3 GB); later loads read the cache.
4. **Load + cache** — `AutoModel.from_pretrained(...).to('cuda').eval()`, keyed in the
   `ModelManager` so every subsequent call reuses it. Shared-model roles (captioner / Qwen
   reasoner / SR.6 verifier) reuse **one** bundle.
5. **Inference** — frames batched (32) on GPU, results to CPU numpy, L2-normalized.

Adding a new role is the same recipe: a `*_inproc` adapter + `_build()`; device resolution,
config folding, and the manager are shared. **No system ffmpeg** — decode + yt-dlp merge use the
`imageio-ffmpeg` binary (symlinked as `ffmpeg`, passed via `ffmpeg_location`).

---

## 14. Testing approach

**157 tests** (+2 skipped), all offline (`.venv/bin/pytest -q`) — stub backends + synthetic
color clips (`media/synth.py`) assert real behavior with no GPU/network. 41 test files, one per
role/pipeline (`test_e2e`, `test_ask_e2e`, `test_retrieval_e2e`, `test_ocr_e2e`, `test_trace*`,
`test_web`, …). Determinism comes from the color-aware hash embedder (a red frame matches the
word "red") + sidecar stubs (transcript/diarization/OCR read a JSON sidecar).

**Golden-query fixtures** for real videos live in `tests/golden_queries/` (`<video_id>.md` human
+ `<video_id>.yaml` machine assertions), generated by a vision + adversarial-verify agent
workflow, split into `match` / `no_match` / future-role queries. Two **gated** harnesses run them
against a pre-ingested real-model workdir (`RUN_GOLDEN=1 VA_CONFIG_DIR=run-claude/config
GOLDEN_WORKDIR=.va-shots .venv/bin/pytest -m golden`): `test_golden_queries.py` (per-modality)
and `test_golden_ask.py` (deep-scan counts). Known model limits carry a strict `xfail` so a
better model alerts. **A green test can still be wrong** — a fixture audit found a hallucinated
"kitchen" match and a false-positive "grandstands" match; low-score vision-verified matches are
treated as audit candidates.

---

## 15. Validation on real footage (Role 2 canonical example)

The Role-2 validation from M1 still stands as the clearest illustration of genuine image↔text
recognition (not color matching), run with real SigLIP on the GB10:

**Positive — red Ferrari video (`GXPRSFL0UUA`, 188 frames):**

| Query | Top score | Moment | Verified |
|---|---|---|---|
| "red sports car" | +0.115 | 1:07 | real red Ferrari, side profile ✅ |
| "a red ferrari on a track" | +0.184 | 1:51 | Ferrari mid-corner ✅ |
| "a blue ocean" | +0.021 | 2:09 | irrelevant — lowest |

**Negative — cobra video (`xDerjsxFkb4`), isolated workdir:** the *same* "red sports car" query
scores **−0.025** (best frame is a cobra) vs **+0.115** on the Ferrari; "a snake" scores +0.144
(positive control). Relevant ≈0.11–0.18, irrelevant ≈0 or negative.

> **SigLIP scores are small in absolute terms** (sigmoid training objective) — rank and relative
> gap matter, not magnitude. There is still no hard relevance threshold, so a no-match query
> prints low/negative hits rather than nothing (visual match in the golden harness = strongest
> hit inside the time range ≥ 0.10, calibrated on the first real run). Later roles have their own
> validations: the golden queries (§14) and the two addenda below.

---

## 16. What is NOT done yet

- **Role 3 (cross-modal audio)** is the one unimplemented role — its driving use-case is
  "water sounds / waterfall vs ocean" queries (needs a CLAP/audio-embedding tier).
- **No remote/HTTP backend** yet — all adapters are in-process. The agnostic seam exists; the
  `http_client` adapter is unwritten.
- **No hard relevance threshold** on visual query — top-k always prints, even for no-match.
- **`claude-api` reasoner is a placeholder** pending the ANTHROPIC_API_KEY decision.
- **Vector store is brute-force numpy** — exact but O(N·D); the ANN swap is deferred/scale-gated
  until continuous camera ingest creates a million-vector corpus.
- **Traceability ingest-run links only exist for videos ingested with `VA_TRACE=1`** (default
  off), so the ingest↔query link is only as complete as your habit of tracing real ingests.

---

## 17. How to reproduce

```bash
# 0. setup + offline tests (no network/GPU — stub backends + synth clips)
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pytest -q                     # 157 pass, 2 skip

# 1. STUB pipeline on a synthetic clip
.venv/bin/python -c "from va.media.synth import write_color_video as w; \
  w('/tmp/c.mp4',[('red',(220,30,30),3.0),('blue',(30,30,220),3.0)],fps=10)"
.venv/bin/va --workdir /tmp/demo ingest /tmp/c.mp4
.venv/bin/va --workdir /tmp/demo query "red sports car"

# 2. REAL models on the GPU (downloads weights on first use)
.venv/bin/pip install -e '.[siglip]'    # or the full set of extras
VA_CONFIG_DIR=run-siglip/config .venv/bin/va --workdir .va \
    ingest "https://www.youtube.com/watch?v=GXPRSFL0UUA"
VA_CONFIG_DIR=run-siglip/config .venv/bin/va --workdir .va query "red sports car" -k 5

# 3. the full appliance config (all real models + claude-code reasoner) + a reasoned question
VA_CONFIG_DIR=run-claude/config .venv/bin/va --workdir .va ask "what color is the car?"

# 4. see what happened (traceability)
VA_TRACE=1 VA_CONFIG_DIR=run-claude/config .venv/bin/va --workdir .va ask "what color is the car?"
.venv/bin/va --workdir .va trace --last
```

> Switching the embedder changes the vector space (stub 64-d vs SigLIP 1152-d), so
> `va reingest <video>` after a model change, or use a fresh `--workdir`. `VA_CONFIG_DIR` keeps
> real-model configs apart from the stub config the tests rely on.

---

## 18. Addendum (2026-06-10): a model-verification finding worth remembering

*(This addendum records a finding from Role 11 validation that changes how we verify model
output. It predates most of the roles above but the lesson is load-bearing.)*

### Two reasoner backends, same question, contradictory answers

Role 11 (`va ask`) was validated on the Ferrari video with the question *"What's the color
of the t-shirt of the person entering the red car?"* — run through two interchangeable
backends over the **same evidence and the same keyframes**:

| Backend | Answer | Verdict |
|---|---|---|
| Qwen2.5-VL-7B (local GPU) | "**red** t-shirt" @0:03 | ❌ wrong |
| claude-code (Claude via subscription CLI) | "**white** t-shirt" @0:02 + 0:03 | ✅ correct (frame-verified) |

The keyframe at t=2 is unambiguous: a person in a **white** tee standing at the Ferrari's
open driver door. Qwen's error is a textbook **attribute-binding hallucination** — "red
car" in the question primed "red t-shirt." Claude additionally cited *two* stages of the
entry (standing at door → lowering into seat) and explicitly noted that the 38s/123s
keyframes (spectators) "don't bear on the question."

### The embarrassing half of the finding: verification bias

The first validation pass **wrongly confirmed Qwen's answer**. The verifier (me) looked at
the t=3 frame *after* reading Qwen's claim, saw a person bent into a sea of Ferrari-red,
and accepted "red-toned top — consistent." Only the cross-backend disagreement forced a
second, more careful look at the clearer t=2 frame.

**Rules adopted from this:**
1. **Verify against the source before reading the model's claim** — look at the frame
   first, write down what you see, then compare. Post-hoc checking inherits the model's
   framing.
2. **Cross-backend disagreement is a free verification signal.** Two backends answering
   the same evidenced question can't both be right when they conflict — running both and
   flagging conflicts is a cheap self-check (this became the SR.6 `--verify` idea).
3. **Model-quality ranking is task-dependent and must be measured, not assumed**: for
   attribute questions, claude-code > qwen-7B was only knowable from a real head-to-head.

This also retroactively justifies the golden-fixtures `provenance:` split
(`vision-verified` vs `model-regression`): model output used as its own ground truth is
exactly how the red-shirt error would have fossilized into the test suite.

---

## 19. Addendum (2026-06-12): Role 10 (OCR) and the runtime-vs-model distinction

Role 10 landed by the standard recipe (Protocol `roles/ocr.py`, sidecar stub, real
adapter, `OcrStore` over the pre-created `ocr_results` table, `va ocr`, a
`needs_ocr_search` tier flag through planner→assemble→`on_screen_text` evidence). Two
findings worth keeping:

- **The planned backend's MODEL was right; its RUNTIME was broken.** plan.md named
  PaddleOCR. paddlepaddle 3.2 installs cleanly on aarch64 (manylinux wheel) and even
  passes `paddle.utils.run_check()` — then its *inference* engine segfaults at predictor
  init, for every model/flag combination tried (PP-OCRv5/v6, mkldnn on/off, PIR off).
  The fix was to keep the PP-OCR det+rec models and swap the runtime: **RapidOCR** runs
  the same lineage as ONNX on onnxruntime. "Backend" is really two choices — weights and
  runtime — and the adapter seam lets you swap one without the other.
- **Language-specific rec models matter for retrieval, not just accuracy.** The default
  CH rec model read the cobra captions as `"boughit acobra"` — characters mostly right,
  *spacing* wrong, which breaks word-overlap search completely ("bought a cobra" shares
  zero tokens with it). The EN model reads `"bought a cobra"` @0.92. If OCR feeds a
  word-level index, tokenization quality IS retrieval quality.

OCR rows for already-ingested videos were backfilled without reingestion (the role only
needs `local_path` + the shared DB), and the Role-10 golden queries were promoted to
runnable in `tests/golden_queries/` (ferrari billboards, cobra captions, dresses title
card).

---

*End of hike. Current commands + the two things most likely to trip you up:
[CLAUDE.md](CLAUDE.md). Remaining: Role 3 (cross-modal audio); the ANTHROPIC_API_KEY decision
for the production `claude-api` reasoner; the deferred ANN / serving / control-plane work in
`performance-and-productization-plan.md`.*
