# Performance & Productization Plan

Status: **proposed 2026-06-17.** Companion to `plan.md` (role roadmap), `qa-and-traceability-plan.md`
(stabilization), and `video-analytics-nvidia-comparison.md` (the VSS gap analysis this operationalizes).
This document is about **turning the PoC into an appliance**: the performance work needed to serve a
multi-tenant home device, and the explicit decision of **which components stay Python vs. move to a
compiled language (Go/Rust)**.

Target product (the workload this plan designs for):
1. A sealed **DGX Spark appliance** (GB10 Grace-Blackwell, aarch64, 128 GB unified memory, NVDEC/NVENC).
2. A **management app** that drives the box over an **API**.
3. **Phone photo/video** analytics for **≤ 10 home users** (multi-tenant; bursty ingest).
4. **Home security-camera** analytics — **continuous 24×7** ingest, footage stored **locally**.
5. Hosting **≤ 10 OpenClaw-style agent harnesses** that call OpenAI/Anthropic.

---

## 0. The framing principle (the answer to "what should be Rust/Go?")

**~95 % of the compute is already compiled.** PyTorch/CUDA, ffmpeg (C), onnxruntime (C++), and
numpy/BLAS do the heavy lifting; the Python in this repo is **orchestration glue**. Rewriting model
inference in Rust would *lose* the CUDA/transformers ecosystem and gain nothing. So the rule is:

> Compiled languages earn their keep in exactly three places, **none of which is model inference**:
> (1) the always-on **data-plane** (camera decode + motion-gating), (2) the concurrent **control-plane**
> (multi-tenant API gateway + agent host), and (3) the **vector index** (adopt a compiled engine; don't
> hand-roll). Everything else stays Python.

And a tier of **algorithmic fixes worth 10–100× needs no language change at all** (§2). Those come first —
they are cheaper and bigger than any rewrite.

Box-specific bonus: **Go and Rust cross-compile cleanly to aarch64.** Grace-Blackwell has already burned
us on fragile ARM Python wheels (paddlepaddle segfault, torchaudio/torchcodec breakage — see CLAUDE.md
gotchas). Compiled components sidestep that whole class of pain.

---

## 1. The workload shift (why the PoC's hot paths change)

| Dimension | PoC today | Appliance target |
|---|---|---|
| Ingest trigger | one-shot YouTube URL / local file (`sources/base.py` knows only these) | **continuous RTSP cameras** + bursty phone uploads |
| Concurrency | one ingest worker + one ask worker, serialized (`web/jobs.py`) | ≤ 10 users + ≤ 10 agents + N cameras, concurrent |
| Tenancy | single-tenant; no `user_id` anywhere | **multi-tenant** isolation + per-user retention |
| Corpus size | hundreds of vectors; brute force is fine | millions+ (10 cams × 1 fps × 24 h ≈ **864 k vectors/day**) |
| Storage growth | bounded (a few clips) | **unbounded** camera footage → needs retention/eviction |
| GPU use | eager, one image at a time, FP16 | batched + quantized (FP8/INT8/FP4 — the Blackwell point) |

The PoC is correct for "Ctrl-F over a few videos." Three of those rows (continuous ingest, concurrency,
corpus size) are the ones that break the current implementation, and they map onto the three
compiled-language seams.

---

## 2. Bottlenecks, grounded in the current code

These are wasted work / scaling cliffs in the **existing** paths. Pure-Python fixes; biggest ROI first.

**B1 — Each video is decoded 5–6× per ingest.** `ingest.py` walks `sample_frames()` once for object
detection (`ingest.py:171`) and **again** for visual embedding (`ingest.py:215`); the scene detector
decodes at 3 fps, `keyframes_for_spans` decodes, OCR decodes, the action recognizer decodes, Whisper
extracts audio. ~6 full passes over the file.
→ **Decode once, sample once, fan frames out to every frame-consuming role.** Largest single ingest win.

**B2 — `sample_frames` decodes every frame, then strides in Python.**
```python
for idx, frame in enumerate(reader):        # frames.py:78 — decodes ALL 30 fps
    if idx % stride == 0:                    # …then keeps 1 fps and discards 29/30
```
→ ~~Drop frames *before* decode (ffmpeg `-vf fps=1`, or seek). ~10–30× on the decode portion.~~
**Tested & rejected (2026-06-18).** The "before decode" premise is false: inter-frame codecs
(P/B-frames) can't skip-decode non-keyframes, and ffmpeg's `fps` filter runs *after* the decoder —
it drops at the output stage, so the decode work is unchanged. The only real saving is the
pipe-transfer + numpy conversion of the dropped frames, measured **content/codec-specific and
unreliable: 0.99×–1.60×, marginal even on real static-camera footage** (birdfeeder 1.60× did NOT
reproduce — EufyCam garden cam 1.09×, Sparrow Hills 0.99×) and an outright regression on motion
content (toretto 0.88×). A genuine decode speedup needs a *different* mechanism: **NVDEC hardware
decode** (`-hwaccel cuda`; already planned for the camera edge, §4-A / PP.6) or **keyframe-only
sampling** (~1/GOP the decode, but irregular keyframe-aligned timestamps — a sampling-semantics
change). The shipped ingest win is **B1 (decode-once) alone**.

**B3 — Vector search reloads the whole corpus from disk, twice, per query.** `query()` calls
`store.count()` then `store.search()` (`query.py:21-23`); each calls `ShardedVectorStore._shards()`
(`sharded.py:29`), which builds a `NumpyFlatVectorStore` per shard whose `__init__` reads the `.npz` +
`.json` off disk (`numpy_flat.py:34`). No caching, no mmap.
→ Persistent in-memory / mmap index held by the process (prerequisite for B4).

**B4 — Brute-force O(N·D) cosine over the entire corpus** (`numpy_flat.py:69` `self._vecs @ q`). The docs
already note it "falls over at millions." Cameras hit millions in a week.
→ Sublinear ANN (§4-C).

**B5 — SQLite opened, schema-applied, and closed per request.** `Catalog.__init__` runs `apply_schema()`
(8 `CREATE TABLE` + 6 `CREATE INDEX`) on **every** open (`catalog_sqlite.py:34-36`); the web layer opens/
closes per call; no WAL, so a background ingest writer blocks all readers; `query.py` does one
`catalog.get()` per hit in a loop.
→ `PRAGMA journal_mode=WAL` + `synchronous=NORMAL`, reuse a pooled connection, apply schema once at
startup, batch the per-hit lookups. (Postgres later, behind the same store interface — P5.)

**B6 — Serial single-worker queues, in-memory job state** (`web/jobs.py`): one ingest worker, one ask
worker, job records lost on restart. 10 users + cameras cannot funnel through one serial thread.
→ Persistent queue + GPU-aware batched scheduler (§4-B, PP.4).

**B7 — Eager, unbatched, unquantized inference** (flagged in `nvidia-comparison.md`): single-image
PyTorch calls, FP16, no TensorRT. On Blackwell, low-precision + dynamic batching is the entire hardware
value, and batching is what lets **one GPU serve 10 users at once** instead of serializing.

**B8 — No tenancy, no streaming source, no retention.** No `user_id`/`camera_id` columns; `sources/`
has no RTSP backend; nothing rotates raw footage. These are product gaps, not slowdowns — but they shape
where the new compiled components sit.

---

## 3. Hot per-item loops (compiled only if a profiler points there)

- **IoU tracker** (`iou_inproc.py:54`): pure-Python frames×detections×tracks loop with a `model_copy`
  per detection. The fix is **make compiled ByteTrack the default for camera ingest** (already behind the
  Protocol), not rewrite the stub in Rust.
- **Histogram scene detector / NMS / bbox math**: per-frame numpy, fine at PoC fps; leaves Python
  naturally once the Go/Rust edge daemon owns the motion-gate (§4-A).
- **deep_scan counting** (`deep_scan.py`): the arithmetic is cheap Python and correct; its cost is the
  VLM calls (GPU). Win = batching + the caching it already does — no rewrite.

---

## 4. The language decision

| Component | Today | Target | Language | Why this language |
|---|---|---|---|---|
| Model inference (all roles) | Python/PyTorch | unchanged, behind a batching server | **Python** | already CUDA/C; ecosystem is the asset |
| Registry / adapters / contracts / config (the spine) | Python | unchanged | **Python** | not hot; flexibility is the point |
| Reasoner / ask / query planning | Python | unchanged | **Python** | low call-rate, model-bound |
| **A. Camera edge / ingest daemon** | *does not exist* | new | **Go** (or Rust) | many always-on RTSP loops + NVDEC + motion-gate; GIL-hostile |
| **B. API gateway / control-plane / agent host** | FastAPI + serial queues | new | **Go** | multi-tenant, websockets/SSE, 10 I/O-bound agents, device mgmt |
| **C. Vector index** | numpy brute force | adopt engine | **Rust/C++ (embedded)** | sublinear ANN + mmap + metadata filters, for free |

### A. Camera edge / ingest daemon → Go *(the clearest new case)*
A long-lived daemon that owns the RTSP connections (one goroutine per camera, reconnect/backpressure),
**hardware-decodes on NVDEC** (Blackwell decoders; unified memory ⇒ zero-copy to GPU), runs a cheap
**motion / change gate**, muxes footage to local storage with a **retention policy**, and pushes only
frames-with-activity to the Python ML workers over gRPC. The motion-gate is the **#1 lever for continuous
footage — it cuts the ML workload 90–99 %** (don't run SigLIP+YOLO+Qwen on 86,400 s/day of an empty
hallway). Python's GIL + per-frame object churn make it the wrong tool for 10 always-on camera loops.

### B. API gateway / control-plane / agent host → Go
The management API (goal #2), the agent harnesses (goal #5), and serving 10 users converge on one front
door: auth, multi-tenant routing, request fan-out, SSE/websocket streaming, job submit/status, camera
supervision, rate-limiting, outbound OpenAI/Anthropic calls. Exactly Go's sweet spot; exactly what
`web/app.py` + `SerialQueue` are not. The 10 agents are I/O-bound (waiting on LLM APIs) — trivial in Go,
miserable under one Python GIL. **This preserves the hosting-agnostic spine**: the Go gateway is just
another caller of the role HTTP adapters already designed and parity-tested (plan.md M1-05). Python keeps
the ML; Go owns concurrency + tenancy. `ask.py`/the reasoner are **not** rewritten.

### C. Vector index → adopt a compiled engine
Replace `numpy_flat.py` behind the existing `VectorStore` Protocol (`storage/vector/base.py`) with:
- **LanceDB** (Rust, embedded, on-disk columnar, multimodal, mmap) — best fit for on-device data that
  won't fit in RAM; or
- **Qdrant** (Rust; embedded *or* sidecar service) for filtering + a clean multi-tenant service seam; or
- **usearch / FAISS** (C++/Rust HNSW) for pure ANN.

All give sublinear search + mmap + **metadata filtering** (`user_id` / `camera_id` / time-range — needed
for tenancy anyway). The docs name Milvus for M10; for a **single box** prefer LanceDB/Qdrant (Milvus is a
distributed cluster — overkill). A hand-written Rust SIMD kernel is possible but just reimplements
usearch; only if filtering needs are exotic.

### Bigger than any rewrite: serve models batched + quantized (Python stays)
Put the embedder/VLM/Whisper behind **Triton / vLLM / NIM** and **quantize (FP8/INT8/FP4)**. This turns
the serial GPU queue into a concurrent batched one — the actual requirement for 10 users on one GPU — and
slots in as `backend: http` behind the role Protocols already in place.

---

## 5. Target topology

```
[Go edge daemon]   cameras · NVDEC decode · motion-gate · local storage + retention
       │ gRPC (only frames with activity)
[Go API gateway]   auth · multi-tenant · device mgmt · streaming · agent host (≤10)
       │ http (the existing role adapters — hosting-agnostic spine, unchanged)
[Python ML pool]   the va pipeline, behind the registry
       │
[Triton/vLLM/NIM]  batched + quantized SigLIP / Qwen-VL / Whisper on the GPU
       │
[LanceDB/Qdrant] vectors+filters   ·   [Postgres] structured   ·   media on local disk
```

---

## 6. Milestones

Build order **PP.0 → PP.1/PP.2/PP.3 (parallel) → PP.4 → PP.5 → PP.6**. PP.0–PP.3 are pure-Python wins
that need no new language and unblock everything; the compiled components (PP.5, PP.6) come after the
Python data-plane and serving layer they depend on exist.

| Step | Deliverable | Done when | Depends on |
|---|---|---|---|
| **PP.0** | **Benchmark + profile harness**: scripted ingest-time / query-latency / recall@k numbers on a fixed real workdir (extends the golden-query harness). Per the repo rule *determinism ≠ correctness* — every later step is judged against these baselines, and ANN recall is validated vs. the brute-force ground truth. | A `va bench` (or test) prints ingest s/min, query p50/p95, and corpus size; baseline captured. | — |
| **PP.1** | **Decode-once fan-out** in `ingest.py`: one decode pass feeds both visual embedding and object detection (was two passes over identical frames) — fixes B1. ✅ shipped 2026-06-18: 1.32× total ingest, every video 1.18–1.53× faster, counts identical. *(ffmpeg-side fps sampling for B2 was tested & rejected — see the B2 note.)* | Ingest decodes the file once; wall-clock drops measurably vs PP.0; offline tests still green. | PP.0 |
| **PP.2** | **Vector engine swap** behind `VectorStore`: persistent/mmap index (fixes B3) → LanceDB/Qdrant/usearch with `video_id`/`user_id`/`camera_id` + time filters (fixes B4). | Query no longer reloads per call; recall@k vs brute-force ≥ target on PP.0 set; remove/reingest still work. | PP.0 |
| **PP.3** | **SQLite hardening**: WAL + `synchronous=NORMAL`, pooled/reused connection, schema applied once, batched hit-lookups (fixes B5); add tenancy columns. (Postgres later, same interface.) | Concurrent reader + ingest writer don't block; `user_id` scoping enforced; tests green. | PP.0 |
| **PP.4** | **Batched + quantized serving**: SigLIP/Qwen-VL/Whisper behind Triton/vLLM/NIM as `backend: http`; replace serial queues with a GPU-aware batched scheduler (fixes B6, B7). | N concurrent queries batch on one GPU; throughput scales with batch; parity vs in-proc within tolerance. | PP.1 |
| **PP.5** | **Go control-plane**: API gateway (auth, multi-tenant routing, device mgmt, streaming) + agent host for ≤10 OpenClaw harnesses, fronting the Python ML pool over the role HTTP adapters. | Device API + 10 concurrent agent sessions + per-user auth run outside Python; existing web UI served through it. | PP.3, PP.4 |
| **PP.6** | **Go/Rust camera edge daemon**: RTSP intake + NVDEC decode + motion-gate + retention, pushing activity frames to the ML pool (fixes B8, §4-A). | A live camera ingests continuously; motion-gating measurably cuts ML calls vs naive 1 fps; footage rotates per policy. | PP.1, PP.4 |

---

## 7. Validation (how we prove each step, not just that it's deterministic)

- **PP.0 baselines are the contract.** No step claims a win without a before/after number from `va bench`.
- **ANN must be checked for recall, not just speed.** Per the fixture-audit lesson (*a green test can be
  wrong*), PP.2 validates recall@k against the brute-force result on the golden set — a fast index that
  silently drops the right hit is a regression, not a win.
- **Serving parity.** PP.4 reuses the existing local-vs-http parity tests (plan.md M1-05) so a batched
  remote model returns the same hits as in-process within tolerance.
- **Offline suite stays green throughout.** Every step keeps the stub-backed offline tests passing; new
  compiled components get their own harness (Go/Rust test + a Python integration test over the gRPC/HTTP
  seam), mirroring P3's "every part has its own harness."

---

## 8. Open questions / future

- **Embedded vs. sidecar vector engine** — LanceDB (in-process, simplest) vs. Qdrant (a service, cleaner
  multi-tenant boundary, but another process to operate on the appliance). Decide at PP.2 against the
  filtering needs that emerge from tenancy (PP.3).
- **One Go binary or two?** Edge daemon and gateway could be one process or split. Split is cleaner
  (cameras shouldn't restart when the API redeploys); revisit once both exist.
- **Audio-event gate for cameras** — Role 3 (the still-unimplemented audio tier) is a natural second gate
  for security footage (glass-break, alarms) alongside motion. Ties into the existing Role-3 use-case.
- **Postgres cutover threshold** — stay on WAL SQLite until concurrent-writer contention is measured
  (PP.3 instruments it); migrate behind the store interface when it bites, not preemptively.
- **Retention policy** — raw footage rotation is a product/privacy decision (per-user quota? motion-only
  retention? days?), not just an engineering one — needs the user's input before PP.6.

## 9. Non-goals

- Rewriting model inference, the registry/adapter spine, or the reasoner in a compiled language.
- A distributed/multi-node vector cluster (Milvus) — this is a single box.
- Real-time (sub-second) streaming analytics — near-real-time with motion-gating is the target, not VSS's
  live-stream latency.
