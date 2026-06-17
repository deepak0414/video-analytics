# Solution Code Hike — Video Analytics M1 ("Ctrl-F for Video")

*A guided, detailed walkthrough of everything built so far: the code, the directory
structure, synthetic clips, YouTube downloads, the SigLIP model download + GPU load,
and the actual runs (positive + negative tests).*

*Written: 2026-06-06 | Companion to: [plan.md](plan.md), [README.md](README.md),
[video-analytics-solution-architecture.md](video-analytics-solution-architecture.md)*

---

## Table of Contents

1. [What we built (one paragraph)](#1-what-we-built-one-paragraph)
2. [The machine & environment](#2-the-machine--environment)
3. [The big picture: two data flows](#3-the-big-picture-two-data-flows)
4. [Directory structure (annotated)](#4-directory-structure-annotated)
5. [Layer-by-layer code walkthrough](#5-layer-by-layer-code-walkthrough)
6. [The hosting-agnostic design (the spine)](#6-the-hosting-agnostic-design-the-spine)
7. [How models get downloaded & loaded on the GPU](#7-how-models-get-downloaded--loaded-on-the-gpu)
8. [Synthetic video clips (for tests)](#8-synthetic-video-clips-for-tests)
9. [YouTube acquisition (real artifacts)](#9-youtube-acquisition-real-artifacts)
10. [The actual runs & results](#10-the-actual-runs--results)
11. [Artifacts on disk](#11-artifacts-on-disk)
12. [Testing approach](#12-testing-approach)
13. [Real-world issues hit & fixed](#13-real-world-issues-hit--fixed)
14. [What is NOT done yet](#14-what-is-not-done-yet)
15. [How to reproduce](#15-how-to-reproduce)
16. [Addendum (2026-06-10): a model-verification finding worth remembering](#16-addendum-2026-06-10-a-model-verification-finding-worth-remembering)

---

## 1. What we built (one paragraph)

We implemented **Milestone M1** from the plan: a working vertical slice that takes a
**video** (a YouTube URL *or* a local file), ingests it once (idempotently), and lets
you **search it with natural-language text** — returning ranked moments (timestamps)
that map back to the source. It uses **one AI model role: Role 2, the Visual Embedding
Model**. The whole thing is ~1,300 lines of Python across small, independently testable
modules, with **34 passing tests**, and it has been validated on **real YouTube footage**
(a red Ferrari video — positive test, and a snake video — negative test) using the real
**SigLIP SO400M** model loaded on the DGX Spark's GPU.

---

## 2. The machine & environment

| Property | Value |
|---|---|
| Machine | **DGX Spark** — NVIDIA **GB10** (Grace-Blackwell), 128GB unified memory |
| Architecture | **aarch64** (ARM64) — matters for wheel selection |
| OS / Python | Linux, **Python 3.12.3** |
| Isolation | A project-local **`.venv`** (4.9 GB once torch is in) |
| GPU available to torch | **Yes** — `torch.cuda.is_available() == True`, device `NVIDIA GB10` |

**Key installed versions (verified):**

| Package | Version | Note |
|---|---|---|
| torch | **2.12.0+cu130** | CUDA build, runs on the GB10 |
| transformers | **5.10.2** | hosts SigLIP; v5 API differs from v4 (see §13) |
| numpy | 2.4.6 | vector math + flat index |
| pydantic | 2.13.4 | all data contracts |
| yt-dlp | 2026.03.17 | YouTube acquisition |
| imageio-ffmpeg | bundles **ffmpeg 7.0.2** (`ffmpeg-linux-aarch64`) | no system ffmpeg needed |

**Two dependency tiers** (so tests never need the heavy stack):
- **Core** (`pip install -e .`): numpy, pydantic, pyyaml, pillow, imageio, imageio-ffmpeg, yt-dlp. Enough for the whole pipeline using the *stub* embedder.
- **`[siglip]` extra** (`pip install -e '.[siglip]'`): torch, transformers, sentencepiece, protobuf. Only needed to run the *real* model.

---

## 3. The big picture: two data flows

Everything is two pipelines sharing the same stores.

### Ingest (write path)
```
URL / path
   │
   ▼
resolve_source(uri) ──► VideoSource (youtube | local)
   │                         │
   │   .resolve(uri)         │  cheap: gives a stable source_key (dedup key)
   ▼                         │
Catalog.get_or_create ◄──────┘   "already ingested?" check (UNIQUE source_key)
   │  (skip if status == done)
   ▼
source.fetch() ──► download (yt-dlp, ≤480p) / locate local file ──► probe metadata
   │
   ▼
media.sample_frames(file, fps=1) ──► (timestamp, PIL.Image) …  [batches of 32]
   │
   ▼
VisualEmbedder.embed_image(batch) ──► [N, D] float32 unit vectors
   │     (Role 2: hash stub OR SigLIP on GPU)
   ▼
VectorStore.add(vectors, payloads={video_id, timestamp, source_uri})
   │
   ▼
Catalog.set_status(done)        ← idempotent: re-running the URL is now a no-op
```

### Query (read path)
```
"red sports car"
   │
   ▼
VisualEmbedder.embed_text(["red sports car"]) ──► [1, D] unit vector
   │     (SAME model/space as ingest — that's why text can match images)
   ▼
VectorStore.search(qvec, k) ──► top-k by cosine similarity
   │
   ▼
join each hit's payload.video_id ──► Catalog ──► source_uri
   │
   ▼
ranked SearchHit(video_id, source_uri, timestamp, score)
```

The crucial idea: **images and text are embedded into the same vector space**, so
"search" is just cosine nearest-neighbor between a text vector and pre-computed frame
vectors. Ingest is the expensive part (run once); query is near-instant math.

---

## 4. Directory structure (annotated)

`src/va/` is ~1,289 LOC. Each file is small on purpose (independently testable):

```
src/va/
├── __init__.py                      (15)  package doc + __version__
├── cli.py                           (75)  `va ingest` / `va query` / `va fixtures`
├── configuration.py                 (65)  load roles.yaml + active profile -> RoleConfig
├── registry.py                      (31)  config -> a role client (the P2 factory)
│
├── contracts/                             ── the data contracts (pydantic) ──
│   ├── video.py                     (83)  SourceType, IngestStatus, ResolvedVideo, Video
│   └── embedding.py                 (33)  FrameEmbedding, SearchHit
│
├── runtime/                               ── model loading / lifecycle ──
│   ├── device.py                    (31)  resolve 'cuda'→cpu fallback; dtype
│   └── manager.py                   (63)  ModelManager: singleton cache + unload (GPU mem)
│
├── sources/                               ── video acquisition ──
│   ├── base.py                      (34)  VideoSource protocol + resolve_source() dispatcher
│   ├── youtube.py                  (101)  video_id parsing, yt-dlp download, ffmpeg symlink
│   ├── local.py                     (39)  sha256 source_key for local files
│   └── fixtures.py                  (29)  `va fixtures pull` from sources.yaml
│
├── media/                                 ── decode helpers ──
│   ├── frames.py                    (47)  sample_frames(fps) + probe() via imageio-ffmpeg
│   └── synth.py                     (37)  write_color_video() — synthetic clips for tests
│
├── roles/                                 ── abstract role interfaces ──
│   └── visual_embedder.py           (25)  Role 2 Protocol: embed_image / embed_text
│
├── adapters/visual_embedder/              ── concrete Role-2 backends ──
│   ├── hash_inproc.py               (73)  deterministic color-aware STUB (no GPU/net)
│   └── siglip_inproc.py             (74)  REAL SigLIP SO400M via runtime manager
│
├── storage/
│   ├── vector/
│   │   ├── base.py                  (33)  VectorStore protocol + VectorHit
│   │   └── numpy_flat.py            (77)  brute-force cosine index, persisted to npz+json
│   └── structured/
│       └── catalog_sqlite.py       (174)  the `videos` table (dedup, status, metadata)
│
└── pipeline/
    ├── paths.py                     (21)  Workspace: where db/vectors/cache live in a workdir
    ├── ingest.py                    (79)  the write path (above)
    └── query.py                     (50)  the read path (above)
```

Config + tests:
```
config/roles.yaml          which backend+model per role (the agnostic switch)
config/profiles/dgx-spark.yaml   per-model load params (device, dtype, weights)
run-siglip/config/         a SEPARATE config dir selecting model: siglip (for real runs)
tests/                     9 test files, 34 tests, no network/GPU required
```

---

## 5. Layer-by-layer code walkthrough

Walking the layers bottom-up — each is a clean seam you can test alone.

### 5.1 Contracts (`contracts/`)
The boundary types. `contracts/video.py` mirrors the `videos` SQL table from the
architecture doc, with two enums (`SourceType` = youtube|local, `IngestStatus` =
pending|fetching|processing|done|failed) and two models:
- **`ResolvedVideo`** — what a source produces: `source_type`, `source_uri`,
  `source_key` (the dedup key), `local_path`, and probed `metadata`.
- **`Video`** — the catalog row; `Video.from_resolved()` converts one to the other.

`contracts/embedding.py` has **`FrameEmbedding`** (a `np.float32` 1-D vector tagged with
`video_id` + `timestamp`; a validator enforces 1-D and casts dtype) and **`SearchHit`**
(the query result: video_id, source_uri, timestamp, score).

### 5.2 Configuration (`configuration.py`)
Reads `config/roles.yaml` (picks `active_profile` and per-role `backend`+`model`) and
the chosen `config/profiles/<name>.yaml` (per-model load params). `Config.role(name)`
**folds** the profile's device/dtype/weights for that model into one `RoleConfig.load`
dict — so an adapter receives a single object describing *what* to run and *how* to load
it. `VA_CONFIG_DIR` env var overrides the config directory (this is how the real run uses
`run-siglip/config` without touching the default).

### 5.3 Runtime (`runtime/`)
- **`device.py`** — `resolve_device('cuda')` returns `'cuda'` only if torch+CUDA exist,
  else `'cpu'`. So the same config runs on the Spark and on a laptop.
- **`manager.py`** — `ModelManager` with a process-wide singleton `MANAGER`. `get(key,
  build)` builds a model once and caches it (double-checked locking); `unload(key)` drops
  it and calls `torch.cuda.empty_cache()`. **In-process adapters never load weights
  directly — they go through this**, so SigLIP loads once and is reused across queries.

### 5.4 Sources (`sources/`)
- **`base.py`** — `VideoSource` protocol with `resolve(uri)` (cheap, gives `source_key`)
  and `fetch(resolved, cache_dir)` (downloads + probes). `resolve_source(uri)` dispatches:
  YouTube URL → `YoutubeSource`, existing file → `LocalSource`, else error.
- **`youtube.py`** — `extract_video_id()` handles every URL form
  (`watch?v=`, `youtu.be/`, `/shorts/`, `/embed/`, extra params) → the 11-char id is the
  `source_key`. `fetch()` uses yt-dlp to pull ≤480p into the cache, after symlinking the
  bundled ffmpeg so yt-dlp can merge streams (see §13).
- **`local.py`** — `source_key = "sha256:" + hash(file)` so a moved/renamed file is still
  recognized as already-ingested.

### 5.5 Media (`media/`)
- **`frames.py`** — `sample_frames(path, fps)` decodes with imageio (bundled ffmpeg) and
  **strides** over frames to hit the target fps, yielding `(timestamp, PIL.Image)`.
  `probe()` returns duration/fps/resolution.
- **`synth.py`** — `write_color_video()` paints solid-color frames and encodes an mp4.
  This is the test-fixture generator (see §8).

### 5.6 Role 2 (`roles/` + `adapters/visual_embedder/`)
- **`roles/visual_embedder.py`** — the contract: `embed_image(images) -> [N,D]` and
  `embed_text(texts) -> [N,D]`, both L2-normalized, same space. A `runtime_checkable`
  Protocol, so `isinstance(x, VisualEmbedder)` works in tests.
- **`hash_inproc.py`** — the **stub**. It's *color-aware*: an image embeds via its
  dominant named color; text embeds via a color word found in the query. So a red frame
  and "red sports car" land on the same vector. Deterministic, no GPU/network — this is
  what lets the full pipeline be golden-tested in CI.
- **`siglip_inproc.py`** — the **real** backend. Loads SigLIP SO400M through the
  `ModelManager`, runs `get_image_features`/`get_text_features`, unwraps the output
  (transformers v5 returns an object), L2-normalizes. dim = **1152**.

### 5.7 Storage (`storage/`)
- **`vector/numpy_flat.py`** — a brute-force cosine index. Vectors are L2-normalized on
  insert, so search is one matrix-vector dot + top-k via `argpartition`. Persisted as
  `vectors.npz` (matrix) + `vectors.json` (payloads). Chosen over FAISS to avoid a fragile
  native dep; same `VectorStore` interface so Milvus can swap in later.
- **`structured/catalog_sqlite.py`** — the `videos` table. `get_or_create(resolved)`
  returns `(video, created)`; a duplicate `source_key` is **never** inserted twice — that's
  the idempotency point. `set_status()` drives the lifecycle; `update_metadata()` fills in
  probed fields after fetch.

### 5.8 Pipeline (`pipeline/`) + CLI (`cli.py`)
- **`ingest.py`** — orchestrates the write path: resolve → dedup → fetch → sample → embed
  (in batches of 32) → store → mark done. Wraps processing in try/except so a failure
  records `status=failed` + error.
- **`query.py`** — the read path: embed text → vector search → join payloads to the
  catalog for `source_uri` → ranked `SearchHit`s.
- **`cli.py`** — thin argparse layer: `va ingest <uri>`, `va query "<text>" -k N`,
  `va fixtures pull`, with a `--workdir` (default `.va`).

---

## 6. The hosting-agnostic design (the spine)

The architecture's hardest constraint: *the DGX Spark may not hold every model, so any
role must run locally or remotely without changing caller code.* The structure enforces
this with three seams:

1. **Role interface** (`roles/visual_embedder.py`) — callers depend only on the Protocol.
2. **Adapters** (`adapters/visual_embedder/*`) — `hash_inproc`, `siglip_inproc` today;
   `http_client` / cloud clients slot in identically tomorrow.
3. **Registry** (`registry.py`) — reads config and returns the right adapter.

Switching the Role-2 backend is a **one-line config edit**, no code change:
```yaml
# config/roles.yaml
visual_embedder: { backend: inproc, model: hash }     # tests/CI — stub, no download
visual_embedder: { backend: inproc, model: siglip }   # real model on the Spark GPU
# visual_embedder: { backend: http, endpoint: http://gpu-box:8000 }   # (future) remote
```
We literally exercised this: the test suite runs `hash`; the real Ferrari/snake runs used
a *separate* `run-siglip/config` selecting `siglip`, via `VA_CONFIG_DIR` — same pipeline
code, different model, different place it could run.

---

## 7. How models get downloaded & loaded on the GPU

This is the part that turns "code" into "AI". Concretely, for SigLIP:

1. **Install the heavy stack** — `pip install -e '.[siglip]'` pulled **torch 2.12.0+cu130**
   (a CUDA build that works on the GB10's aarch64), transformers, sentencepiece, protobuf.
   The venv grew to **4.9 GB**.

2. **Resolve where to load** — `runtime/device.py` checks `torch.cuda.is_available()` →
   `True` on the GB10 → device `cuda`. The profile `config/profiles/dgx-spark.yaml`
   specifies `dtype: float16` and `weights: google/siglip-so400m-patch14-384`.

3. **Download the weights (once)** — first time `SiglipEmbedder._build()` runs,
   `transformers` fetches the model from the HuggingFace Hub into
   `~/.cache/huggingface/hub/models--google--siglip-so400m-patch14-384` — **~3.3 GB** on
   disk. Subsequent loads read from that cache (no re-download).

4. **Load onto the GPU** — `AutoModel.from_pretrained(weights).to('cuda').eval()` places
   the ~400M-parameter model on the GB10. The `ModelManager` caches the loaded
   model+processor under a key like `siglip::google/siglip-so400m-patch14-384::cuda`, so
   every subsequent `embed_image`/`embed_text` in that process reuses it.

5. **Inference** — frames are processed in batches of 32; `get_image_features` runs on
   GPU, results moved to CPU as numpy and L2-normalized. Output dimensionality: **1152**.

The same machinery is model-agnostic: a different role (Whisper, YOLO-World, …) would add
its own `*_inproc` adapter and `_build()`; the manager, device resolution, and config
folding are shared.

---

## 8. Synthetic video clips (for tests)

We **generated** small videos in code — no real footage in the repo. `media/synth.py`
`write_color_video()` paints solid-color frames (e.g. 3s red, 3s green, 3s blue at 10fps,
64×64) and encodes an mp4 via imageio's bundled ffmpeg. These are a few KB each.

Why: paired with the **color-aware hash embedder**, a solid-red clip + the query "red
sports car" gives a *deterministic, assertable* result. That's how `tests/test_e2e.py`
proves the entire ingest→query path end-to-end with **no network, GPU, or model download**.
Honest caveat: a synthetic clip is a flat color field — there is **no object** in it. It
tests the *plumbing*, not visual recognition. Recognition is proven separately on real
footage (§10).

---

## 9. YouTube acquisition (real artifacts)

`sources/youtube.py` turns a URL into a local file:
- **Dedup key** — `extract_video_id()` normalizes any URL form to the 11-char id. Both
  `watch?v=GXPRSFL0UUA` and `/shorts/xDerjsxFkb4` reduce to their ids; that id is the
  `source_key`, so re-submitting the same video (any URL form) is detected.
- **Download** — yt-dlp pulls the best stream ≤480p into `<workdir>/cache/<id>.mp4`. We
  cap resolution because frame embeddings don't need 4K and it keeps fetches fast/small.
- **ffmpeg** — yt-dlp needs ffmpeg to merge separate video+audio streams; we symlink the
  `imageio-ffmpeg` binary as `ffmpeg` and pass `ffmpeg_location` (see §13).
- **Metadata** — title, duration, fps, resolution come back from yt-dlp's info dict and
  are written to the catalog.

Two real videos were downloaded:

| Video id | Title | Size | Resolution | Duration | Frames @1fps |
|---|---|---|---|---|---|
| `GXPRSFL0UUA` | Red Ferrari 458 Italia Racing | 11.9 MB | 640×360 | 188 s | 188 |
| `xDerjsxFkb4` | (King Cobra short) | 1.6 MB | (short/vertical) | ~60 s | 60 |

---

## 10. The actual runs & results

### 10.1 Synthetic end-to-end (stub embedder, in CI)
`va ingest <red/green/blue clip>` then `va query "red sports car"` returns the **red**
segment (0:00–0:02); "blue sky" returns the **blue** segment (0:06–0:08). Proves the wiring;
the "match" is purely color (the words "sports car" are ignored by the stub).

### 10.2 Positive test — real Ferrari video, real SigLIP (on GPU)
Ingested `GXPRSFL0UUA` (188 frames embedded through SigLIP on the GB10), then queried:

| Query | Top score | Moment | Verified frame |
|---|---|---|---|
| **"red sports car"** | **+0.115** | 1:07 | a real red Ferrari, side profile ✅ |
| **"a red ferrari on a track"** | **+0.184** | 1:51 | the Ferrari mid-corner with track cones ✅ |
| "people in a crowd" | +0.046 | 0:40 | (spectators) |
| "snow covered mountains" | +0.027 | 2:22 | (irrelevant — low) |
| "a blue ocean" | +0.021 | 2:09 | (irrelevant — lowest) |

Relevant queries scored **4–9× higher** than irrelevant ones, and the *more specific*
accurate description ("ferrari on a track") beat the generic one — and surfaced a
*different* moment. We extracted the top frames and **visually confirmed** they are real
red Ferraris. This is genuine image↔text recognition, not color matching.

### 10.3 Negative test — snake video (`.va-snake`, isolated)
Ingested `xDerjsxFkb4` (60 frames) into a separate workdir so the search space is only
snake frames:

| Query | Top score | Verdict |
|---|---|---|
| **"red sports car"** | **−0.0245** | ✅ no car — even the best frame is a cobra, scores *negative* |
| "a red ferrari on a track" | +0.0002 | ✅ ≈ zero |
| **"a snake"** | **+0.144** | positive control — model genuinely recognizes the snake |
| "a snake in the grass" | +0.122 | positive control ✅ |

Side-by-side, the **same** query "red sports car": **+0.115** on the Ferrari vs **−0.025**
on the snake. The top "red sports car" frame in the snake video was visually confirmed to
be a King Cobra in a terrarium. **Takeaway:** the absolute SigLIP score is a usable
relevance threshold — relevant content ~0.11–0.14, irrelevant ≈0 or negative — so a cutoff
around ~0.05–0.08 would make a no-match query correctly return *nothing*. (Not yet wired;
the pipeline currently always returns top-k regardless of score.)

> Note on SigLIP scores: they're small in absolute terms because SigLIP is trained with a
> sigmoid objective (not softmax contrastive like CLIP). **Ranking and relative gaps
> matter, not the raw magnitude.**

---

## 11. Artifacts on disk

| Artifact | Location | Size | What it is |
|---|---|---|---|
| Source code | `src/va/` | ~1,289 LOC | the package |
| Python venv | `.venv/` | **4.9 GB** | incl. torch+CUDA |
| SigLIP weights | `~/.cache/huggingface/hub/models--google--siglip-so400m-patch14-384` | **3.3 GB** | downloaded once |
| Ferrari video | `.va/cache/GXPRSFL0UUA.mp4` | 11.9 MB | downloaded |
| Ferrari vectors | `.va/vectors.npz` + `.json` | ~0.9 MB | 188 × 1152 float32 + payloads |
| Ferrari catalog | `.va/catalog.db` | 16 KB | the `videos` row |
| Snake video | `.va-snake/cache/xDerjsxFkb4.mp4` | 1.6 MB | downloaded |
| Snake vectors | `.va-snake/vectors.npz` + `.json` | ~0.28 MB | 60 × 1152 float32 |

Note the metadata-overhead point from the architecture doc, now measured for real:
188 frames × 1152-dim float32 ≈ **0.87 MB** of vectors for a 11.9 MB / 188s video — the
index is a small fraction of the source, dominated by the embeddings exactly as predicted.

---

## 12. Testing approach

**34 tests**, all runnable with `.venv/bin/pytest -q` (no network, GPU, or model download —
they use the stub embedder + synthetic clips):

| Test file | Covers |
|---|---|
| `test_contracts.py` | schema validation, dtype casting, JSON round-trip |
| `test_config.py` | roles+profile merge, unknown-role error |
| `test_runtime.py` | manager builds-once/caches/unloads; device fallback |
| `test_vector_store.py` | nearest-neighbor correctness, persistence |
| `test_catalog.py` | **idempotent dedup**, status transitions, error recording |
| `test_media.py` | probe + frame sampling counts/timestamps |
| `test_embedder.py` | stub satisfies Protocol, unit-norm, color matching |
| `test_sources.py` | all URL forms → same id, dispatcher, local sha256 |
| `test_e2e.py` | full ingest→query, idempotency, CLI |

The real-footage runs (Ferrari, snake) were **manual** — not yet automated tests (see §14).

---

## 13. Real-world issues hit & fixed

These only surfaced once we used real videos + the real model — exactly what the synthetic
test couldn't catch:

1. **yt-dlp couldn't merge streams** — it needs `ffmpeg` on PATH to combine ≤480p
   video+audio. The bundled `imageio-ffmpeg` binary has an odd name. **Fix:** symlink it as
   `ffmpeg` in a cache dir and pass `ffmpeg_location` to yt-dlp (`sources/youtube.py`).

2. **SigLIP tokenizer needed `protobuf`** — `SiglipTokenizer requires the protobuf
   library`. **Fix:** added `protobuf` to the `[siglip]` extra.

3. **transformers v5 changed the feature API** — `get_image_features` now returns a
   `BaseModelOutputWithPooling` object, not a bare tensor (`'…' object has no attribute
   'cpu'`). **Fix:** `siglip_inproc.py` now unwraps `image_embeds`/`text_embeds`/
   `pooler_output` before converting to numpy.

4. **Enum repr in CLI output** — printed `SourceType.local` instead of `local`. **Fix:**
   use `.value` in `cli.py`.

All four are committed; the 34-test suite stayed green throughout.

---

## 14. What is NOT done yet

Being explicit about the edges:
- **Only Role 2** (visual embedding) is implemented. Roles 1, 3–11 (scene detection,
  captioning, audio, objects, actions, reasoning, …) are designed in the plan but not built.
- **No remote/HTTP backend** for Role 2 yet — only in-process (`hash`, `siglip`). The
  agnostic seam exists; the `http_client` adapter is unwritten.
- **Real-footage runs are manual**, not automated tests. `tests/fixtures/sources.yaml`
  still has a `REPLACE_ME` placeholder (should be pinned to `GXPRSFL0UUA` / `xDerjsxFkb4`).
- **No relevance threshold** — query always returns top-k even when nothing is relevant
  (the snake video still *prints* a top hit, just with a negative score). A `--min-score`
  filter is the obvious next step (motivated directly by the negative test).
- **Re-ingesting a failed/partial video can double-add vectors** (the numpy store has no
  delete). Fine for PoC; needs delete-by-video_id before production.
- **Vector store is brute-force numpy** — exact but O(N); swap to Milvus/Qdrant at scale.

---

## 15. How to reproduce

```bash
# 0. setup
python3 -m venv .venv
.venv/bin/pip install -e .              # core only (stub embedder)
.venv/bin/pytest -q                     # 34 tests pass, no network/GPU

# 1. run the slice with the STUB on a synthetic clip
.venv/bin/python -c "from va.media.synth import write_color_video as w; \
  w('/tmp/c.mp4',[('red',(220,30,30),3.0),('blue',(30,30,220),3.0)],fps=10)"
.venv/bin/va --workdir /tmp/demo ingest /tmp/c.mp4
.venv/bin/va --workdir /tmp/demo query "red sports car"

# 2. run with the REAL SigLIP model on the GPU, against a YouTube URL
.venv/bin/pip install -e '.[siglip]'    # torch + transformers (downloads weights on 1st use)
VA_CONFIG_DIR=run-siglip/config .venv/bin/va --workdir .va \
    ingest "https://www.youtube.com/watch?v=GXPRSFL0UUA"
VA_CONFIG_DIR=run-siglip/config .venv/bin/va --workdir .va query "red sports car" -k 5
```

> The `VA_CONFIG_DIR=run-siglip/config` prefix selects the SigLIP backend without touching
> the default `hash` config the tests rely on. Switching the embedder changes the vector
> space, so use a fresh `--workdir` after changing models.

---

## 16. Addendum (2026-06-10): a model-verification finding worth remembering

*(The hike above describes the M1-era state; Roles 1, 4, 5, 6, 8 and 11 have since landed
following the same recipe — see CLAUDE.md for the current map. This addendum records one
finding from Role 11 validation that changes how we verify model output.)*

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
   flagging conflicts is a cheap self-check (candidate future `va ask --verify` mode).
3. **Model-quality ranking is task-dependent and must be measured, not assumed**: for
   attribute questions, claude-code > qwen-7B was only knowable from a real head-to-head
   (57s vs ~similar latency; subscription usage vs free local).

This also retroactively justifies the golden-fixtures `provenance:` split
(`vision-verified` vs `model-regression`): model output used as its own ground truth is
exactly how the red-shirt error would have fossilized into the test suite.

---

## 17. Addendum (2026-06-12): Role 10 (OCR) and the runtime-vs-model distinction

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

*End of hike. Current state and commands: [CLAUDE.md](CLAUDE.md). Remaining roles:
7 (action), 9 (diarizer), 3 (cross-modal); plus the ANTHROPIC_API_KEY decision
for the production claude-api reasoner backend.*
