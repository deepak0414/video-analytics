# Implementation Plan: Video Analytics Platform ("Ctrl-F for Video")

*Created: 2026-06-04 | Status: Draft for iteration*
*Source: [write-a-plan.md](write-a-plan.md) | Architecture: [video-analytics-solution-architecture.md](video-analytics-solution-architecture.md) | Models: [video-analytics-model-analysis.md](video-analytics-model-analysis.md)*

**Locked decisions (iteration 1):** Primary dev target = **DGX Spark** (local-heavy: roles default to in-process, remote backends built to prove agnosticism but loaded on demand). Task-card granularity = **per-adapter** (each contract / in-process / serve+http / remote backend / test-suite is its own card). Fixtures = **YouTube clips** (pinned by ID + time range + checksum).

---

## 0. How to Read This Plan

This plan breaks the system into **small, independently testable steps**. Each step has:

- **Goal** — one sentence.
- **Deliverable** — the concrete artifact (a file, a CLI, a test).
- **Done when** — the test/observation that proves it works *in isolation*.
- **Depends on** — prerequisite steps (most are independent on purpose).

Steps are grouped into **Milestones (M0–M10)**. M0 builds the skeleton; each later milestone adds one *vertical slice* that works end-to-end before the next begins. You can stop at any milestone and have something demonstrable.

The plan is **proof-of-concept first**: Python scripts driving small harnesses, reusing existing models and blueprints wherever possible. Performance rewrites (Rust/C++) are deferred — but each step flags whether it's a future rewrite candidate.

---

## 1. Guiding Principles

These five principles decide every structural choice below.

### P1 — Contract-first (the architecture already gave us the contracts)
The architecture doc defines an **input → output contract** for each of the 11 roles. We encode those as schemas *first*, before any model. Everything downstream depends only on the contract, never on a specific model.

### P2 — Hosting-agnostic (local ⇄ remote is a config switch) ⭐
**This is the most important principle.** The DGX Spark may not hold every model at once, so any role must be runnable **in-process, on a local server, or on a remote/cloud endpoint** without changing caller code.

We achieve this with three layers per role:
1. **Role interface** — an abstract `Protocol` (e.g. `SceneDetector.detect(video) -> list[Segment]`).
2. **Adapters** — concrete backends behind that interface:
   - `*_inproc` — loads the model in the current Python process (fast dev, no network).
   - `http_client` — calls a server that implements the role's HTTP contract.
   - protocol clients — `openai_compatible`, `nim`, cloud SDKs (Claude/Gemini) for roles that fit those.
3. **A serving wrapper** — `va serve <role>` wraps *any* `*_inproc` adapter behind a FastAPI endpoint. **The exact same code is "a local server" or "a remote server" — only the URL differs.**

A `registry` reads `config/roles.yaml` and hands the caller a ready client. **Switching a role from local to remote = editing one config block.** No pipeline code changes.

```yaml
# Example: roles.yaml — DGX Spark profile defaults to in-process; any role can flip to remote
scene_detector:   { backend: inproc, model: transnetv2 }              # local on Spark
vlm_captioner:    { backend: inproc, model: qwen2.5-vl-72b-int4 }     # local on Spark…
# vlm_captioner:  { backend: http, protocol: openai, endpoint: https://gpu-box:8000/v1, model: qwen2.5-vl-7b }  # …or remote if Spark is full
reasoning_llm:    { backend: cloud,  provider: anthropic, model: claude-sonnet }  # cloud-preferred per architecture
```

**On DGX Spark specifically:** the model-analysis Deployment Profile A shows all roles fit concurrently (~64GB). So the default profile is in-process. The agnostic layer exists precisely for the case the user flagged — *if* a model (e.g. Qwen-72B INT4 at ~40GB) crowds the budget, flip its one config block to `http`/`cloud` with no other change.

### P3 — Independently testable (every part has its own harness)
Each role ships a **harness**: a CLI driver + a test suite. A role is "done" when its harness passes against a fixture **without any other role present**. Integration is a separate, later step. This makes failures trivial to localize — a bad result is either in one role's harness or in the wiring, never ambiguous.

### P4 — Reuse-first
Prefer, in order: (1) prebuilt inference containers (e.g. NIM), (2) off-the-shelf HF/open-source models and libraries. We write *glue and contracts*, not models. Each step names the artifact to reuse.

### P5 — Storage abstraction (simple local now, production DB later)
Vector / full-text / structured stores sit behind interfaces too. Start with the simplest local backend that satisfies the contract (FAISS-flat, SQLite-FTS, SQLite) and swap to production engines (Milvus/Qdrant, Elasticsearch/Typesense, Postgres) behind the same interface — no caller changes.

---

## 2. Repository Structure

```
video-analytics/
├── pyproject.toml                  # deps, console scripts (`va ...`)
├── config/
│   ├── roles.yaml                  # role → backend + model choice (the agnostic switch)
│   └── profiles/                   # dgx-spark.yaml, consumer-gpu.yaml, cloud-hybrid.yaml
│                                   #   ↳ per-model LOAD params live here: device, dtype, quant, weights path,
│                                   #     residency (keep-resident on Spark vs load/unload on 24GB GPU)
├── src/va/
│   ├── contracts/                  # P1: pydantic schemas (Segment, Embedding, Detection, …)
│   ├── roles/                      # P2: abstract Protocol per role
│   ├── adapters/                   # P2: backends per role (thin: role logic only)
│   │   └── <role>/{*_inproc.py, http_client.py, <protocol>_client.py}
│   ├── runtime/                    # ⭐ model loading/lifecycle — used by *_inproc adapters AND serving
│   │   ├── weights.py              #   resolve weight source (HF hub / local path / NIM) + local cache
│   │   ├── device.py               #   device + dtype + quantization from active profile (Spark vs 24GB)
│   │   ├── loader.py               #   build a model handle from a ModelSpec (load only, no role logic)
│   │   └── manager.py              #   ModelManager: singleton cache, warmup, load/unload, VRAM-budget eviction
│   ├── serving/                    # P2: FastAPI wrapper + protocol schemas (uses runtime to load server-side)
│   ├── sources/                    # video acquisition: base.py + youtube.py (yt-dlp) + local.py → ResolvedVideo
│   ├── media/                      # ffmpeg frame sampling, keyframe + audio extraction (S0.7)
│   ├── storage/                    # P5: vector/ fulltext/ structured/ (incl. catalog = videos table)
│   ├── registry.py                 # P2: config → client factory
│   ├── harness/                    # P3: CLI driver per role
│   └── pipeline/                   # ingest.py, query/{planner,tiers,orchestrator}.py
├── tests/
│   ├── contract/  parity/  golden/  integration/
│   └── fixtures/                   # sample videos + expected outputs
└── scripts/
```

---

## 3. Testing Taxonomy

ML outputs are non-deterministic, so "compare to exact output" rarely works. Every harness uses some mix of these five test types:

| Type | Question it answers | Example |
|---|---|---|
| **Contract** | Does output match the schema? | Embedding is `float32[768]`; detection bbox has 4 coords in `[0,1]`. |
| **Property/sanity** | Are invariants held? | Embeddings unit-norm; segment times monotonic & non-overlapping; confidences in `[0,1]`. |
| **Golden / known-answer** | Right answer on a curated fixture (within tolerance)? | Scene detector finds 5±1 cuts in a 5-cut clip; "dog" query returns the dog frame in top-3; transcript WER < 15%. |
| **Parity** ⭐ | Do `inproc` and `http`/remote backends agree (within tolerance)? | Same frame → cosine sim of local vs remote embedding > 0.999. **Proves P2.** |
| **Smoke/perf** | Does it run within a latency/throughput budget? | Embed 1 hr of frames @1fps in < N min; record cost for cloud calls. |

**Parity tests are the safeguard for the hosting-agnostic goal** — they run in CI whenever a remote endpoint is configured and guarantee a role behaves the same regardless of where it runs.

---

## 4. Per-Adapter Task Cards (the unit of work)

Per the locked decision, the **smallest schedulable unit is one task card per adapter** — not "build the whole role." Every role in the milestone tables below *expands* into the cards in this template when it's pulled into a sprint. Each card is independently testable and most are independently buildable.

**Card schema:** `ID · Goal · Deliverable · Done when (test type) · Depends on`. The card types, in order, for any role `<R>`:

| Card | Deliverable | Done when | Buildable independently? |
|---|---|---|---|
| **`<R>.contract`** | `roles/<R>.py` Protocol + `contracts/` schemas | Schema validates a hand-written example; matches architecture I/O table. *(contract test)* | Yes |
| **`<R>.inproc`** | `adapters/<R>/<model>_inproc.py` + harness CLI (gets its model via `runtime.manager`, never loads directly) | Schema-valid output on a fixture. *(contract + sanity)* | After `.contract`, S0.9/S0.10 |
| **`<R>.serve`** | `va serve <R>` wraps the in-process adapter | Server returns schema-valid output to a manual call. *(smoke)* | After `.inproc` |
| **`<R>.http`** | `adapters/<R>/http_client.py` | Client ↔ server round-trips; **parity vs inproc passes**. *(parity)* ⭐ | After `.serve` |
| **`<R>.remote.<backend>`** | one card *each* for `nim`, `openai_compatible`, `claude`, `gemini`, alt-model, … as the role needs | Each remote backend passes the same parity test vs inproc. *(parity)* | After `.contract` |
| **`<R>.golden`** | role-specific accuracy test in `tests/golden/` | Meets the role's tolerance (P@k / WER / IoU / count). *(golden)* | After `.inproc` |

So a role with one local model + two remote backends = **6–7 cards**. On the **DGX Spark target**, build order is `.contract → .inproc → .golden` first (these unblock integration), then `.serve → .http → .remote.*` to prove agnosticism (deferrable, since Spark runs in-process by default).

The milestone tables in §5 list each role at the **role level** for readability; M1 below is **fully expanded into cards** as the worked example of what every role looks like when scheduled.

---

## 5. Milestones

### M0 — Foundations & Scaffolding
*Goal: a skeleton where a trivial role runs in-process AND over HTTP, proving the agnostic machinery before any real model.*

| Step | Goal | Deliverable | Done when | Depends on |
|---|---|---|---|---|
| **S0.1** | Repo + tooling | `pyproject.toml`, `va` CLI entrypoint, lint/test config | `va --help` runs; empty test suite green. | — |
| **S0.2** | Fixture corpus (YouTube) | `tests/fixtures/sources.yaml` pinning 4–6 **YouTube clips** by `video_id` + `[start,end]` + sha256; a `va fixtures pull` command (yt-dlp) that downloads & clips them locally. Cover: (a) hard scene cuts, (b) speech/multi-speaker, (c) on-screen text, (d) objects+action north-star (e.g. a wildlife "squirrel eating" clip), (e) no-audio. Each clip gets a hand-written `meta.json` of known answers. | `va fixtures pull` reproduces identical local clips (checksum match); `meta.json` documents expected answers. | — |
| **S0.3** | Data contracts | `contracts/`: `Segment`, `FrameEmbedding`, `Caption`, `Detection`, `ObjectTrack`, `ActionEvent`, `TranscriptLine`, `OcrLine`, `AudioEmbedding`, `QueryPlan`, `Evidence`, `Answer` — mirroring the architecture Data Model. | All schemas validate hand-written examples; round-trip JSON. | — |
| **S0.4** | Agnostic core | `roles/base.py` (Protocol pattern), `registry.py`, `config/roles.yaml` loader, `config/profiles/*`. | `registry.get("echo")` returns a client per config. | S0.1 |
| **S0.5** | Serving wrapper | `serving/server.py` (FastAPI) + generic `http_client`; `va serve <role>`. | Generic mechanism documented. | S0.4 |
| **S0.6** | Prove the machinery | An **`echo` role** (returns input) with `inproc` + `http` backends. | **Parity test passes: `echo` via inproc == via http.** This validates P2/P3 end-to-end with zero ML. | S0.4, S0.5 |
| **S0.7** | Frame & audio I/O utils | `media/` helpers: ffmpeg frame sampling at N fps, keyframe extraction, audio track extraction. | Extract exactly K frames @1fps from a 10s clip; extract wav. | S0.1 |
| **S0.8** | Reuse audit | Short `docs/reuse-map.md`: survey which components (decode, Milvus wiring, inference endpoints) we borrow from existing open-source projects vs. build. | Reuse decisions recorded. | S0.2 |
| **S0.9** | Model runtime — loader | `runtime/{weights,device,loader}.py` + `ModelSpec` schema; resolves weight source + device/dtype/quant from active profile and returns a loaded handle. | Load a **tiny stub model** + one real small model (e.g. SigLIP) onto the configured device with the configured dtype; assert placement & dtype. *(contract+sanity)* | S0.4 |
| **S0.10** | Model runtime — manager | `runtime/manager.py`: singleton cache + warmup (minimal now); `load()/unload()` interface with VRAM-budget eviction **stubbed for Spark, implemented later for 24GB profile**. | `get(spec)` twice returns the *same* instance; `unload(spec)` frees it (assert VRAM/RSS delta). *(sanity)* | S0.9 |

**M0 milestone test:** `echo` role passes parity (inproc vs http); frame/audio extraction works on all fixtures; the runtime loads + caches + unloads the stub model. **Note:** S0.9/S0.10 are tested with a stub model so the runtime is verifiable *without* any heavyweight download — keeping the step small and independent.

---

### M1 — Vertical Slice: Visual Search ("Ctrl-F for Video") — *fully expanded into cards*
*Goal: give a **YouTube URL**, ingest it once (idempotently), then search text like "red sports car" and get ranked moments back with the source video + timestamp.*
*Role: **2 (Visual Embedding)**. Stores: **video catalog (structured, minimal)** + vector. This milestone shows the per-adapter card granularity every other role follows.*

> **What this slice adds beyond "embed a local file":** a **source-acquisition layer** (URL → download → metadata) and a **video catalog** (the `videos` table) so we can answer *"have we already ingested this?"*. The catalog is a minimal slice of the structured store that M5 later extends with object/action tables (P5: simple-first, same interface).

**Source & catalog cards (entry point is a YouTube URL):**

| Card | Goal | Deliverable | Done when (test) | Depends on |
|---|---|---|---|---|
| **M1-S1 `source.contract`** | Source-resolver interface + Video schema | `sources/base.py`: `VideoSource.resolve(uri) -> ResolvedVideo(source_type, source_uri, source_key, local_path, metadata)`; `contracts/Video` mirroring the updated `videos` table | Schema validates; matches architecture `videos` table. *(contract)* | S0.3 |
| **M1-S2 `source.youtube`** | YouTube backend (reuse S0.2 yt-dlp) | `sources/youtube.py`: normalize URL → 11-char `video_id` (= `source_key`); download to cache (`local_path`); ffprobe metadata (duration/fps/resolution/has_audio/title) | A URL resolves to a stable `source_key`; second call reuses the cached file (no re-download). *(sanity)* | M1-S1, S0.2 |
| **M1-S3 `source.local`** | Local-file backend | `sources/local.py`: path → sha256 `source_key` + metadata | A local file resolves with a content-hash key; `source_type='local'`. *(sanity)* | M1-S1 |
| **M1-S4 `catalog.store`** | Video catalog (minimal structured store) | `storage/structured/base.py` + `catalog_sqlite.py` implementing the `videos` table; `upsert_video()`, `get_by_source_key()`, `set_status()` | Insert a video; duplicate `source_key` is rejected/returned (idempotent); status transitions persist. *(golden)* | S0.3 |

**Role 2 cards (Visual Embedder):**

| Card | Goal | Deliverable | Done when (test) | Depends on |
|---|---|---|---|---|
| **M1-01 `embed.contract`** | Define the contract | `roles/visual_embedder.py` Protocol; `contracts/FrameEmbedding` (`float32[768]`, video_id, timestamp) | Schema validates example; matches architecture Role 2 I/O. *(contract)* | S0.3 |
| **M1-02 `embed.inproc`** | Local SigLIP | `adapters/visual_embedder/siglip_inproc.py` + harness CLI (model via `runtime.manager`) | Embeds image & text → `float32[768]`, unit-norm. *(contract+sanity)* | M1-01, S0.7, S0.9, S0.10 |
| **M1-03 `embed.golden`** | Semantic correctness | golden test: text "dog" closer to dog frame than kitchen frame | Ranking correct on YouTube fixture pairs. *(golden)* | M1-02 |
| **M1-04 `embed.serve`** | Serve SigLIP | `va serve visual_embedder` | Server returns schema-valid vector to a manual call. *(smoke)* | M1-02 |
| **M1-05 `embed.http`** | HTTP client | `adapters/visual_embedder/http_client.py` | **Parity:** inproc vs http cosine > 0.999. *(parity)* ⭐ | M1-04 |
| **M1-06 `embed.remote.nim`** | NV-CLIP via NIM *(deferrable)* | `adapters/visual_embedder/nim_client.py` | Parity vs inproc within tolerance. *(parity)* | M1-01 |
| **M1-07 `embed.remote.openai`** | OpenAI-compatible embeddings endpoint *(deferrable)* | `adapters/visual_embedder/openai_client.py` | Parity vs inproc within tolerance. *(parity)* | M1-01 |

**Storage + pipeline cards:**

| Card | Goal | Deliverable | Done when (test) | Depends on |
|---|---|---|---|---|
| **M1-08 `vec.contract`** | Vector store interface | `storage/vector/base.py` | Hand-written backend stub satisfies interface. *(contract)* | S0.3 |
| **M1-09 `vec.faiss`** | Local vector backend | `storage/vector/faiss_local.py` (flat index) | Insert N vectors; top-k returns nearest by cosine. *(golden)* | M1-08 |
| **M1-10 `ingest.url`** | Idempotent ingest from a URL | `pipeline/ingest.py`: resolve source → **dedup check via catalog (skip if `done`)** → ensure local file → sample frames → embed → vector store (tagged `video_id`, `timestamp`) → mark video `done` | `va ingest <youtube-url>` creates a `videos` row (`source_type='youtube'`, `local_path` set) and 1 vector/frame tagged with its `video_id`; **re-running the same URL is a no-op** ("already ingested"). *(integration-lite)* | M1-02, M1-09, M1-S2, M1-S4, S0.7 |
| **M1-11 `query.slice`** | Tier-1 query w/ source mapping | `pipeline/query` path: text → embed → vector search → ranked hits **joined to the catalog** → `(video_id, source_uri, timestamp, score)` | `va query "red sports car"` returns ranked moments, each with the originating YouTube URL + timestamp, < 100ms local. *(smoke)* | M1-09, M1-02, M1-S4 |
| **M1-12 `slice.eval`** | Slice retrieval eval | golden retrieval test (P@3) | precision@3 ≥ threshold for "red sports car" on a fixture known to contain one. *(golden)* | M1-10, M1-11 |

**M1 milestone test:** `va ingest <youtube-url>` then `va query "red sports car"` returns the correct moment(s) with the source URL + timestamp; re-ingesting the same URL is detected as already-ingested. Runs with SigLIP **in-process** (default Spark path), then re-run with Role 2 flipped to a remote endpoint via config only (M1-05/06/07) and confirm the same eval passes — proving "Ctrl-F for video", idempotent URL ingest, **and** the hosting-agnostic guarantee.

---

### M2 — Scene Segmentation
*Goal: structure-aware boundaries so later roles operate per segment. Demonstrates two interchangeable backends behind one contract.*
*Role: **1 (Scene Detector)**.*

| Step | Goal | Deliverable | Done when | Depends on |
|---|---|---|---|---|
| **S2.1** | Role 1 module | Build role module; `inproc` = **PySceneDetect** (CPU, zero-GPU, easiest first). | Returns non-overlapping, monotonic `Segment` list. | S0.3, S0.7 |
| **S2.2** | Second backend (interchangeability) | Add **TransNetV2** `inproc` behind the *same* contract. | Both backends pass the same golden test; swapped via config. | S2.1 |
| **S2.3** | Remote backend | `http` server + client for Role 1. | Parity test passes. | S2.1, S0.5 |
| **S2.4** | Accuracy test | Golden: detected cuts within ±1 of fixture's known cut count, boundaries within tolerance. | Meets tolerance on the hard-cut fixture. | S2.1 |
| **S2.5** | Segment-aware keyframes | `media`: pick representative keyframe(s) per segment. | 1–N keyframes per segment, within segment bounds. | S2.1, S0.7 |

**M2 milestone test:** Same fixture segmented identically (within tolerance) by PySceneDetect, TransNetV2, and a remote endpoint — config-switched only.

---

### M3 — Captioning + Caption/Full-Text Search
*Goal: semantic, language-based search; first VLM; first cloud-capable role; implements the stable-scene coalescing optimization.*
*Role: **4 (VLM Captioner)**. Store: full-text.*

| Step | Goal | Deliverable | Done when | Depends on |
|---|---|---|---|---|
| **S3.1** | Role 4 module | Build role module; `inproc` = **Qwen2.5-VL-7B**; input = keyframes per segment. | Produces a non-empty caption per segment (schema-valid). | S2.5, S0.3 |
| **S3.2** | Cloud/remote backends | Add `openai_compatible` (vLLM/local-server), `gemini`, `claude` adapters. | Parity (semantic similarity tolerance, not exact string) across backends. | S3.1 |
| **S3.3** | Full-text store | `storage/fulltext/base.py` + `sqlite_fts.py`. | Index captions; keyword search returns matching segments. | S0.3 |
| **S3.4** | Stable-scene coalescing | Implement embedding-similarity caption coalescing (reuse Role 2 vectors) per the architecture's "Long Static Scenes" section. | On a long-static fixture, caption count drops vs naive per-keyframe; temporal spans preserved. | S3.1, S1.1 |
| **S3.5** | Caption search test | Golden: "kitchen scene" returns the kitchen segment. | precision@3 ≥ threshold. | S3.3, S3.1 |

**M3 milestone test:** Ingest adds captions to full-text store; semantic queries resolve to correct segments; coalescing measurably reduces redundant captions on static footage.

---

### M4 — Audio Pipeline
*Goal: search by what was said.*
*Roles: **8 (Speech-to-Text)**, **9 (Speaker Diarizer, optional)**.*

| Step | Goal | Deliverable | Done when | Depends on |
|---|---|---|---|---|
| **S4.1** | Role 8 module | Build role module; `inproc` = **Whisper large-v3**; output = timestamped `TranscriptLine[]`. | Word/segment timestamps; monotonic. | S0.7, S0.3 |
| **S4.2** | Role 8 remote | `http` + cloud (e.g. Deepgram) adapters. | Parity by WER tolerance against fixture transcript. | S4.1 |
| **S4.3** | Transcript store + search | Index transcripts in full-text store. | "mention the budget" returns the right timestamp. | S4.1, S3.3 |
| **S4.4** | Role 9 module (optional) | Build role module; `inproc` = **pyannote.audio 3.0**; merge speaker labels into transcript. | Multi-speaker fixture → ≥2 distinct speakers, labels merged. | S4.1 |

**M4 milestone test:** Transcript search returns correct moments; diarized fixture shows speaker turns.

---

### M5 — Structured Extraction (Objects, Tracks, Actions)
*Goal: counting & analytics ("how many dogs"). Introduces the structured store.*
*Roles: **5 (Detector)**, **6 (Tracker)**, **7 (Action)**. Store: structured.*

| Step | Goal | Deliverable | Done when | Depends on |
|---|---|---|---|---|
| **S5.1** | Role 5 module | Build role module; `inproc` = **YOLO-World** (open-vocab); output = `Detection[]`. | Finds "person"/"dog" in fixtures; bboxes in-frame. | S0.7, S0.3 |
| **S5.2** | Role 5 precision backend | Add **GroundingDINO** adapter (query-time precision). | Parity-by-label-overlap with YOLO-World. | S5.1 |
| **S5.3** | Structured store (extend catalog) | Extend the M1 catalog store (`storage/structured/`) with `object_tracks, object_detections, action_events` (FK → `videos.id`). | Insert + SQL count queries work. | M1-S4 |
| **S5.4** | Role 6 module | Build role module; `inproc` = **SAM 2** (masks), **ByteTrack** as lightweight alt; seed from Role 5. | Same object keeps one track_id across frames on fixture. | S5.1 |
| **S5.5** | Count query | "how many distinct dogs" via structured store (tracks, not frames). | Returns correct distinct count on fixture. | S5.4, S5.3 |
| **S5.6** | Role 7 module | Build role module; `inproc` = **InternVideo2 (1B)**, VideoMAE v2 alt; per-segment `ActionEvent[]`. | Detects "eating" in the squirrel fixture segment. | S2.5, S5.3 |

**M5 milestone test:** "how many dogs appear" answered from the structured store; "eating" action localized to the right segment.

---

### M6 — Optional Roles
*Goal: round out coverage where the use case needs it.*
*Roles: **3 (Cross-Modal)**, **10 (OCR)**.*

| Step | Goal | Deliverable | Done when | Depends on |
|---|---|---|---|---|
| **S6.1** | Role 10 module | Build role module; `inproc` = **PaddleOCR**; output = `OcrLine[]`. | Reads on-screen text fixture; text in store & searchable. | S2.5, S3.3 |
| **S6.2** | Role 3 module (optional) | Build role module; `inproc` = **ImageBind** (note CC-BY-NC license); audio+visual shared embeddings. | Audio query retrieves matching visual moment. | S1.4, S0.7 |

**M6 milestone test:** On-screen text is searchable; (if enabled) audio↔visual cross-search returns a sane match.

---

### MR — Retrieval Layer (semantic text search + cross-modal fusion + reranking)
*Goal: the "retrieval brain" between extraction and reasoning — turn a pile of per-role
extractions into **one short, relevance-ranked evidence list**. See the "Retrieval Layer" section of the
architecture doc.*

**Why this milestone exists (the value, for a first-time reader):** today caption/transcript/OCR/
action search is literal **word-overlap** (so "the budget" misses "fiscal spending"), each
modality is searched separately and interleaved **round-robin by time**, and there is **no
relevance threshold** (we always return top-k). This milestone fixes all three: find by *meaning*,
**fuse + rank across all modalities** into one list, **rerank** for true relevance, and return
*nothing* when nothing matches. Better retrieval is the cheapest way to make every Role-11 answer
better. New backends (text embedder, reranker) are model-roles behind the registry seam, so they
also **double as the proof of the remote-adapter bet** (point either at a NIM, nothing else changes).

| Step | Goal | Deliverable | Done when | Depends on |
|---|---|---|---|---|
| **SR.1** ✅ | Text embedder role | `roles/text_embedder.py` + adapters: `hash` stub (offline) / real **transformers-direct** (BGE-M3, *not* sentence-transformers — its multimodal build hard-imports torchcodec which dies on this box) / `http` NIM (NV-embedqa) future. A *text-text* space, separate from Role-2 SigLIP. | **DONE 2026-06-15:** deterministic stub + real bge-m3; suite green. Validated: BGE scores paraphrases 0.70-0.73 vs unrelated 0.42 where word-overlap = 0.00. | S0.3, S0.7 |
| **SR.2** ✅ | Index text at ingest | `pipeline/text_index.py` embeds all four text modalities (caption/transcript/OCR/action) → per-video `text_vectors` shard (reuses `VectorStore`, parameterized `ShardedVectorStore`) keyed `(video_id, modality, time, source_role)`; `pipeline/text_search.py` + `va textsearch`; ingest hook + `IngestResult.text_vectors`; `backfill_text_index()`. | **DONE 2026-06-15:** ingest builds the index (best-effort), `search_text` retrieves + modality filter, removal cleans the shard; 3 e2e tests + suite green (114). | SR.1, M3–M6 |
| **SR.3** ✅ | Reranker role | `roles/reranker.py` (`rerank(query, candidates) → aligned scores`) + adapters: **word-overlap** stub (offline) / real **cross-encoder** transformers-direct (BAAI/bge-reranker-v2-m3, matches bge-m3) / `http` NIM future. | **DONE 2026-06-15:** deterministic stub orders correctly; registry/config/extra wired; 3 tests + suite green (117). | S0.3 |
| **SR.4** ✅ | Retriever orchestrator | `pipeline/retrieval.py`: **gather** (visual vec + semantic text via SR.2 index, lexical fallback + structured) → **rerank** language items (one common cross-encoder scale) → **fuse** `RERANK_WEIGHT·norm_rerank + (1−w)·lane-normalized native cosine` → ranked `Evidence`. `ask()` routes through it (supersedes `assemble()`). Raw rerank/cosine kept in `attributes` for SR.5; visual frames carry no language so rank on cosine alone. | **DONE 2026-06-15:** 4 tests + suite green (121). Real-model (bge-m3 + bge-reranker-v2-m3) on SNL/Ferrari: "harmony among nations" → "So, world peace." ranks #1 across ALL modalities; "elegant formal gowns" → fusion FIXED the SR.3 reranker misfire (captions the bi-encoder favored now outrank "Very pretty.", which the cross-encoder alone had put #1). | SR.2, SR.3, S7.3 |
| **SR.5** ✅ | Relevance threshold | `RelevanceGate` (in `retrieval.py`): two absolute floors on the RAW signals — `min_rerank` (cross-encoder logit, language items) + `min_cosine` (native cosine, visual frames), gating per-signal because neither alone suffices. Permissive by default; calibrated floors in run-*/config (`-3.0`/`0.10`), FLAGGED as harness-calibration targets. Notes record drops; never silently empties. | **DONE 2026-06-15:** 3 tests + suite green (124). Real-model: no-match ("scuba diver", "ski slope") → 0 kept ("no match"); matches still return the right hits. Calibrated `min_cosine` 0.05→0.10 after observing irrelevant SigLIP frames cluster at 0.05-0.06. | SR.4 |
| **SR.6** ✅ | VLM verifier | `roles/vlm_verifier.py` (3-way `Verdict`: `accept` keeps-unless-confident-NO for reranking; `present` counts-only-confident-YES for detection) + adapters: `passthrough` no-op stub (offline) / real **Qwen2.5-VL** (reuses the Role-4 bundle, no extra VRAM). `pipeline/verify.py`: `verify_visual_hits` (drop SigLIP attribute/composition false-positives) + `verify_object_presence` (recover novel objects YOLO misses). **SELECTIVE** — applied only to queries hitting SigLIP/YOLO weaknesses. **Productionized into the live path:** `QueryPlan.needs_visual_verification` (the Role-11 planner auto-sets it — claude flags "blue Ferrari"/"feeding a snake" True, counting False), applied in `retrieve()`; plus `va query --verify`. `verify_scene_presence` adds recall-recovery (find a true scene SigLIP under-scored). | **DONE 2026-06-16:** 8 offline tests (132 suite) + golden **83 pass / 1 xfail / 0 fail + 2 ask**. Graduated 4 xfails: "blue Ferrari"→NO (attribute), "feeding a mouse to the snake"→NO (composition), "snake"→VLM presence 1/8 (YOLO found 0), wedding→scene-presence 2/8 (SigLIP 0.022). Caught+fixed a blanket-verification regression by going selective. **A fixture audit then found two more defects:** `cobra-pos-07` "kitchen" was a HALLUCINATED match (no kitchen in the video; human-confirmed) → `no_match` (`cobra-neg-07`); `ferrari-pos-06` "grandstands" was passing on a FALSE POSITIVE (the real LVMS grandstands ~122s are distant background — SigLIP maxes 0.065<0.10, VLM misses them) → narrowed time_range to the true location + `xfail` (the 1 remaining, a genuine distant-background-object gap). Region-aware retrieval (SR.7) investigated but NOT built (it would amplify false positives). | SR.4, M4 |

**MR milestone test:** a paraphrase query ("fiscal spending" → a line that says "budget") and a
cross-modal query each return the correct moment ranked first, beating the current word-overlap +
round-robin baseline; a true no-match returns nothing. Runnable local OR against a NIM by config swap.

---

### M7 — Reasoning & Query Planner
*Goal: the "brain" for complex analytical queries and tier routing.*
*Role: **11 (Reasoning LLM)** — cloud-preferred, local fallback (squarely a P2 case).*

| Step | Goal | Deliverable | Done when | Depends on |
|---|---|---|---|---|
| **S7.1** | Role 11 module | Build role module; backends: `claude` (cloud, primary), `gemini`, **Qwen2.5-VL-72B** `inproc`/`http` (local fallback). | Given fixed evidence JSON, returns a cited `Answer`. | S0.3 |
| **S7.2** | Query planner | `pipeline/query/planner.py`: Role 11 classifies a query → `QueryPlan` (which tiers to run). | Planner flags correct tiers on a labeled query set. | S7.1 |
| **S7.3** | Evidence assembly | Gather captions/transcripts/tracks/keyframes for a query into an `Evidence` bundle. | Bundle for the squirrel query contains the right segments. | M3–M5 |
| **S7.4** | Reasoning golden test | "how many nuts did the squirrel eat" → correct count with cited timestamps. | Answer matches fixture ground truth; citations valid. | S7.1, S7.3 |

**M7 milestone test:** Planner routes correctly; reasoner answers the north-star question with citations — runnable against cloud OR local Qwen by config swap.

---

### M8 — Query Orchestration (Progressive Escalation)
*Goal: tie tiers together with the instant-then-refine UX from the architecture.*

| Step | Goal | Deliverable | Done when | Depends on |
|---|---|---|---|---|
| **S8.1** | Tier router | `orchestrator.py`: run Tier 1 immediately; planner in parallel; escalate per plan. | Simple query stops at Tier 1; complex query escalates. | M1, M7 |
| **S8.2** | Parallel + streaming | Async execution; stream partial results that refine. | Tier-1 result emitted before Tier-5 completes. | S8.1 |
| **S8.3** | Query-flow golden set | Tests for each row of the architecture's "Query Flow by Complexity" table. | Each query type uses the expected tiers within latency budget. | S8.1 |
| **S8.4** | **Deep-scan escalation (Tier 5b)** | Planner flag `needs_deep_scan` (+ rule heuristics "how many times / each time"); executor: scope → ~1fps VLM sweep with micro-prompt → timestamped observations → **count in code** → observations cached to DB. Deterministic generation for Qwen (`do_sample=False`). See architecture doc "Deep-Scan Escalation". | The dress-change question on `eiLeBJUf1iE` returns a stable count matching ground truth (double digits, user-verified) with per-change timestamps. | M7 (ask pipeline — done) |
| **S8.5** | Role-1 backend swap for montage content | Switch real-models config to `pyscenedetect` (adapter exists), re-ingest the dresses clip, compare segment count vs histogram (expect the 20–67s montage to split into ~10+ shots) and re-caption. | Per-shot captions enumerate distinct outfits; ingest-side count roughly agrees with S8.4's query-side count. | S2.2 |

**M8 milestone test:** The full progressive-refinement trace from the architecture doc reproduces on the squirrel fixture (instant matches → narrowed → final cited answer). **Plus:** the dress-change counting question (the observed real failure) is answered correctly and stably via deep-scan, with the ingest-side (S8.5) and query-side (S8.4) counts converging.

---

### M9 — Full Integration, Eval & Observability
*Goal: one command runs the whole pipeline; we can measure quality, cost, latency, and storage.*

| Step | Goal | Deliverable | Done when | Depends on |
|---|---|---|---|---|
| **S9.1** | Full ingest | `va ingest` runs all configured roles per the architecture's Ingestion Pipeline, writing all stores. | One fixture fully indexed across vector/full-text/structured. | M1–M6 |
| **S9.2** | E2E integration tests | `tests/integration/`: ingest → assorted queries → expected answers. | All north-star queries pass end-to-end. | S9.1, M8 |
| **S9.3** | Eval harness | Labeled query set + metrics (retrieval P@k, action/count accuracy, transcript WER, reasoning correctness). | Single `va eval` report. | S9.1 |
| **S9.4** | Cost/latency telemetry | Per-role timing + cloud $ tracking surfaced per run. | Report shows per-role latency and per-query cost. | S9.1 |
| **S9.5** | Metadata-overhead measurement | Measure real per-hour metadata sizes + **embedding retention ratios** to validate the architecture's overhead/coalescing tables on actual footage. | Report compares measured vs predicted overhead %. | S9.1 |

**M9 milestone test:** `va ingest` + `va eval` produce a quality/cost/latency/storage report on a multi-fixture set.

---

### M10 — Production Backends & Performance Prep
*Goal: swap toy stores for production engines; identify what to rewrite in Rust/C++.*

| Step | Goal | Deliverable | Done when | Depends on |
|---|---|---|---|---|
| **S10.1** | Production vector store | `storage/vector/milvus.py` (or qdrant) behind existing interface. | Same M1 eval passes on Milvus; embedding dedup applied. | S1.4 |
| **S10.2** | Production full-text/structured | `elasticsearch.py`/`typesense.py`, `postgres.py`. | Same golden tests pass on production engines. | S3.3, S5.3 |
| **S10.3** | Deployment profiles | Validated `config/profiles/` for DGX Spark / consumer-GPU / cloud-hybrid (per model-analysis Deployment Profiles). | Each profile ingests a fixture within its resource budget. | S9.1 |
| **S10.4** | Perf-candidate audit | `docs/rewrite-candidates.md`: hotspots (frame decode, sampling, embedding throughput, mask encoding) ranked for Rust/C++. | Profiled hotspots listed with expected payoff. | S9.4 |

**M10 milestone test:** Full pipeline runs on production stores under at least one hardware profile; rewrite roadmap documented.

---

## 6. Reuse Map (P4)

| Role / Concern | Reuse | Build |
|---|---|---|
| Decode, frame sampling | FFmpeg | Sampling policy, keyframe selection |
| Vector DB wiring | Milvus | Our `storage/vector` interface |
| Role 2 Embedding | SigLIP / NV-CLIP (NIM) | Adapter + parity tests |
| Role 4 / 11 VLM | Qwen2.5-VL, NVILA (NIM), Claude/Gemini SDKs | Adapter + serving wrapper |
| Roles 1,5,6,7,8,9,10 | TransNetV2, YOLO-World, SAM 2, InternVideo2, Whisper, pyannote, PaddleOCR | Adapters + harnesses |
| Serving | FastAPI; NIM where available | Generic `va serve` wrapper |
| Orchestration | LangChain patterns | Our tier router/planner |

---

## 7. Dependency Graph (high level)

```
M0 (foundations) ──┬─> M1 (visual search) ─────────────┐
                   ├─> M2 (scenes) ─> M3 (captions) ───┤
                   │                  └─> M6 OCR        ├─> M7 (reasoning) ─> M8 (orchestration) ─> M9 ─> M10
                   ├─> M4 (audio) ────────────────────┤
                   └─> M5 (objects/actions) ──────────┘
```
M1–M6 are **largely parallelizable** once M0 exists (they touch different roles/stores). M7+ integrate them.

---

## 8. Decisions & Open Questions

**Resolved (iteration 1):**
- ✅ **First target hardware** → **DGX Spark, local-heavy.** Roles default to in-process; remote adapters built to prove agnosticism but deferrable.
- ✅ **Step granularity** → **per-adapter task cards** (see §4; M1 fully expanded as the worked example).
- ✅ **Fixture sourcing** → **YouTube clips**, pinned by `video_id` + time range + checksum, pulled via `va fixtures pull` (see S0.2).

**Resolved (2026-06-10):**
- ✅ **Deep-scan escalation (S8.4) + Role-1 backend swap (S8.5) prioritized** — motivated by
  an observed real failure: dress-change counting on `eiLeBJUf1iE` was wrong and unstable
  (sparse keyframes + histogram detector merging the montage + no state-change extractor in
  any role). Key decision: counting moves OUT of the LLM into code over exhaustive VLM
  observations. Architecture doc section "Deep-Scan Escalation (Tier 5b)" is the spec.
- ✅ **S8.5 DONE (2026-06-10):** pyscenedetect swap → 6 → **71 segments** (the merged 20-67s
  montage split into ~25 shots); per-shot Qwen captions enumerate the outfits; the dress
  question now answers **"12–13 changes"** with 13 hyperlinked timestamps — matching the
  user's double-digit ground truth — including honest uncertainty notes. Along the way fixed
  an evidence-PRESENTATION bug: `render_evidence` truncated at 20 items in list order, so
  visual hits crowded out all captions; now round-robin across modalities, chronological,
  max 60. **S8.4 still pending** (single-run result; LLM still does the arithmetic; gaps
  24-35s/91-103s would be covered by a dense sweep + code count).
- 📊 **Stability measured (3× repeat of the dress question, claude-code):** counts 13 /
  12-13 / 15; enumerated items 14/15/17. Class-stable (always double-digit, same timeline,
  same hedges) but count wobbles ±2-3 — variance is entirely in the LLM's grouping of
  ambiguous re-wears, not in retrieval. Confirms S8.4's code-side counting as the closer;
  target output shape: a bounded count ("13, up to 15 counting possible re-wears").
- ✅ **S8.4 DONE (2026-06-11)** after FOUR diagnosed iterations (each stable, first three
  wrong): free-text sweep counted descriptions (94-99); constrained vocab counted camera
  cuts (70-71 ≈ the 71 shots); color-only canonical labels undercounted (11 distinct —
  different pink/white dresses merged, sub-second shots missed by the 1.3s stride);
  final design — **shot-aligned sampling (one frame per Role-1 segment midpoint) →
  style-bearing labels (color + distinguishing detail) → LLM-normalize to canonical
  states (cached, auditable; cut-aways→OTHER; no merging across style details) →
  code-count** — yields **18 distinct dresses = 17 changes, byte-identical across runs,
  EXACTLY matching the user's ground truth of 17** (26 raw transitions = montage
  intercutting, correctly explained). Key lessons in the architecture doc: distinct
  states answer "how many changes" on montage footage; transitions measure the editor;
  sampling must be shot-aligned, not stride-based. Also: canonical cache keys
  (wording-drift-proof, version-stamped), de-hardcoded rule-planner scan_target,
  CLAUDE.md "Heuristics & validation" conventions.

- ✅ **Deep-scan CROSS-VALIDATED on a second content class (2026-06-11):** fixed-camera
  birdfeeder clip (4:13-14:05 of yt 2Oy4Fy8vIgw, single 592s take), query "count number
  of birds visiting birdfeeder", user ground truth 4-5. Result: **5 visits — exact match**
  (cardinal @0:18 = the user's 4-vs-5 ambiguity). Two NEW long-take failure modes found
  and fixed en route: (1) per-sample label flicker on live subjects (23 phantom visits)
  → temporal debounce in code (single-sample runs sandwiched by same state = flicker);
  (2) sustained angle-dependent relabeling ("brown speckled"/"brown striped" = one
  sparrow's side vs back, frame-verified) → normalizer now receives the RUN TIMELINE and
  a same-subject-alternation exception (mapping cache key versioned :norm2). Validated
  regimes: montage (dresses, 17 ✓) + long take (birds, 5 ✓). Also: yt-dlp section
  downloads need full ffmpeg+ffprobe ON PATH (`static-ffmpeg` pip works on arm64;
  ffmpeg_location is ignored by FFmpegFD.available()).

- ✅ **ANTHROPIC_API_KEY decision (2026-06-11): no key purchase; BYOK for productization.**
  Dev continues on `claude-code` (existing subscription) + `qwen` (local). When
  productizing, customers plumb THEIR provider account: two thin Role-11 adapters —
  `openai-compatible` (covers OpenAI, Gemini-compat, vLLM/Ollama self-hosters,
  OpenRouter/LiteLLM aggregators) and `anthropic` (the existing placeholder graduates) —
  config carries `protocol/base_url/model/api_key_env` (key from env/secrets, never in
  files). Guardrails: deep-scan sweeps ALWAYS on local models (only plan/normalize/reason
  ~3 calls per ask touch the customer key); per-ask token caps + usage surfacing. Reuses
  shared prompts/parsing/fallback as-is. ~1 day of work, deferred until a product customer needs it.

- 📐 **Intent-trigger trajectory (ideated 2026-06-11):** the rule-planner floor evolves in
  three steps — (1) **lexicon.yaml** in the config dir (intent trigger verbs +
  scan-target noise; merges over closed code defaults; per-deployment/domain tuning);
  (2) **exemplar-based semantic matching**: lexicon carries ~5-10 *example questions*
  per intent, matched by text-embedding cosine (deterministic, ms-fast, generalizes to
  unseen phrasings — "words don't generalize, meanings do"); spike below tests whether
  SigLIP's text tower suffices or a small sentence encoder is needed; (3) **self-
  escalation backstop**: sparse-pass answers reporting insufficient evidence / instability
  trigger a deep-scan re-run, so no floor miss is final. Rejected: MCP server for verb
  vocabulary (wrong layer — lexicon-behind-a-socket or LLM-in-disguise); the MCP instinct
  is REDIRECTED to productization: **customer MCP servers as domain/evidence extension
  points** for Role 11 (their cameras/catalogs/incident DBs as extra evidence tiers) —
  parked next to BYOK.
  **SPIKE RESULT (2026-06-11, `scripts/spike_intent_embeddings.py`):** SigLIP text tower
  — 8/11, margins ≈ noise (+0.001..0.05), deep_scan binary gap **−0.138** → unusable
  (image-text tuned, not sentence semantics). MiniLM (all-MiniLM-L6-v2) — 9/11, real
  margins (+0.10..0.36), gap **−0.096** → still overlapping: deep_scan vs object_count is
  intrinsically fuzzy (both are counting questions). CONCLUSION: exemplar matching as
  sole floor does NOT pass with off-the-shelf encoders; viable redesign = collapse to a
  3-way {counting/transcript/visual} superclass (counting escalates BOTH object query and
  deep scan — confusion becomes harmless) + calibrated thresholds. Until then the stack
  stays: regex floor (closed) + LLM planner + self-escalation (step 3) — which is now the
  higher-value next investment, since it catches misses regardless of any classifier.
  ✅ **Step 3 BUILT (2026-06-11):** `ask()` escalates once when no deep scan ran and the
  sparse answer self-reports insufficiency (marker regex incl. "unknown") or returns
  uncited+empty; re-reasons over dense evidence; guarded against double scans; noted in
  `Evidence.notes` for transparency. Tests cover escalate/sufficient/already-scanned.

**Deferred (intentionally, not blocking):**
- ⏸️ **Calibrated golden-query pytest harness** — fixtures exist in `tests/golden_queries/` (generated + verified). The runnable harness that ingests each video with SigLIP and asserts match/no_match against a *calibrated* score threshold is deferred until more roles exist (so it covers speech/OCR/etc. in one build). Manual `va query` against the `.md` fixtures works today.

- ✅ **Workspace layout v2 + `va remove`/`reingest`/`migrate-layout` DONE (2026-06-11).**
  Shared `catalog.db`; per-video `videos/<key16>-<slug>/` dirs (media + vector shard +
  keyframes); `ShardedVectorStore` = one logical index over shards; remove = dir + rows;
  reingest preserves managed local media through the cycle. All five experiment workdirs
  migrated (media/vectors/keyframes moved, catalog paths retargeted, monolith kept as
  *.v1.bak); post-migration SigLIP queries byte-identical to pre-migration scores
  (ferrari "red sports car" 0.115@1:07). Web contract preserved: `/api/media` reads
  `local_path` from the catalog, which migration updates. 92 tests green.

- ✅ **S6.1 (Role 10, OCR) DONE (2026-06-12).** `roles/ocr.py` (`OcrReader.read(media_path)
  -> OcrLine[]`, media-path based like STT so each backend owns frame handling) +
  `sidecar` stub (`<video>.ocr.json`) + real **RapidOCR** adapter (`ocr` extra; PP-OCR
  det+rec models on onnxruntime, CPU — no VRAM contention with the VLM). **Plan
  deviation from "inproc = PaddleOCR", flagged:** paddlepaddle 3.2's inference engine
  segfaults at predictor init on this aarch64 box (PIR param loading; reproduced with
  PP-OCRv5/v6, mkldnn on/off) — RapidOCR keeps the same PP-OCR model lineage on a
  runtime that works here. The EN rec model is required: the CH default garbles
  spacing ("boughit acobra" vs "bought a cobra").
  Samples at 1 fps, collapses consecutive sightings of the same
  normalized text into one row per appearance. `OcrStore` writes/searches `ocr_results`
  (word-overlap, grouped per distinct string with first..last sighting); `va ocr`;
  `needs_ocr_search` tier flag wired through QueryPlan → planner prompts → rule-planner
  intent regex → `assemble()` → `on_screen_text` evidence modality (source_role=10).
  Golden fixtures promoted: ferrari-ocr-01/02 (Coors Light, Dodge), cobra-ocr-01/02
  (burned-in captions), dresses-ocr-01 (title card).

- ✅ **S5.6 (Role 7, Action Recognizer) DONE (2026-06-12).** `roles/action_recognizer.py`
  (`recognize(media_path, spans, actions) -> List[List[ActionEvent]]`, open-vocab like
  Role 5; runs per Role-1 segment — the shot is an action's natural unit) + `motion` stub
  (pixel-diff motion-vs-static, grounds what synthetic clips actually have) + real **xclip**
  backend (`action` extra). **Plan deviation from "inproc = InternVideo2", flagged:**
  InternVideo2 is a custom OpenGVLab stack, not transformers-native; after the
  paddle-on-aarch64 lesson we prefer runtimes already proven here. VideoMAE (the listed alt)
  is closed-vocab Kinetics; **X-CLIP** is zero-shot/open-vocab via transformers, so the
  ingest vocabulary lives in config (`DEFAULT_INGEST_ACTIONS`, roles.yaml `actions:` override).
  `ActionStore` over the pre-created `action_events` table (word-overlap search); `va actions`;
  `needs_action_query` now EXECUTES in `assemble()` (was a "Role 7 not implemented" note) →
  `action` evidence modality (source_role=7). **Validated on real footage:** Ferrari → all 11
  pyscenedetect segments "driving a car" 0.94-0.99; birdfeeder → "feeding animals" 0.85; full
  `va ask "is anyone driving..."` flagged the action tier and cited the events with timestamps.
  Golden fixtures promoted: ferrari-act-01 (driving), bird-act-01 (feeding animals).
  **Known limit (documented):** X-CLIP scores a FIXED vocabulary and always picks the
  least-bad label (softmax over requested phrases) — great for "is one of these happening",
  but a specific unlisted action ("counting dresses" → "dancing") needs query-time recognition
  (the action analogue of GroundingDINO); left in dresses future_queries as needs_query_time_vocab.
  **Abstention foil added (2026-06-12):** `NO_ACTION = "no particular action"` always rides in
  the candidate set so the softmax can decline to label rather than force a wrong one; winning
  foil = no event. Re-backfill measured: confident labels intact (Ferrari 11/11 driving), the
  dress montage trimmed 29 → 23 borderline labels, cobra "feeding animals" eased 0.91 → 0.89
  (still wins — X-CLIP genuinely prefers it over abstention).
  Also surfaced a data-staleness issue: 3 of 5 `.va-shots` videos (ferrari/cobra/F&F) were
  ingested pre-Role-1 with **0 segments** — backfilled pyscenedetect segments before running
  Role 7 (no full reingest).

- ✅ **S4.4 (Role 9, Speaker Diarizer) DONE (2026-06-12).** `roles/diarizer.py`
  (`diarize(media_path) -> SpeakerTurn[]`, media-path based like STT) + `sidecar` stub
  (`<video>.diarization.json`) + real **pyannote.audio** backend (`diarize` extra, pinned
  `>=4` — 3.x crashes importing `torchaudio.AudioMetaData`, removed in this box's torchaudio 2.11). No schema change — Role 9 *annotates* Role 8:
  `pipeline/diarize.py::assign_speakers()` joins turns onto transcript lines by max temporal
  overlap and fills the existing `transcripts.speaker` column. `va transcript --speaker
  SPEAKER_01` filter (TranscriptStore.search gained a speaker arg); evidence already carried
  `speaker` in `from_transcript_hit` attributes, so Role 11 gets it free. `IngestResult.speakers`
  = distinct speakers assigned. Best-effort in ingest (between STT and the transcript write).
  **Real path unvalidated here:** pyannote's model is gated and no HF_TOKEN is available in this
  env — the adapter is written to the documented API and degrades gracefully (missing
  token/model → speaker stays NULL, ingest unaffected); the sidecar stub covers the offline
  tests (106 green, incl. overlap-assignment + --speaker filter on real-scale lines).

**Still open (for next iteration):**
1. **Remote protocol surface** — standardize on OpenAI-compatible for VLM/LLM/embeddings and a single native REST for the CV roles? Or adopt NIM contracts directly where they exist?
2. **License constraints** — ImageBind (CC-BY-NC) and YOLO-World (GPL) / YOLOv10 (AGPL) may be blocked if this becomes commercial; pick permissive alternates now or defer to M6/M10?
3. **Parity tolerances** — concrete thresholds per role (cosine, WER, IoU, label-overlap) need pinning before golden/parity tests are meaningful.
4. **YouTube fixture reproducibility** — videos can be deleted/region-locked; do we mirror the clipped fixtures to private storage (and is that acceptable for our use), or accept occasional re-sourcing?
5. **M0 echo-role** — keep the no-ML role as a dedicated step to prove the agnostic machinery, or fold into M1-02?
6. **Should M2–M10 also be pre-expanded into cards now**, or expand each milestone just-in-time as it's scheduled (current approach, keeps the plan readable)?

---

*Next: resolve the open questions above, then expand the next scheduled milestone (M2/M4/M5 — all parallelizable after M0) into per-adapter cards like M1.*
