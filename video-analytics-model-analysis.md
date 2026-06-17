# Video Analytics: Model Analysis & Selection Guide

*Created: 2026-06-03 | Status: Living Document*
*Companion to: [video-analytics-solution-architecture.md](video-analytics-solution-architecture.md)*

---

## How to Read This Document

This document catalogs AI models organized by the **role** they play in the video analytics pipeline defined in the architecture doc. Each role is a function the system needs — you pick one model per role (or sometimes zero, if the role is optional for your use case).

**Columns explained:**

| Column | What It Means |
|---|---|
| **Params** | How many parameters (weights) the model has. Bigger = smarter but more resources. |
| **VRAM** | GPU memory needed to run it. FP16 = full precision, INT4 = compressed (lower quality, fits smaller GPUs). |
| **Open Source** | Can you download and run it yourself? |
| **License** | Legal terms — Apache 2.0 and MIT are fully permissive. Some have restrictions. |
| **NIM** | Available as a one-liner Docker container via NVIDIA NIM? |
| **SotA** | State of the Art — is this currently the best model for this role? |
| **Local on DGX Spark** | Can it run on a DGX Spark (128GB unified memory)? |
| **Local on 24GB GPU** | Can it run on a consumer GPU like RTX 4090 or A6000? |

---

## Model Role Map (Quick Reference)

| Role | What It Does (One-Liner) | # Models | Required? |
|---|---|---|---|
| [1. Scene Boundary Detector](#role-1-scene-boundary-detector) | Splits video into meaningful segments | 3 | Yes |
| [2. Visual Embedding Model](#role-2-visual-embedding-model) | Converts frames + text to vectors for search | 5 | Yes |
| [3. Cross-Modal Embedding Model](#role-3-cross-modal-embedding-model) | Embeds audio + image + text in shared space | 1 | Optional |
| [4. VLM Captioner](#role-4-vlm-captioner) | Generates text descriptions of video segments | 6 | Yes |
| [5. Object Detector](#role-5-object-detector) | Finds and labels objects in frames | 4 | Yes |
| [6. Object Tracker / Segmenter](#role-6-object-tracker--segmenter) | Follows objects across frames over time | 5 | Yes |
| [7. Action / Event Recognizer](#role-7-action--event-recognizer) | Identifies temporal actions (eating, running) | 3 | Recommended |
| [8. Speech-to-Text Model](#role-8-speech-to-text-model) | Transcribes spoken audio to text | 4 | Yes (if audio) |
| [9. Speaker Diarizer](#role-9-speaker-diarizer) | Identifies who is speaking when | 2 | Optional |
| [10. OCR Model](#role-10-ocr-model) | Reads text visible on screen | 5 | Optional |
| [11. Reasoning LLM](#role-11-reasoning-llm) | Thinks through complex analytical queries | 5 | Yes |

---

## Role 1: Scene Boundary Detector

**Architecture reference:** Ingestion Pipeline, Step 2

| Model | Source | Params | VRAM | Open Source | License | Local on DGX Spark | Local on 24GB GPU | NIM | SotA | Framework | Key Strength | Key Weakness |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **TransNetV2** | Czech Technical Univ. | ~1M | <1GB | Yes | MIT | Yes | Yes | No | Yes | PyTorch | Best accuracy, handles gradual transitions | Requires GPU for speed |
| **PySceneDetect** | Open Source | N/A (algorithmic) | 0 (CPU) | Yes | BSD | Yes | Yes (CPU) | No | No | OpenCV | No GPU needed, simple to use | Misses gradual transitions, only detects hard cuts |
| **FFmpeg scene filter** | FFmpeg | N/A (algorithmic) | 0 (CPU) | Yes | LGPL | Yes | Yes (CPU) | No | No | FFmpeg | Zero dependencies, fastest | Very rough, many false positives/negatives |

**Recommendation:** TransNetV2 for quality, PySceneDetect if you want zero GPU usage for this step.

---

## Role 2: Visual Embedding Model

**Architecture reference:** Ingestion Pipeline, Step 3 (frame embedding) + Query Pipeline, Tier 1

| Model | Source | Params | VRAM (FP16) | VRAM (INT8) | Open Source | License | Local on DGX Spark | Local on 24GB GPU | NIM | SotA | Multi-Frame | Key Strength | Key Weakness |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **SigLIP (SO400M)** | Google | 400M | ~1.5GB | ~0.8GB | Yes | Apache 2.0 | Yes | Yes | No | Yes | No (single image) | Best zero-shot text→image accuracy | Slightly newer, smaller ecosystem than CLIP |
| **NV-CLIP** | NVIDIA | ~400M | ~1.5GB | ~0.8GB | Yes (via NIM) | NVIDIA license | Yes | Yes | Yes | No | No | TensorRT optimized, one-liner deploy | NVIDIA-locked, slightly weaker than SigLIP |
| **CLIP (ViT-L/14)** | OpenAI | 428M | ~1.6GB | ~0.8GB | Yes | MIT | Yes | Yes | No | No | No | Huge ecosystem, well-tested, many tools built on it | Weaker on fine-grained visual details |
| **EVA-CLIP** | BAAI | 307M-4.4B | 1.2-16GB | 0.6-8GB | Yes | MIT | Yes | Depends on size | No | Competitive | No | Strong performance across sizes | Less ecosystem support |
| **DINOv2** | Meta | 86M-1.1B | 0.3-4GB | N/A | Yes | Apache 2.0 | Yes | Yes | Yes (NV-DINOv2) | Yes (visual features) | No | Best visual features for similarity/clustering | **No text encoder** — can't do text→image search alone |

**Important note on DINOv2:** It's the best model for *visual similarity* (find frames that look like this frame), but it **cannot** do text-to-image search because it has no text encoder. Use it alongside SigLIP/CLIP, not as a replacement. See also: [Models Spanning Multiple Roles](#models-spanning-multiple-roles).

**Recommendation:** SigLIP SO400M as primary. Add DINOv2 only if visual similarity search is a priority.

---

## Role 3: Cross-Modal Embedding Model

**Architecture reference:** Ingestion Pipeline, Step 3 (audio embedding) + Query Pipeline (cross-modal search)

**Reframing this role.** Its name suggests "one shared space for all modalities" (which only ImageBind provides), but the actual product need is narrower: **search non-speech audio with text** — "glass breaking", "dog barking", "engine starting" — sounds that Role 8 (Speech-to-Text) ignores because they aren't words. Since user queries arrive as *text*, text→audio retrieval is the capability that matters; direct audio↔image similarity is a niche. That reframing opens up better-fitting alternatives than ImageBind.

| Model | Source | Params | VRAM | Open Source | License | Local on DGX Spark | Local on 24GB GPU | NIM | SotA | Modalities | Key Strength | Key Weakness |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **LAION-CLAP** | LAION | ~150M | <1GB | Yes | CC0 / Apache 2.0 | Yes | Yes | No | Yes (text↔audio) | Audio, Text | "CLIP for audio" — trained specifically on text↔audio pairs, beats ImageBind at audio retrieval, license-clean, light | Separate vector space from SigLIP — no direct audio↔image similarity (see correlation note below) |
| **BEATs / AST / PANNs** | Microsoft / MIT / CVSSP | 80-300M | <1GB | Yes | MIT / BSD / Apache 2.0 | Yes | Yes | No | Yes (BEATs, tagging) | Audio → labels | Structured sound events (AudioSet ~527 classes) → SQL-countable rows; fills the existing `audio_events` table | Closed vocabulary, classification not retrieval — can't find sounds outside the label set |
| **ImageBind** | Meta | ~1.2B | ~4.5GB | Yes | CC-BY-NC 4.0 | Yes | Yes | No | Yes (unique shared space) | Image, Text, Audio, Depth, Thermal, IMU | **Only model** embedding audio+image+text in ONE space → direct audio↔visual similarity | Weaker text→audio than CLAP; **non-commercial license**; 8× heavier |

**Who correlates frames with audio if we use CLAP?** We do — at the application layer, and the machinery already exists. With ImageBind, "does this sound match this frame" is one cosine similarity inside a shared space. With CLAP + SigLIP there are **two separate vector spaces**, so cross-modal correlation becomes a **temporal join, not vector math**: every embedding is tagged `video_id` + `timestamp`, so a query like "glass breaking" runs text→CLAP (audio windows) and text→SigLIP (frames) independently, then the results merge by time proximity — the same rank-fusion the system already needs to merge transcript hits with visual hits. No new architecture; the planner just treats audio hits as one more result stream keyed by time. The **one capability a join cannot reconstruct**: querying *by* an audio clip to find visually similar frames (or vice versa). That direct audio↔visual similarity genuinely requires the shared space — ImageBind remains the only option for it.

**License caveat (ImageBind):** CC-BY-NC 4.0 means **non-commercial use only**. For a commercial product you'd need to negotiate with Meta — or simply use CLAP + SigLIP, which avoids the issue entirely.

**Recommendation:** Treat Role 3 as "text→audio retrieval": **LAION-CLAP** as primary backend (own vector namespace alongside SigLIP's, correlation via temporal join). Add an AudioSet tagger (BEATs) later if *counting/alerting* on sound events matters — the `audio_events` table in the schema is already waiting for it. Reserve ImageBind for the rare case where direct audio↔visual similarity search is a hard requirement *and* the license is acceptable. Skip the whole role for MVP.

---

## Role 4: VLM Captioner

**Architecture reference:** Ingestion Pipeline, Step 4 (keyframe captioning)

These models also serve as Role 11 (Reasoning LLM) when used at query time. The distinction:
- **Role 4 context:** Runs at ingest time, batch processing, needs to be fast and cost-efficient → prefer self-hosted
- **Role 11 context:** Runs at query time, on-demand, needs to be smart → cloud APIs acceptable

| Model | Source | Params | VRAM (FP16) | VRAM (INT4) | Open Source | License | Local on DGX Spark | Local on 24GB GPU | NIM | SotA | Multi-Frame | Key Strength | Key Weakness |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Qwen2.5-VL-72B** | Alibaba | 72B | ~144GB | ~40GB | Yes | Apache 2.0 | Yes (INT4) | No | No | Yes (open VLMs) | Yes (video native) | Best open-source VLM, video-native input | Huge — needs INT4 quantization even on DGX Spark |
| **Qwen2.5-VL-7B** | Alibaba | 7B | ~14GB | ~4GB | Yes | Apache 2.0 | Yes | Yes | No | No | Yes | Good quality at small size, fits consumer GPUs | Weaker reasoning than 72B |
| **NVILA** | NVIDIA | 8B-15B | 16-30GB | 8-15GB | Yes | NVIDIA license | Yes | Yes (8B) | Yes | Competitive | Yes (100+ frames) | Processes many frames efficiently, NIM deploy | NVIDIA ecosystem lock-in |
| **VILA-1.5** | NVIDIA | 3B-40B | 6-80GB | 3-40GB | Yes | NVIDIA license | Yes | Yes (3-13B) | Yes | Competitive | Yes | Range of sizes, NIM support | Surpassed by Qwen2.5-VL on benchmarks |
| **InternVL2.5** | Shanghai AI Lab | 8B-78B | 16-156GB | 8-40GB | Yes | Apache 2.0 | Yes (INT4 for 78B) | Yes (some sizes) | No | Competitive | Yes | Strong multi-image understanding | Less ecosystem than Qwen |
| **Llama 4 Scout** | Meta | 17B active (109B total MoE) | ~60GB | ~30GB | Yes | Llama Community | Yes | Maybe (8B) | No | Competitive | Yes | Open weights, large context (10M tokens) | MoE architecture uses more memory than dense models |
| **Gemini 2.0 Flash** | Google | Proprietary | N/A (cloud) | N/A | No | Proprietary | No | No | No | Yes (cloud VLMs) | Yes (native video) | Cheapest cloud VLM, native video input, fast | Cloud-only, data leaves your machine |
| **Claude Sonnet** | Anthropic | Proprietary | N/A (cloud) | N/A | No | Proprietary | No | No | No | Yes (reasoning) | Yes (multi-image) | Best reasoning quality for complex descriptions | Cloud-only, most expensive, no native video |

**Recommendation for ingest (Role 4):** Qwen2.5-VL-7B on consumer GPU, Qwen2.5-VL-72B (INT4) on DGX Spark. Gemini Flash if going cloud.
**Recommendation for reasoning (Role 11):** Claude Sonnet for best quality. Qwen2.5-VL-72B as self-hosted fallback.

---

## Role 5: Object Detector

**Architecture reference:** Ingestion Pipeline, Step 5 (object detection)

| Model | Source | Params | VRAM | Open Source | License | Local on DGX Spark | Local on 24GB GPU | NIM | SotA | Open Vocabulary | Key Strength | Key Weakness |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **YOLO-World** | Tencent AILab | 12-52M | 0.5-2GB | Yes | GPL 3.0 | Yes | Yes | No | Yes (speed) | Yes | Fastest open-vocab detector, great for ingest-time | GPL license may be restrictive for commercial use |
| **GroundingDINO** | IDEA Research | ~172M | ~3GB | Yes | Apache 2.0 | Yes | Yes | No | Yes (accuracy) | Yes | Best accuracy for text-prompted detection | Slower than YOLO-World (3-5x) |
| **YOLOv10** | Tsinghua | 6-56M | 0.3-2GB | Yes | AGPL 3.0 | Yes | Yes | No | Yes (closed-vocab speed) | No (fixed classes) | Fastest detector overall | Fixed class set — can't detect arbitrary objects |
| **Detectron2** | Meta | Varies | 2-8GB | Yes | Apache 2.0 | Yes | Yes | No | No | No | Mature, well-documented, COCO-trained | Superseded by GroundingDINO for open-vocab use |

**Key distinction: Open-vocabulary vs closed-vocabulary.**
- **Open vocabulary** (YOLO-World, GroundingDINO): You describe what to find in text — "squirrel", "red car", "coffee mug". Can detect anything.
- **Closed vocabulary** (YOLOv10, Detectron2): Trained on a fixed set of ~80-365 classes. If your object isn't in the training set, it can't find it.

For a general-purpose video search system, **open vocabulary is required**.

**Recommendation:** YOLO-World for speed at ingest. GroundingDINO for precision at query time.

---

## Role 6: Object Tracker / Segmenter

**Architecture reference:** Ingestion Pipeline, Step 5 (object tracking)

| Model | Source | Params | VRAM | Open Source | License | Local on DGX Spark | Local on 24GB GPU | NIM | SotA | Tracking Type | Key Strength | Key Weakness |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **SAM 2** | Meta | ~200M | ~4GB | Yes | Apache 2.0 | Yes | Yes | No | Yes | Pixel-level mask, any object | Best-in-class: pixel-level tracking, no training needed, handles occlusion | Heavier than bbox trackers |
| **ByteTrack** | ByteDance | ~0 (algorithmic) | <0.1GB | Yes | MIT | Yes | Yes (CPU) | No | No | Bounding box | Extremely lightweight, fast, simple | Boxes only (no masks), breaks on occlusion |
| **DeepSORT** | Open Source | ~10M | ~0.5GB | Yes | GPL 2.0 | Yes | Yes | No | No | Bounding box + ReID | Handles re-identification (same person reappearing) | Slower than ByteTrack, less accurate than SAM 2 |
| **BoT-SORT** | Open Source | ~10M | ~0.5GB | Yes | MIT | Yes | Yes | No | Competitive | Bounding box | Good balance of speed and accuracy | Still box-level only |
| **CoTracker** | Meta | ~30M | ~1GB | Yes | CC-BY-NC 4.0 | Yes | Yes | No | Yes (point tracking) | Dense point tracking | Tracks any point through video, understands motion | Different use case — tracks points, not objects |

**SAM 2 is the clear winner** for object tracking in this pipeline. It provides pixel-level masks (exact outlines, not rough boxes), works on any object without training, and handles the hard cases (objects going behind things, changing shape, reappearing). The only reason to use ByteTrack is if you need absolute maximum speed and boxes are good enough.

**CoTracker note:** It tracks *points*, not *objects*. Useful for understanding motion trajectories ("where did the ball go?") but not for "follow this squirrel." Listed here because it's the closest role, but it's a specialized tool.

**Recommendation:** SAM 2 as primary tracker. ByteTrack as lightweight fallback for non-critical tracking.

---

## Role 7: Action / Event Recognizer

**Architecture reference:** Ingestion Pipeline, Step 5b (action recognition)

| Model | Source | Params | VRAM | Open Source | License | Local on DGX Spark | Local on 24GB GPU | NIM | SotA | Input | Key Strength | Key Weakness |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **X-CLIP** ✅ *(built)* | Microsoft | ~200M | <1GB | Yes | MIT | Yes | Yes | No | No (older) | 8 frames | **Zero-shot / open-vocab** (scores arbitrary text phrases), tiny, transformers-native, works on aarch64 | Forced-choice softmax (no native abstention — mitigated with a "none" foil); fixed candidate set; coarse temporal |
| **ViCLIP** (InternVideo) | Shanghai AI Lab | ~430M (ViT-L) | ~2GB | Yes | Apache 2.0 | Yes | Yes | No | Competitive | 8 frames | Same contrastive shape as X-CLIP but InternVid-trained → better zero-shot action discrimination | Same forced-choice limitation; heavier than X-CLIP |
| **VideoMAE v2** | Meta | 300M-1B | 2-6GB | Yes | Apache 2.0 | Yes | Yes | No | Competitive | 16-32 frames | Self-supervised pre-training (no labels needed to pre-train), efficient | **Closed-vocab** (Kinetics labels) — can't answer open-domain action queries |
| **InternVideo2** | Shanghai AI Lab | 1B-6B | 4-24GB | Yes | Apache 2.0 | Yes | Yes (1B) | No | Yes | 8-64 frames | Strongest overall benchmarks, multi-task capable | Custom (non-transformers) stack; large; runtime-integration risk |
| **TimeSformer** | Meta | 121M | ~2GB | Yes | Apache 2.0 | Yes | Yes | No | No | 8-96 frames | Divided space-time attention, efficient | Older; closed-vocab; surpassed by VideoMAE/InternVideo |

### Decision (as built) — X-CLIP — *Status: Accepted 2026-06-12*

**Chosen:** **X-CLIP** (zero-shot video-text contrastive) as the always-on *ingest-tier* backend,
with a confidence floor + a `NO_ACTION = "no particular action"` **abstention foil** in the
candidate set. Two-tier plan: this cheap scored model labels every segment at ingest; a
*query-time* open-vocab pass (the resident Qwen2.5-VL) handles specific/rare actions on demand
(**not built yet**).

**Why this deviates from the original "InternVideo2 (1B)" recommendation above:**
- **Open-vocab was the priority.** Queries are arbitrary text, so the action vocabulary must be
  caller-chosen. That rules out **VideoMAE/TimeSformer** (closed Kinetics label set, skewed to
  human-activity classes — "snake in a tank" isn't in Kinetics).
- **Runtime risk, learned the hard way.** **InternVideo2** is a custom non-transformers stack.
  After paddlepaddle segfaulted at inference on this aarch64 box (see Role 10), we now prefer
  runtimes already proven here. X-CLIP is transformers-native and ran first try.
- **Right tier, right cost.** A tiny contrastive encoder over every segment is cheap and emits a
  *thresholdable score* — exactly what an always-on ingest pass needs. The expensive open-vocab
  brain is reserved for the query tier (where it's also the Role 11 reasoner).

**Validated (2026-06-12):** Ferrari → "driving a car" 0.94–0.99 across all 11 segments;
birdfeeder → "feeding animals" 0.81; the abstention foil left confident-correct labels intact
while trimming borderline ones (dress montage 29 → 23 events).

**Known limitations (the reasons this might need to change):** fixed ingest vocabulary;
forced-choice softmax returns the *least-bad* label (relative, not absolute confidence — a
pet-snake clip confidently scored "feeding animals"); one label per segment (no fine temporal
or compositional structure); cannot name an action outside the configured vocabulary.

**↻ Revisit triggers — change the backend / add the query-time tier when:**
1. **Specific/rare-action queries matter** and X-CLIP confidently mislabels out-of-vocab footage
   (the cobra "feeding animals" pattern). → Build the **query-time Qwen escalation tier** (already
   the planned next step); this fixes the limitation, *not* swapping the contrastive model.
2. **Fine-grained / compositional actions** are needed ("counting", "parallel parking", "X then Y").
   → Query-time recognition, or two-stage **object-conditioned** action (run Role 7 cropped to a
   Role 5/6 track).
3. **X-CLIP gets *common* actions wrong** (it doesn't today). → Drop-in upgrade to **ViCLIP** —
   same contrastive shape and adapter contract, better-trained weights. Note it does **not** fix
   limitations 1–2 (still forced-choice closed-vocab); it only improves label accuracy.
4. **Bigger DGX-class HBM + stronger local VLMs** arrive. → Reconsider folding action recognition
   into a resident VLM entirely (open-vocab, can abstain) — see the Role 11 cloud↔local discussion.
5. **Calibrated *absolute* confidence** is required (not relative). → Move off contrastive softmax
   toward real per-class scores or a VLM judge.

**How to revisit safely:** Role 7 sits behind the `ActionRecognizer` Protocol + registry — a swap
is a new adapter + a one-line `config/roles.yaml` change. Validate any swap against the golden
action fixtures (`ferrari-act-*`, `bird-act-*`) **plus** whatever new ground-truth video exposed
the gap, per the "determinism ≠ correctness" rule in CLAUDE.md.

---

## Role 8: Speech-to-Text Model

**Architecture reference:** Ingestion Pipeline, Step 6 (audio transcription)

| Model | Source | Params | VRAM | Open Source | License | Local on DGX Spark | Local on 24GB GPU | NIM | SotA | Languages | Key Strength | Key Weakness |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Whisper large-v3** | OpenAI | 1.5B | ~6GB | Yes | MIT | Yes | Yes | No | Yes (open) | 100+ | Best open-source STT, word-level timestamps, multi-language | Slower than real-time on CPU |
| **Whisper medium** | OpenAI | 769M | ~3GB | Yes | MIT | Yes | Yes | No | No | 100+ | Half the size, still good quality | Noticeably worse than large-v3 |
| **NVIDIA Riva** | NVIDIA | Varies | ~2-4GB | No (via NIM) | NVIDIA license | Yes | Yes | Yes | Competitive | 20+ | GPU-optimized, low latency, NIM deploy | Closed source, NVIDIA-locked |
| **Deepgram** | Deepgram | Proprietary | N/A (cloud) | No | Proprietary | No | No | No | Yes (cloud) | 30+ | Fastest cloud STT, great accuracy | Cloud-only, costs per minute of audio |

**Recommendation:** Whisper large-v3 for local deployment. It's the clear default — open source, excellent quality, self-hostable.

---

## Role 9: Speaker Diarizer

**Architecture reference:** Ingestion Pipeline, Step 6 (speaker identification)

| Model | Source | Params | VRAM | Open Source | License | Local on DGX Spark | Local on 24GB GPU | NIM | SotA | Key Strength | Key Weakness |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **pyannote.audio 3.0** | CNRS/Herve Bredin | ~10M | ~1GB | Yes | MIT | Yes | Yes | No | Yes | Best open-source diarization, active development | Requires HuggingFace token for model download |
| **NeMo MSDD** | NVIDIA | ~20M | ~1GB | Yes | Apache 2.0 | Yes | Yes | Yes (via NeMo) | Competitive | Integrates with NVIDIA ecosystem, multi-scale | More complex setup than pyannote |

**Recommendation:** pyannote.audio 3.0. Simpler, better documented, state of the art.

**Decision (as built) — pyannote.audio — *Status: Accepted 2026-06-12.*** Implemented behind
the `diarize` extra (pinned `>=4`: 3.x crashes importing `torchaudio.AudioMetaData`, which
this box's torchaudio 2.11 removed). Role 9 annotates Role 8: it
emits speaker turns, joined onto transcript lines by temporal overlap to fill the existing
`transcripts.speaker` column (no new store). **Operational caveat (the main thing to know):**
the pyannote pipeline is a **gated** HF model — it needs a HF token with **four** separately-gated
models accepted (see CLAUDE.md), so the real backend isn't exercised by the offline suite (the
sidecar stub is). **Validated on real footage (2026-06-15):** SNL multi-speaker sketch, human
truth = **5 distinct speakers**. Auto diarization **under-counts to 4** (merges two brief voices);
a `num_speakers=5` hint recovers the balanced 5; `min_speakers=6/7` still yields 5 (the audio's
embeddings cap there — the hint is not a hard floor). Two takeaways baked into the design: the
adapter now accepts `num_speakers`/`min_speakers`/`max_speakers` hints, and **speaker count is a
model estimate to validate against ground truth, not trust blind** (same rule as deep-scan
counting). **↻ Revisit if:** the gated-model/token friction is a deployment blocker (→ **NeMo
Sortformer/MSDD**, Apache-2.0, no gate) — or if auto-mode under-counting hurts real queries (then
add a clustering-threshold knob or move to a model with stronger speaker-count estimation).

---

## Role 10: OCR Model

**Architecture reference:** Ingestion Pipeline, Step 7 (on-screen text reading)

| Model | Source | Params | VRAM | Open Source | License | Local on DGX Spark | Local on 24GB GPU | CPU OK? | SotA | Key Strength | Key Weakness |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **PaddleOCR** | Baidu | ~10-20M | <1GB | Yes | Apache 2.0 | Yes | Yes | Yes | Yes (open) | Best open-source OCR, multi-language, fast | Chinese-first documentation |
| **EasyOCR** | JaidedAI | ~10M | <1GB | Yes | Apache 2.0 | Yes | Yes | Yes | No | Simplest API, 80+ languages | Slower and less accurate than PaddleOCR |
| **TrOCR** | Microsoft | 60-350M | 1-3GB | Yes | MIT | Yes | Yes | GPU preferred | Competitive | Transformer-based, handles handwriting | Heavier than PaddleOCR for similar results |
| **Tesseract** | Google (maintained by community) | N/A (non-neural) | 0 (CPU) | Yes | Apache 2.0 | Yes | Yes | Yes | No | Zero dependencies, runs anywhere | Oldest, weakest accuracy, no GPU acceleration |
| **Google Vision OCR** | Google | Proprietary | N/A (cloud) | No | Proprietary | No | No | N/A | Yes (cloud) | Best accuracy overall | Cloud-only, pay per image |

**Recommendation:** PaddleOCR for local deployment. Best accuracy among open-source options, runs on CPU or GPU.

---

## Role 11: Reasoning LLM

**Architecture reference:** Query Pipeline, Tier 5 (complex analytical reasoning) + Query Planner

These models also appear under Role 4 (VLM Captioner). The distinction:
- **Role 4 = ingest time:** "Describe this video segment." Runs on every segment, batch processing. Needs to be cheap and fast.
- **Role 11 = query time:** "Given these 10 segments of evidence, did the squirrel eat any nuts? How many?" Runs on-demand per user query. Needs to be smart.

| Model | Source | Params | VRAM (FP16) | VRAM (INT4) | Open Source | License | Local on DGX Spark | Local on 24GB GPU | Vision | SotA | Key Strength | Key Weakness |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Claude Sonnet/Opus** | Anthropic | Proprietary | N/A (cloud) | N/A | No | Proprietary | No | No | Yes (multi-image) | Yes (reasoning) | Best reasoning quality, best at synthesizing evidence | Cloud-only, most expensive |
| **Gemini 2.0 Pro** | Google | Proprietary | N/A (cloud) | N/A | No | Proprietary | No | No | Yes (native video) | Yes (cloud) | Native video input (no frame extraction needed), strong | Cloud-only |
| **Qwen2.5-VL-72B** | Alibaba | 72B | ~144GB | ~40GB | Yes | Apache 2.0 | Yes (INT4) | No | Yes | Yes (open) | Best open-source reasoning VLM, self-hostable | Needs INT4 quantization on DGX Spark |
| **Llama 4 Scout** | Meta | 17B active (109B MoE) | ~60GB | ~30GB | Yes | Llama Community | Yes | No | Yes | Competitive | Open weights, 10M context window | MoE = more memory than parameter count suggests |
| **Llama 4 Maverick** | Meta | 17B active (400B MoE) | ~220GB | ~110GB | Yes | Llama Community | Maybe (INT4) | No | Yes | Competitive | Larger, stronger reasoning than Scout | Very large, may not fit DGX Spark |

**Design principle:** Use cloud APIs (Claude, Gemini) for Reasoning LLM in production. This is the only role where cloud dependency is acceptable because:
1. It only fires for complex queries (minority of queries)
2. Reasoning quality matters most here — cloud models are significantly better
3. Self-hosted fallback (Qwen2.5-VL-72B on DGX Spark) exists if you need to go fully local

**Recommendation:** Claude Sonnet for best quality. Qwen2.5-VL-72B (INT4 on DGX Spark) as self-hosted alternative.

---

## Models Spanning Multiple Roles

Some models can fill more than one role. This is important for deployment efficiency — one model serving two purposes means less GPU memory used.

| Model | Primary Role | Also Usable For | Trade-off |
|---|---|---|---|
| **DINOv2** | Role 2 (visual embedding — similarity only) | Backbone features for other models | No text encoder — **cannot** do text→image search alone. Must pair with SigLIP/CLIP. |
| **Qwen2.5-VL** | Role 4 (VLM captioner) | Role 11 (Reasoning LLM) | Same model, different context. At ingest: fast captioning. At query: deep reasoning. |
| **VILA / NVILA** | Role 4 (VLM captioner) | Role 11 (Reasoning LLM) | Same as above. NVILA's efficient frame handling makes it good for both. |
| **Llama 4 Vision** | Role 4 (VLM captioner) | Role 11 (Reasoning LLM) | Same as above. |
| **ImageBind** | Role 3 (cross-modal embedding — audio↔visual niche) | Role 2 (visual embedding — weaker) | Its text→image is weaker than SigLIP and its text→audio weaker than CLAP. Only justified when direct audio↔visual similarity is required. |
| **GroundingDINO** | Role 5 (object detector) | Can initialize Role 6 (tracker) | Its detections seed SAM 2 tracking. Two roles, sequential dependency. |
| **VLM Captioner (any)** | Role 4 | Can partially replace Role 10 (OCR) | Good VLMs read on-screen text as part of captioning. May not need a dedicated OCR model. |

---

## Deployment Profiles

### Profile A: DGX Spark (128GB Unified Memory, Grace Blackwell GB10)

The DGX Spark's 128GB unified memory (shared between CPU and GPU) can run most models. Here's what fits **concurrently**:

| Role | Model | Memory Footprint | Notes |
|---|---|---|---|
| 1. Scene Detector | TransNetV2 | <1GB | Tiny |
| 2. Visual Embedding | SigLIP SO400M | ~1.5GB | |
| 3. Cross-Modal Embed | LAION-CLAP | <1GB | Optional (ImageBind ~4.5GB only if audio↔visual needed) |
| 4. VLM Captioner | Qwen2.5-VL-72B (INT4) | ~40GB | Largest model |
| 5. Object Detector | YOLO-World | ~1GB | |
| 6. Object Tracker | SAM 2 | ~4GB | |
| 7. Action Recognizer | X-CLIP *(built)* | <1GB | Open-vocab; see Role 7 decision |
| 8. Speech-to-Text | Whisper large-v3 | ~6GB | |
| 9. Speaker Diarizer | pyannote.audio | ~1GB | |
| 10. OCR | PaddleOCR | <1GB | |
| **Total** | | **~64GB** | Fits with ~64GB headroom |

**Verdict:** All roles can run concurrently on DGX Spark. Even with Qwen2.5-VL-72B (INT4) as both captioner and reasoning model, you have ~64GB of headroom for the OS, video frames in memory, and index operations.

**If skipping ImageBind and using Qwen2.5-VL-7B instead of 72B:** Total drops to ~25GB, leaving massive headroom.

### Profile B: Consumer GPU (RTX 4090 / A6000, 24GB VRAM)

Must schedule models sequentially rather than running all at once:

| Role | Model | Memory | Fits? |
|---|---|---|---|
| 1. Scene Detector | TransNetV2 | <1GB | Yes |
| 2. Visual Embedding | SigLIP SO400M | ~1.5GB | Yes |
| 4. VLM Captioner | Qwen2.5-VL-7B | ~14GB | Yes (alone) |
| 5. Object Detector | YOLO-World | ~1GB | Yes |
| 6. Object Tracker | SAM 2 | ~4GB | Yes |
| 7. Action Recognizer | X-CLIP *(built)* | <1GB | Yes |
| 8. Speech-to-Text | Whisper large-v3 | ~6GB | Yes |
| 10. OCR | PaddleOCR | <1GB | Yes |
| 11. Reasoning LLM | Cloud API (Claude) | 0 (cloud) | N/A |

**Cannot fit:** Qwen2.5-VL-72B (40GB INT4 > 24GB). Must use 7B variant or cloud API for captioning/reasoning.

**Strategy:** Run models sequentially during ingestion. Unload one before loading the next. Or keep small models (YOLO, SigLIP, PaddleOCR) loaded and swap the large ones.

### Profile C: Cloud-Hybrid (Local Ingest + Cloud Reasoning)

**This is the recommended production approach.**

| Phase | Runs Where | Models |
|---|---|---|
| Ingest: all Roles 1-10 | **Local** (DGX Spark or GPU server) | All open-source models, fixed cost |
| Query: Tier 1 (vector search) | **Local** | SigLIP embedding + Milvus (no GPU needed at query time) |
| Query: Tier 3-4 (caption/object search) | **Local** | Database queries only, no GPU |
| Query: Tier 5 (complex reasoning) | **Cloud** | Claude Sonnet or Gemini Pro |

**Cost model:**
- Ingest: One-time GPU cost per video. ~5-15 min per hour of video on DGX Spark.
- Simple queries: Free (vector math + DB queries, local)
- Complex queries: ~$0.05-0.50 per query (Claude/Gemini API)

### Profile D: CPU-Only (No GPU)

| Role | Model | Works on CPU? | Practical? |
|---|---|---|---|
| 1. Scene Detector | PySceneDetect | Yes | Yes — fast enough |
| 2. Visual Embedding | CLIP ViT-B/32 | Yes (slow) | Barely — ~0.5fps vs 50fps on GPU |
| 4. VLM Captioner | Cloud API only | N/A | Must use cloud |
| 5. Object Detector | YOLO-World (ONNX) | Yes (slow) | Marginal — ~1fps |
| 8. Speech-to-Text | Whisper medium | Yes (slow) | Yes — ~0.3x real-time |
| 10. OCR | Tesseract / PaddleOCR | Yes | Yes |
| 11. Reasoning | Cloud API | N/A | Must use cloud |

**Verdict:** CPU-only is impractical for ingest (too slow). Use cloud APIs for everything or get a GPU.

---

## Model Selection Matrix (Summary)

| Role | DGX Spark (128GB) | Consumer GPU (24GB) | Cloud-Hybrid | Notes |
|---|---|---|---|---|
| **1. Scene Detector** | TransNetV2 | TransNetV2 | TransNetV2 | Same everywhere |
| **2. Visual Embedding** | SigLIP SO400M | SigLIP SO400M | SigLIP SO400M | Same everywhere |
| **3. Cross-Modal Embed** | LAION-CLAP | LAION-CLAP | Skip | Optional role — text→audio retrieval; ImageBind only for audio↔visual |
| **4. VLM Captioner** | Qwen2.5-VL-72B (INT4) | Qwen2.5-VL-7B | Gemini Flash | Size-dependent |
| **5. Object Detector** | YOLO-World | YOLO-World | YOLO-World | Same everywhere |
| **6. Object Tracker** | SAM 2 | SAM 2 | SAM 2 | Same everywhere |
| **7. Action Recognizer** | X-CLIP *(built; open-vocab)* | X-CLIP | X-CLIP | InternVideo2/ViCLIP for higher accuracy — see Role 7 decision + revisit triggers |
| **8. Speech-to-Text** | Whisper large-v3 | Whisper large-v3 | Whisper large-v3 | Same everywhere |
| **9. Speaker Diarizer** | pyannote.audio 3.0 | pyannote.audio 3.0 | pyannote.audio 3.0 | Same everywhere |
| **10. OCR** | PaddleOCR | PaddleOCR | PaddleOCR | Same everywhere |
| **11. Reasoning LLM** | Qwen2.5-VL-72B (INT4) | Claude Sonnet (cloud) | Claude Sonnet (cloud) | Only role needing cloud on smaller hardware |

---

## References

### By Source

**Meta FAIR:** SAM 2, ImageBind, DINOv2, VideoMAE v2, CoTracker, Llama 4, Detectron2, TimeSformer
**NVIDIA:** NV-CLIP, VILA/NVILA, Riva, NV-DINOv2 (all via NIM)
**Alibaba:** Qwen2.5-VL (7B, 72B)
**Shanghai AI Lab:** InternVL2.5, InternVideo2
**Google:** SigLIP, Gemini, Tesseract
**OpenAI:** CLIP, Whisper
**Anthropic:** Claude Sonnet/Opus
**Community/Academic:** TransNetV2, PySceneDetect, ByteTrack, BoT-SORT, DeepSORT, pyannote.audio, PaddleOCR, EasyOCR, GroundingDINO, YOLO-World

### GitHub Repos

- SAM 2: `github.com/facebookresearch/sam2`
- ImageBind: `github.com/facebookresearch/ImageBind`
- LAION-CLAP: `github.com/LAION-AI/CLAP`
- BEATs: `github.com/microsoft/unilm/tree/master/beats`
- AST: `github.com/YuanGongND/ast`
- PANNs: `github.com/qiuqiangkong/audioset_tagging_cnn`
- DINOv2: `github.com/facebookresearch/dinov2`
- VideoMAE v2: `github.com/facebookresearch/VideoMAE`
- CoTracker: `github.com/facebookresearch/co-tracker`
- Llama 4: `github.com/meta-llama`
- SigLIP: `github.com/google-research/big_vision`
- CLIP: `github.com/openai/CLIP`
- Whisper: `github.com/openai/whisper`
- GroundingDINO: `github.com/IDEA-Research/GroundingDINO`
- YOLO-World: `github.com/AILab-CVC/YOLO-World`
- SAM 2: `github.com/facebookresearch/sam2`
- TransNetV2: `github.com/soCzech/TransNetV2`
- pyannote.audio: `github.com/pyannote/pyannote-audio`
- PaddleOCR: `github.com/PaddlePaddle/PaddleOCR`
- Qwen2.5-VL: `github.com/QwenLM/Qwen2.5-VL`
- InternVL: `github.com/OpenGVLab/InternVL`
- InternVideo2: `github.com/OpenGVLab/InternVideo`
