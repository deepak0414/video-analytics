# Video Analytics Solution: "Ctrl-F for Video"

*Created: 2026-06-02 | Last Updated: 2026-06-12 | Status: Design Phase*
*Model-agnostic architecture — see [video-analytics-model-analysis.md](video-analytics-model-analysis.md) for specific model choices*

---

## Table of Contents

- [Problem Statement](#problem-statement)
- [Scope Decisions](#scope-decisions)
- [Model Roles](#model-roles)
- [Existing Solutions & Reference Architectures](#existing-solutions--reference-architectures)
- [Core Technical Challenges](#core-technical-challenges)
- [Architecture: Hybrid Multi-Tier Pipeline](#architecture-hybrid-multi-tier-pipeline)
- [Ingestion Pipeline](#ingestion-pipeline)
- [Query Pipeline — Progressive Escalation](#query-pipeline--progressive-escalation)
- [Data Model](#data-model)
- [Metadata Storage Overhead](#metadata-storage-overhead)
- [Trade-offs and Decision Points](#trade-offs-and-decision-points)
- [MVP Scope](#mvp-scope)
- [Future Extensions](#future-extensions)

---

## Problem Statement

Build a system that can:
1. Ingest **recorded video files** (with or without audio)
2. Enable **natural language search** across video content ("Ctrl-F for video")
3. **Navigate to specific moments** — highlight and jump to relevant sections
4. Answer **complex analytical queries** — e.g., "Was there a squirrel in the video and how many nuts did it eat?"

The hardest requirement is #4 — it demands not just retrieval but **temporal reasoning** across the video.

---

## Scope Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Live streaming** | Out of scope | Any video (including from live sources) is assumed to be in storage before processing. Removes real-time pressure, growing indexes, and alerting. |
| **Hardware target** | Local-first (DGX Spark or GPU server) | Minimize cloud dependency and ongoing API costs. |
| **Cloud dependency** | Reasoning LLM only (Role 11) | Roles 1-10 run locally. Cloud APIs (Claude, Gemini) used only for complex analytical queries where reasoning quality matters most. |
| **Query approach** | Progressive escalation | Every query starts with instant results (Tier 1), planner decides if deeper analysis is needed, results refine progressively. |

---

## Model Roles

The pipeline requires 11 distinct model roles. Each role is a function the system needs — you pick one model per role. Models are interchangeable within a role as long as they meet the input/output contract.

For specific model recommendations per role, see **[video-analytics-model-analysis.md](video-analytics-model-analysis.md)**.

### Role 1: Scene Boundary Detector

> *In plain terms:* When you watch a movie, you notice when it "cuts" from one scene to another — the camera switches from a kitchen to a street, or from a close-up to a wide shot. A Scene Boundary Detector does the same thing automatically. It watches the video and marks where each scene starts and ends, splitting a 1-hour video into, say, 200 meaningful chunks.

*Why it matters:* Everything else in the pipeline works on these chunks. Captioning, object detection, and search all operate at the segment level. Bad scene boundaries mean bad results downstream.

| | |
|---|---|
| **Input** | Raw video file |
| **Output** | List of `(start_time, end_time)` for each segment |
| **Runs at** | Ingest time |
| **Required** | Yes |

---

### Role 2: Visual Embedding Model

> *In plain terms:* This model looks at a video frame (a single image from the video) and converts it into a list of numbers (a "vector") that captures what the image contains. It does the same for text — converts "red sports car" into a similar list of numbers. If the image and the text describe the same thing, their number lists will be mathematically close together. This is what powers the "Ctrl-F" search — you type words, the model converts them to numbers, and we find which stored frame vectors are closest.

*Why it matters:* This is the foundation of fast visual search. Without it, you'd need to run an AI model on every frame for every query (slow, expensive). With it, searching is just comparing pre-computed numbers — instant.

| | |
|---|---|
| **Input** | An image (video frame) OR a text query |
| **Output** | A vector (list of ~768 numbers) representing the content |
| **Runs at** | Ingest time (embed frames) + Query time (embed query text) |
| **Required** | Yes |

---

### Role 3: Cross-Modal Embedding Model

> *In plain terms:* Similar to the Visual Embedding Model, but it understands multiple types of media — images, audio, and text — and puts them all in the same "number space." This means you can search for a sound and find the matching video, or search with text and find matching audio moments. Imagine typing "glass breaking" and finding both the video frame where a glass falls AND the audio moment of the crash.

*Why it matters:* Enables searches across different media types. Most embedding models only understand image+text. This adds audio to the mix.

| | |
|---|---|
| **Input** | An image, audio clip, OR text query |
| **Output** | A vector in a shared space (all modalities comparable) |
| **Runs at** | Ingest time (embed frames + audio) + Query time (embed query) |
| **Required** | Optional — skip if audio↔visual cross-search isn't needed |

**Implementation note — two valid shapes for this role:**
1. **Single shared space** (ImageBind): audio and frame vectors are directly comparable — audio↔visual similarity is one cosine distance. Costly license (non-commercial) and weaker per-modality quality.
2. **Separate text→audio space** (CLAP alongside Role 2's SigLIP): audio gets its own vector namespace; the system correlates audio hits with frame hits via a **temporal join** on `video_id` + `timestamp` — the same fusion already used to merge transcript and visual results. Covers the dominant use case (text query → sound moments) with a lighter, license-clean model. The only query it cannot serve is direct query-*by*-audio-clip↔visual matching, which requires shape 1.

See [Role 3 in the model analysis](video-analytics-model-analysis.md#role-3-cross-modal-embedding-model) for model options under each shape.

---

### Role 4: Vision Language Model (VLM) — Captioner

> *In plain terms:* This model looks at one or more images from a video segment and writes a detailed text description of what's happening — like a narrator describing a scene to someone who can't see it. "A grey squirrel sits on an oak branch, holding an acorn in its front paws. The background shows a park with fallen leaves."

*Why it matters:* Captions make visual content text-searchable with semantic understanding. The embedding model (Role 2) catches visual similarity, but captions capture meaning, context, and relationships. Searching captions finds things that visual similarity alone might miss.

| | |
|---|---|
| **Input** | 1-10 keyframe images from a segment + optional prompt |
| **Output** | Detailed text description of the segment |
| **Runs at** | Ingest time (batch captioning of all segments) |
| **Required** | Yes |

**Note:** The same model type can serve Role 11 (Reasoning LLM) at query time. See [Models Spanning Multiple Roles](video-analytics-model-analysis.md#models-spanning-multiple-roles) in the model analysis doc.

---

### Role 5: Object Detector

> *In plain terms:* This model looks at a single frame and draws boxes around every object it recognizes, labeling each one — "car", "person", "squirrel", "tree". It tells you *what* is in the frame and *where* it is (the bounding box coordinates). The best detectors are "open vocabulary" — you can describe what to look for in text, and they'll find it, even if they've never seen that specific object before.

*Why it matters:* Enables structured queries like "how many cars appear in this video" and provides the seed detections that the Object Tracker (Role 6) connects across frames.

| | |
|---|---|
| **Input** | A single video frame (optionally + text describing what to find) |
| **Output** | List of `(object_label, bounding_box, confidence_score)` |
| **Runs at** | Ingest time (sampled frames) + Query time (on-demand for specific objects) |
| **Required** | Yes |

---

### Role 6: Object Tracker / Segmenter

> *In plain terms:* If the Object Detector (Role 5) tells you "there's a squirrel in frame 100", the Object Tracker follows that *same* squirrel through the video — frame 101, 102, 103... It answers "is this the same squirrel or a different one?" and "where did it go?" The best trackers don't just draw boxes — they create pixel-level masks (exact outlines) of the object as it moves, deforms, and goes behind things.

*Why it matters:* Without tracking, you can only say "squirrels appeared in 47 frames." With tracking, you can say "there were 2 distinct squirrels — squirrel A appeared from 1:00-3:00, squirrel B from 5:00-7:00." This distinction is critical for counting, re-identification, and analytical queries.

| | |
|---|---|
| **Input** | Video frames + initial detection (box or point on the object) |
| **Output** | Per-frame masks or boxes with consistent track ID |
| **Runs at** | Ingest time (prominent objects) + Query time (on-demand tracking) |
| **Required** | Yes |

---

### Role 7: Action / Event Recognizer

> *In plain terms:* While the Object Detector (Role 5) sees *things* in a single frame, the Action Recognizer understands *what's happening* over time. It watches a sequence of frames and identifies actions: "eating", "running", "throwing", "falling". These are impossible to detect from a single image — you need to see the motion unfold over multiple frames. Think of it this way: a single photo of someone holding a ball could be "catching" or "throwing" — you need the video to tell which.

*Why it matters:* Critical for queries like "how many nuts did the squirrel eat." Role 5 finds the squirrel. Role 7 identifies the "eating" action. Together they answer the question.

| | |
|---|---|
| **Input** | The media file + the Role-1 segment spans (the backend owns frame sampling) |
| **Output** | Per segment, the recognized `(action_label, confidence_score)` — or nothing if no label clears the confidence floor |
| **Runs at** | Ingest time (per Role-1 segment — the shot is an action's natural unit) |
| **Required** | Recommended — needed for action-based queries |

**As implemented (2026-06-12):** **X-CLIP** (video-text contrastive) zero-shot via
transformers, open-vocabulary like Role 5 — the action phrases to score live in config
(`DEFAULT_INGEST_ACTIONS`, overridable per deployment). plan.md named InternVideo2 (custom
non-transformers stack, skipped after the paddle-on-aarch64 runtime lesson); VideoMAE, the
listed alt, is closed-vocab Kinetics. **Key property to design around:** the score is a
softmax over the *requested* phrases, so the model always returns the least-bad label — it
answers "is one of these actions happening" (Ferrari clip → "driving a car" at 0.94–0.99
across all segments) but cannot name an action outside the configured vocabulary ("counting
dresses" came back "dancing"). Two guards make the score usable: a confidence floor, and an
**abstention foil** ("no particular action") always added to the candidate set so the softmax
can park probability on "none of these" instead of forcing a wrong label — when it wins, no
event is stored (measured: confident-correct labels untouched, borderline labels trimmed
29 → 23 on the dress montage). Recognizing an arbitrary *queried* action still needs
**query-time** recognition with the query as the vocabulary — the action analogue of Role 5's
"always-on for common, on-demand for specific" split. Not built yet; the ingest-time
always-on pass is. → The full decision (alternatives weighed, validation, and **"revisit
when…" triggers**) is recorded in the model-analysis doc's [Role 7 decision
record](video-analytics-model-analysis.md#decision-as-built--x-clip--status-accepted-2026-06-12).

> *In plain terms:* Listens to the audio track of the video and types out everything that's said, with timestamps. Like auto-generated subtitles, but stored as searchable text. The best models also provide word-level timestamps, so you can pinpoint exactly when a specific word was spoken.

*Why it matters:* Enables searching by what was *said* in the video, not just what was *shown*. "When did they mention the budget?" is a transcript search.

| | |
|---|---|
| **Input** | Audio track from video |
| **Output** | Timestamped transcript: `[(start, end, "spoken text"), ...]` |
| **Runs at** | Ingest time |
| **Required** | Yes (if video has audio) |

---

### Role 9: Speaker Diarizer

> *In plain terms:* Figures out *who* is speaking at each moment. It doesn't know people's names (unless given reference audio), but it can tell "Speaker A talked from 0:00-0:30, then Speaker B from 0:30-1:00, then Speaker A again." It's like labeling a transcript with "Person 1:", "Person 2:" before each line.

*Why it matters:* Makes transcripts useful for multi-person conversations. "What did the second speaker say about the budget?" requires knowing which words belong to which speaker.

| | |
|---|---|
| **Input** | The media file (the backend extracts audio, like Role 8) |
| **Output** | Speaker turns `[(start, end, SPEAKER_xx), ...]` — joined onto Role-8 lines |
| **Runs at** | Ingest time (after speech-to-text) |
| **Required** | Optional — useful for multi-speaker video |

**As implemented (2026-06-12):** **pyannote.audio** (the model-analysis recommendation),
behind the `diarize` extra. Role 9 *annotates* Role 8 rather than adding a store: it produces
speaker turns, and the pipeline (`assign_speakers`) joins each transcript line to the turn it
most overlaps in time, filling the `transcripts.speaker` column the schema already reserves —
a temporal join, the same `video_id`+time correlation used everywhere. Search can then filter
by speaker ("what did SPEAKER_01 say"). **Operational caveat:** pyannote's pipeline is a
**gated** HuggingFace model — it needs an `HF_TOKEN` with the model terms accepted, so it's
not exercised by the offline test suite (the deterministic **sidecar** stub is). Diarization
is best-effort: no token/model just leaves `speaker` NULL; the transcript is unaffected.

---

### Role 10: OCR Model

> *In plain terms:* Reads text that appears visually in the video — signs, labels, title cards, subtitles burned into the video, text on whiteboards, presentation slides, product labels. It's reading what's *on screen*, not what's being *said* (that's Role 8).

*Why it matters:* Some important information exists only as on-screen text. A presentation video's content is in the slides, not the narration.

| | |
|---|---|
| **Input** | The media file (the backend owns frame sampling, like Role 8 owns audio) |
| **Output** | List of `(text_string, timestamp, bounding_box_on_screen)` — one row per *appearance* of a text string, not per frame |
| **Runs at** | Ingest time, sampled at ~1 fps (denser than keyframes: text often appears mid-shot, e.g. a billboard drifting into view during a pan; consecutive sightings of the same text collapse into one row, so storage stays near keyframe-level) |
| **Required** | Optional — important for videos with on-screen text |

**Note:** Good VLM Captioners (Role 4) often read on-screen text as part of their description, potentially making a dedicated OCR model unnecessary for basic cases.

**As implemented (2026-06-12):** PP-OCR det+rec models running as ONNX on onnxruntime via
RapidOCR, on CPU (~11s per minute of video; no GPU contention with the VLM). PaddleOCR's
own paddlepaddle runtime segfaults on aarch64 — same models, different runtime. The
language-specific rec model matters for *retrieval*, not just accuracy: the CH default
reads "bought a cobra" as "boughit acobra" (right characters, wrong spacing), which breaks
word-level search. Search over `ocr_results` is word-overlap plus a space-insensitive
phrase match, because OCR routinely merges words it read correctly ("Coors Light" →
"COOrSLIGHT", measured).

---

### Role 11: Reasoning LLM

> *In plain terms:* This is the "brain" that thinks about complex questions. It doesn't watch video directly — instead, it receives the *evidence* gathered by all the other models (captions, object tracks, transcripts, keyframe images) and reasons over it to produce an answer. It's like giving a smart analyst a dossier and asking them a question. "Given these 10 segments where squirrels appear, these 3 segments where eating was detected, and these keyframes — how many nuts did the squirrel eat?"

*Why it matters:* No single model can answer complex analytical queries. The Reasoning LLM synthesizes evidence from all tiers into a coherent, cited answer. It also serves as the Query Planner — analyzing the user's query to decide which tiers need to run.

| | |
|---|---|
| **Input** | Text evidence (captions, transcripts, object tracks) + optionally keyframe images |
| **Output** | Natural language answer with cited timestamps |
| **Runs at** | Query time only (on-demand) |
| **Required** | Yes (for complex queries and query planning) |

**Design principle:** This is the **only role where cloud API dependency is acceptable** (Claude, Gemini). It fires only for complex queries (minority of total queries), and cloud models provide significantly better reasoning than self-hosted alternatives. A self-hosted fallback exists for fully local deployments.

---

### Role Summary

```
LOCAL (Roles 1-10):                    CLOUD-OPTIONAL (Role 11):
  Runs at ingest time, once per video    Runs at query time, per complex query
  Fixed cost (GPU hardware)              Pay-per-query (API cost)
  All open-source models available       Cloud = best quality, local = good fallback
```

---

## Existing Solutions & Reference Architectures

### NVIDIA AI Blueprint: Video Search and Summarization (VSS)

The closest existing reference implementation — same problem statement as us (NL search,
summarization, Q&A over video). **A full role-by-role comparison now lives in its own doc:
[video-analytics-nvidia-comparison.md](video-analytics-nvidia-comparison.md) — read that for the
current analysis.** The summary below is kept short and pointed there.

**⚠ This table was rewritten 2026-06-15.** The earlier version claimed VSS had *no* object
detection, *no* audio, *no* OCR, and only fixed-interval chunking, concluding "VSS covers ~40%
of our architecture." That was roughly true of the **2024 VSS release but is now wrong.** As of
**VSS 3.1** (March 2026) the feature-coverage gap has largely closed — VSS added ASR (Parakeet),
a CV detection/tracking layer (RT-DETR + Grounding DINO + nvtracker, plus 3D multi-camera
Sparse4D), GraphRAG (Neo4j), live-stream alerts, and an MCP-based agent layer. **Our real
differentiation is now architectural and licensing posture, not feature count.**

| Component | VSS 3.1 (default NIM) | Ours | Note |
|---|---|---|---|
| Scene detection | Duration chunks + sliding-window overlap | Content-aware (PySceneDetect) | We're sharper at shot boundaries |
| Visual embedding | NV-CLIP | SigLIP SO400M | Equivalent (both image-text) |
| Text embedding (caption/transcript RAG) | llama-3.2-nv-embedqa-1b-v2 | **none — word-overlap SQL** | **VSS ahead; our biggest model gap** |
| Reranker | llama-3.2-nv-rerankqa-1b-v2 | none | VSS ahead |
| VLM captioning | Cosmos-Reason2-8B | Qwen2.5-VL-7B | Comparable |
| Object detection | RT-DETR + (Mask-)Grounding-DINO | YOLO-World | VSS ahead (real-time, TensorRT, open-vocab) |
| Object tracking | Gst-nvtracker (NvDCF/DeepSORT), multi-cam | ByteTrack / iou stub | VSS far ahead (re-ID, multi-camera) |
| Action recognition | (folded into the VLM) | X-CLIP zero-shot per segment | We keep a discrete, thresholdable signal |
| ASR | Parakeet-CTC-XL-0.6B (Riva NIM) | Whisper | Comparable; Riva lower-latency, Whisper multilingual |
| Diarization | optional (NeMo) | pyannote.audio 4.x | We have it wired |
| OCR | (VLM reads text) | RapidOCR (word-searchable index) | We keep a dedicated index |
| Reasoning LLM | Nemotron-Nano-9B-v2 / Llama-3.1-70B (CA-RAG) | Claude / Qwen2.5-VL | We allow a cloud-frontier option |
| Retrieval | Vector RAG (Milvus) + GraphRAG (Neo4j) + reranker | Vector (SigLIP) + time-fusion; no graph/reranker | VSS ahead |
| Counting | detection/tracking + GraphRAG aggregation | **deep-scan: describe→normalize→code-count, ground-truth-validated** | Our novel piece, no VSS analog |
| Deployment | NIM microservices, Docker/K8s, streaming + alerts | in-process Python, batch | VSS productized; ours a PoC |

**Verdict:** VSS is a far more capable *product*; ours is a more *portable architecture*
(vendor-neutral role/adapter/registry spine, cloud-frontier reasoning option, validated
deep-scan counting, offline-testable core). See the comparison doc for the full benefits/gaps
analysis and what to borrow.

### Other Platforms

| Platform | Strength | Limitation |
|---|---|---|
| **Twelve Labs** | Best commercial video search API | Cloud-only, limited reasoning, no self-hosting |
| **Google Gemini** | Can process 1-2hr video natively, strong reasoning | No persistent index — re-processes video per query |
| **Azure Video Indexer** | Good structured extraction (faces, brands, emotions) | No free-form natural language queries |
| **Amazon Rekognition** | Solid object/face detection | Label-based, not semantic |

None of these provide the full pipeline we need. They either lack complex reasoning, lack self-hosting, or lack persistent indexing.

---

## Core Technical Challenges

### 1. The Granularity Problem

At what level do you analyze the video?

| Granularity | Pros | Cons |
|---|---|---|
| Every frame (30fps) | Never miss anything | 1 hour = 108,000 frames. Massive cost. |
| Sampled frames (1-5fps) | Manageable volume | May miss brief events |
| Scene-level | Semantically coherent chunks | Scenes can be long |

**Answer: Multi-resolution.** Cheap models (Role 2 embedding) at high frequency. Expensive models (Role 4 VLM) at scene level.

### 2. The Temporal Reasoning Problem

"How many nuts did the squirrel eat?" requires:
- Detecting the squirrel across multiple scenes (Role 5 + 6)
- Recognizing the "eating" action over time (Role 7)
- Counting distinct events, not double-counting (Role 11 reasoning)

This is a **reasoning problem**, not a retrieval problem. No embedding search can answer it alone.

### 3. The Indexing vs. Query-Time Compute Trade-off

| Approach | Ingest Cost | Query Cost | Complex Query Support |
|---|---|---|---|
| Heavy indexing (extract everything upfront) | Expensive | Fast | Limited to what was extracted |
| Light indexing + heavy query | Cheap | Expensive per query | Unlimited |
| **Hybrid (our approach)** | Moderate | Moderate | Good for simple, deep for complex |

---

## Architecture: Hybrid Multi-Tier Pipeline

### Tiers Explained

| Tier | What It Does | Cost | Latency | When It Runs |
|---|---|---|---|---|
| **Tier 1** | Vector search on pre-computed frame embeddings | Cheapest | <100ms | Every query |
| **Tier 2** | Scene structure and segment boundaries | Free (pre-computed) | 0ms | Pre-computed at ingest |
| **Tier 3** | Full-text search on captions + transcripts | Cheap | <500ms | When planner requests |
| **Tier 4** | SQL on object tracks + action events | Cheap | <500ms | When planner requests |
| **Tier 5** | VLM/LLM reasoning over retrieved evidence | Expensive | 5-30s | Complex queries only |

### Ingestion Pipeline

```
Video File ---+-- [Role 1: Scene Boundary Detector] --> Segment Boundaries
              |
              +-- [Role 2: Visual Embedding Model] ---> Vector DB
              |   (1-3fps frame sampling)                (frame embeddings)
              |
              +-- [Role 3: Cross-Modal Embedding] ----> Vector DB
              |   (optional, audio embedding)            (audio embeddings)
              |
              +-- [Role 4: VLM Captioner] ------------> Full-Text Index
              |   (keyframes per segment)                (captions)
              |
              +-- [Role 5: Object Detector] ----------> Structured DB
              |   (1fps, open-vocabulary)                (detections)
              |   + [Role 6: Object Tracker]             (object tracks)
              |
              +-- [Role 7: Action Recognizer] ---------> Structured DB
              |   (per segment)                          (event timeline)
              |
              +-- [Role 8: Speech-to-Text] -----------> Full-Text Index
              |   + [Role 9: Speaker Diarizer]           (transcripts)
              |
              +-- [Role 10: OCR] ---------------------> Full-Text Index
              |   (keyframes with visible text)          (on-screen text)
              |
              All outputs tagged with: video_id, timestamp, segment_id
```

### Unified Index Layer

```
+----------------+  +------------------+  +--------------------+
|  Vector DB     |  |  Full-Text Index |  |  Structured DB     |
|  (Milvus /     |  |  (Elasticsearch /|  |  (PostgreSQL)      |
|   Qdrant)      |  |   Typesense)     |  |                    |
|                |  |                  |  |  - Object tracks   |
|  - Frame       |  |  - Captions      |  |  - Action events   |
|    embeddings  |  |  - Transcripts   |  |  - Scene metadata  |
|  - Audio       |  |  - OCR text      |  |  - Speaker segments|
|    embeddings  |  |                  |  |                    |
+----------------+  +------------------+  +--------------------+
```

### Retrieval Layer — semantic search, cross-modal fusion, reranking

> *In plain terms:* the three stores above are *where the data lives*; this layer is *how a
> query finds the right moments across all of them*. It turns a pile of per-role extractions
> (captions, transcript lines, OCR strings, action labels, detections, frame embeddings) into
> **one short, relevance-ranked list of evidence** handed to the reasoner. It is the "retrieval
> brain" that sits between extraction and reasoning. (NVIDIA's VSS calls its equivalent
> **CA-RAG**; ours is vendor-neutral and runs the same locally or against a remote NIM.)

**Why a first-time reader should care — five concrete wins:**

1. **Find by *meaning*, not keywords.** A query for "the budget" should surface a line that says
   "our fiscal spending" — different words, same meaning. Semantic text retrieval matches meaning
   across everything *said* (Role 8), *shown on screen* (Role 10), *described* (Role 4), and
   *labeled* (Role 7). (Today those searches are literal word-overlap, so they miss paraphrases.)
2. **One ranked answer across all modalities.** A moment can match because of what's *visible*,
   *spoken*, *on-screen*, *described*, or *detected*. Today each modality is searched separately
   and the hits are interleaved blindly; this layer **fuses them into a single relevance-ordered
   list** so the best moment wins regardless of which modality found it.
3. **Reranking = quality.** A cross-encoder reads the query and each candidate *together* and
   scores true relevance — the single highest-leverage retrieval-quality win, and what separates
   "keyword hits" from "actually answers the question."
4. **Honest "no match."** A relevance threshold lets the system return *nothing* when nothing
   fits, instead of always serving top-k regardless of score (a known gap today).
5. **It earns the reasoner its keep.** Role 11 reasons over whatever evidence it's handed; if that
   evidence is a blunt keyword interleave, even a frontier model answers from noise. Better
   retrieval is the cheapest way to make every downstream answer better.

**What it is — three pieces, all behind the hosting-agnostic adapter seam:**

- **Text embedder** *(new, model-backed)* — embeds caption / transcript / OCR / action text into
  a semantic *text-text* space (distinct from Role 2's *image-text* SigLIP space). At ingest,
  each text row is embedded and stored in a text vector index keyed by `(video_id, modality,
  time, source_role)`. Backends: stub (offline tests) · local sentence-transformer (BGE-M3 / E5,
  vendor-neutral) · remote NIM (NV-embedqa). *Stub default keeps the suite offline.*
- **Reranker** *(new, model-backed)* — `rerank(query, candidates) → relevance-scored candidates`,
  a cross-encoder. Backends: stub · local (BGE-reranker) · remote NIM (NV-rerankqa).
- **Retriever** *(orchestrator, code not model)* — the CA-RAG-equivalent flow:
  **gather** candidates from every enabled modality (visual vector search + semantic text search +
  structured object/track queries) → **normalize** their heterogeneous scores into one comparable
  space → **rerank** → **threshold** → emit a ranked `Evidence` bundle (reusing the existing
  `Evidence`/`EvidenceItem` contracts).

```
 per-role stores / index ──► RETRIEVER ──► ranked Evidence ──► Role 11 (reason)
 (visual vecs, text rows,    gather → normalize → rerank → threshold
  object/track tables)
```

**As built today vs the target.** Today text search is per-modality **word-overlap** and evidence
is interleaved **round-robin by time** — honest, but blunt and keyword-bound. This layer is the
upgrade path: semantic text retrieval + cross-modal fusion + reranking + a relevance threshold.
Because the embedder and reranker are model-backed roles behind the registry seam, they also
**double as the proof of the remote-adapter bet** — point either at a NIM and the rest of the
system is unchanged.

### Query Pipeline — Progressive Escalation

**Design decision:** Every query starts with instant Tier 1 results. A parallel query planner decides if deeper analysis is needed. Results stream back and refine progressively.

```
User Query
    |
    +---> [IMMEDIATE] Tier 1: Vector search (<100ms)
    |     [Role 2] embeds query text -> nearest-neighbor in Vector DB
    |     -> Show approximate visual matches instantly
    |
    +---> [PARALLEL] Query Planner (~200-500ms)
          [Role 11] classifies query intent, outputs execution plan:
          {
            needs_transcript_search: true/false,
            needs_caption_search: true/false,
            needs_object_query: true/false,
            needs_action_query: true/false,
            needs_vlm_reasoning: true/false
          }

          Additional tiers execute based on plan:
          +-- Tier 3: Full-text search on captions + transcripts
          +-- Tier 4: SQL on object tracks + action events
          +-- Tier 5: [Role 11] reasoning over retrieved evidence
                      + [Role 4] VLM verification of keyframes

          Results stream back, refining initial Tier 1 results
```

### Query Flow by Complexity

| Query | Tiers | Total Latency | Example |
|---|---|---|---|
| Visual search | 1 only | <100ms | "red sports car" |
| Transcript search | 1 + 3 | <500ms | "when did they mention inflation" |
| Caption/scene search | 1 + 3 | <500ms | "find the kitchen scene" |
| Object counting | 1 + 4 | <500ms | "how many dogs appear" |
| Complex analytical | 1 + 3 + 4 + 5 | 5-15s | "how many nuts did the squirrel eat" |
| State-change counting | 1 + 5 + **deep-scan** | 1-5min first run, cached after | "how many times does she change her dress" |

### Deep-Scan Escalation (Tier 5b) — query-time exhaustive analysis

*Added 2026-06-10 after an observed failure on real footage.*

**The failure that motivated it:** on the 27 Dresses clip, "how many times does she change
her dress?" (ground truth: double digits) returned unstable, wrong answers. Root causes:
(1) sparse retrieval hands the reasoner only a few keyframes — the answer requires seeing
*every* outfit; (2) the scene detector merged the entire dress-montage into ONE segment
(same room dominates the color histogram; only the dress changes), so per-segment captions
collapsed 10+ outfits into one description; (3) **no role in the 11-role list extracts
"appearance-state-change" events** — more roles would not have fixed this; Role 7 would only
have localized "trying on clothes" activity, not counted changes.

**Three escalation triggers** (defense in depth): (1) the LLM planner flags
`needs_deep_scan` from semantics — the primary, general mechanism; (2) a small CLOSED
regex floor catches common counting phrasings on weak-planner/offline paths; (3)
**self-escalation** — if no deep scan ran and the sparse answer self-reports
insufficiency (or returns uncited and empty), the ask escalates ONCE and re-reasons over
the dense evidence. A missed trigger therefore degrades to a *slower right answer*, never
a wrong one. (Exemplar-embedding classification was spiked as a candidate layer and
rejected: off-the-shelf encoders cannot separate events-over-time from distinct-instance
counting — see plan.md.)

**The pattern** (as implemented + iterated 2026-06-11): when the deep scan triggers
(any of the above):

1. **Scope** — target video + time range from evidence; frame budget capped; the scan
   target (what to watch per frame) is DERIVED from the user's query, never canned.
2. **Sweep** — **shot-aligned sampling: one frame per Role-1 segment midpoint** (a fixed
   stride skips sub-second montage shots — measured: a 1.3s stride missed entire dresses).
   The VLM gets a constrained micro-prompt per frame ("2-5 words, color AND one
   distinguishing detail; 'none' if not visible" — color-only labels merge distinct
   same-color items) → timestamped raw labels.
3. **Normalize (one LLM call, cached + auditable)** — map raw labels to canonical states:
   synonyms merge ("olive strapless" = "green strapless"), off-subject labels (cut-aways
   to another person) → OTHER, dropped. The normalizer receives the **run timeline**, not
   just the label list: live subjects get relabeled per viewing angle ("brown speckled" /
   "brown striped" = one sparrow's side vs back), and only the alternation *pattern*
   reveals that. Semantics belongs to the LLM; arithmetic does not.
3b. **Debounce (code)** — a single-sample run sandwiched between two runs of the same
   state is measurement flicker, not two transitions (live subjects flap labels
   per-sample in a way static subjects never do; measured: 23 phantom bird visits → 5
   real ones). Multi-sample interruptions survive — those are genuine (montage intercuts).
4. **Count in code** — transitions + distinct states over the canonical timeline,
   deterministic. **Report BOTH: for montage-edited footage, *distinct states* answer
   "how many changes"; raw *transitions* measure the editor's cutting, not the subject**
   (measured: 34 transitions vs 11 distinct dresses on the same timeline).
5. **Cache** — observations AND the normalization mapping persist in the DB; repeat
   queries are fast and byte-identical.

**Iteration history worth remembering** (each count was perfectly stable; the first three
were wrong): free-text sweep counted *descriptions* (94-99); constrained vocabulary
counted *camera cuts* (70-71 ≈ the clip's 71 shots); color-only canonical labels
*undercounted* (11 — distinct same-color dresses merged, sub-stride shots missed);
shot-aligned sampling + style-bearing labels + normalize + code-count produced **18
distinct = 17 changes — exactly the human-verified ground truth**. Determinism must be
paired with a validated counting semantic, or it is just reproducible error — and the
validation loop against a known answer is what found all three wrong semantics.

**Design lessons recorded:**
- The role list is not a completeness guarantee: some query classes are bounded by
  *segmentation granularity* and *query-time compute*, not by which extractors exist.
- Scene detection has a "reverse coalescing" trap: content-change *within* a static
  setting (outfit swap, object swap) defeats histogram-based detectors; shot-boundary
  models (TransNetV2/PySceneDetect) or embedding-delta splitting are required for
  montage-style content.
- Answer instability across runs is a signal of insufficient evidence — surfacing it
  (or cross-backend disagreement) is a cheap self-verification trigger for escalation.

### Example: Progressive Refinement in Action

```
Time 0ms:      User types "squirrel eating nuts" + hits Enter

Time 100ms:    Tier 1 results appear
               8 frames with squirrels (some may not be eating)
               Label: "Quick matches (visual similarity)"

Time 400ms:    Planner decides: needs object + action + reasoning

Time 600ms:    Tier 4 results arrive
               Object DB: squirrel tracks in segments 42, 67, 91, 103
               Action DB: "eating" detected in segments 42, 67, 91
               UI updates: narrows to 5 frames, re-ranked

Time 8s:       Tier 5 reasoning completes
               [Role 11] examined keyframes, confirmed 3 eating events
               UI updates with final answer:
               "The squirrel ate 3 nuts: an acorn at 02:22,
                a peanut at 05:11, and a walnut at 08:34"
               + clickable timeline markers
```

### UX Mockup

```
+---------------------------------------------------------+
| [magnifying glass] squirrel eating nuts          [Enter] |
+---------------------------------------------------------+
|                                                         |
|  TIMELINE: [====*====*=================*==========]     |
|                 ^    ^                 ^                 |
|               2:22  5:11             8:34               |
|                                                         |
|  Verified eating events (3 found):              [8.2s]  |
|  +--------+  +--------+  +--------+                    |
|  | [2:22] |  | [5:11] |  | [8:34] |                    |
|  |squirrel|  |squirrel|  |squirrel|                    |
|  |eating  |  |eating  |  |cracking|                    |
|  |acorn   |  |peanut  |  |walnut  |                    |
|  +--------+  +--------+  +--------+                    |
|                                                         |
|  Answer: "The squirrel ate 3 nuts -- an acorn at 2:22, |
|  a peanut at 5:11, and a walnut at 8:34."              |
+---------------------------------------------------------+
```

---

## Data Model

```sql
CREATE TABLE videos (
    id UUID PRIMARY KEY,                            -- internal unique id (referenced by all other tables)
    source_type TEXT NOT NULL,                      -- 'youtube' | 'local' (extensible: 'url', 's3', …)
    source_uri TEXT NOT NULL,                       -- canonical input: full YouTube URL or original local path
    source_key TEXT NOT NULL UNIQUE,                -- dedup key: YouTube video_id, or sha256 for local files
    local_path TEXT,                                -- cached/downloaded file on disk (NULL until fetched)
    title TEXT,                                     -- human-friendly label (from source metadata)
    duration_seconds FLOAT,
    fps FLOAT,
    resolution TEXT,
    has_audio BOOLEAN,
    ingest_status TEXT NOT NULL DEFAULT 'pending',  -- pending | fetching | processing | done | failed
    ingest_error TEXT,                              -- last error message when ingest_status = 'failed'
    created_at TIMESTAMP DEFAULT NOW(),
    fetched_at TIMESTAMP,                           -- when download/copy to local_path completed
    processed_at TIMESTAMP                          -- when embedding/indexing completed
);

-- Idempotent ingest: look up by source_key before processing.
--   YouTube → source_key = the 11-char video_id (so the same video via different URL forms dedupes).
--   Local   → source_key = sha256 of file contents (so a moved/renamed file is recognized).
-- A row with ingest_status='done' means "already ingested" → skip. 'failed'/partial → safe to re-run.

CREATE TABLE segments (
    id UUID PRIMARY KEY,
    video_id UUID REFERENCES videos(id),
    segment_index INT,
    start_time FLOAT,
    end_time FLOAT,
    duration FLOAT GENERATED ALWAYS AS (end_time - start_time) STORED,
    keyframe_paths TEXT[],
    caption TEXT,
    UNIQUE(video_id, segment_index)
);

CREATE TABLE object_tracks (
    id UUID PRIMARY KEY,
    video_id UUID REFERENCES videos(id),
    object_class TEXT NOT NULL,
    track_confidence FLOAT,
    first_seen FLOAT,
    last_seen FLOAT,
    frame_count INT
);

CREATE TABLE object_detections (
    id BIGSERIAL PRIMARY KEY,
    video_id UUID NOT NULL,
    timestamp FLOAT NOT NULL,
    track_id UUID REFERENCES object_tracks(id),
    object_class TEXT NOT NULL,
    bbox_x FLOAT, bbox_y FLOAT, bbox_w FLOAT, bbox_h FLOAT,
    confidence FLOAT
);

CREATE TABLE action_events (
    id BIGSERIAL PRIMARY KEY,
    video_id UUID NOT NULL,
    segment_id UUID REFERENCES segments(id),
    action_class TEXT NOT NULL,
    confidence FLOAT,
    start_time FLOAT,
    end_time FLOAT
);

CREATE TABLE transcripts (
    id BIGSERIAL PRIMARY KEY,
    video_id UUID REFERENCES videos(id),
    start_time FLOAT,
    end_time FLOAT,
    speaker TEXT,
    text TEXT NOT NULL
);

CREATE TABLE ocr_results (
    id BIGSERIAL PRIMARY KEY,
    video_id UUID NOT NULL,
    timestamp FLOAT,
    text TEXT NOT NULL,
    bbox_x FLOAT, bbox_y FLOAT, bbox_w FLOAT, bbox_h FLOAT
);

CREATE TABLE audio_events (
    id BIGSERIAL PRIMARY KEY,
    video_id UUID NOT NULL,
    timestamp FLOAT,
    event_type TEXT,
    confidence FLOAT
);

-- Vector data stored in Milvus/Qdrant (not Postgres)
-- Full-text data stored in Elasticsearch/Typesense (not Postgres)
```

---

## Metadata Storage Overhead

**Question:** For every hour of video we ingest, how much *additional* storage does the generated metadata cost — and what percentage of the source video is that?

**Short answer:** Roughly **0.3% – 6%** of the source video size, almost entirely driven by frame embeddings. For typical 1080p footage it lands around **1–2%**. Text and structured metadata (captions, transcripts, object/action rows) are collectively **< 1 MB/hour** — effectively free. The real wildcards are *stored keyframe images* and *pixel-level masks*, which can dwarf everything else if not managed.

### Assumptions

| Parameter | Value | Notes |
|---|---|---|
| Embedding dimension | 768, float32 = **3.07 KB/vector** | Role 2 / Role 3 contract |
| Vector index overhead | ~1.5× raw | HNSW graph (Milvus/Qdrant) adds ~30–60% |
| Frame sampling | 1–3 fps | Role 2 ingest rate |
| Segments per hour | 60–120 | ~30–60s scenes (Role 1) |
| Speech rate | ~150 words/min | ~9,000 words/hour |
| Objects per sampled frame | ~8 | Role 5 at 1 fps |
| Postgres row cost | ~80–120 B incl. overhead | per detection/event/transcript row |

### Per-Component Breakdown (per hour of video)

| Source (Role) | What's stored | Volume/hour | Size (indexed) |
|---|---|---|---|
| **Frame embeddings (2)** | 768-d vector per sampled frame | 3,600 @1fps → 10,800 @3fps | **16 MB → 50 MB** |
| **Audio embeddings (3, opt.)** | vector per ~1s window | ~3,600 vectors | ~10–15 MB |
| Captions (4) | ~1.5 KB text/segment | 60–120 segments | ~0.1–0.2 MB |
| Object detections (5) | bbox + class + conf row | ~29,000 rows @1fps | ~3–6 MB |
| Object tracks (6) | aggregated track row | ~200–400 tracks | ~0.04 MB |
| Action events (7) | label + interval row | ~150–300 rows | ~0.02 MB |
| Transcripts (8/9) | timestamped text + speaker | ~9,000 words | ~0.06–0.1 MB |
| OCR (10, opt.) | on-screen text rows | text-density dependent (measured: 37–111 rows on 1–3 min clips with per-appearance dedup) | ~0.1–0.5 MB |

> **Everything except embeddings totals < 7 MB/hour** — and the text/structured part alone is under 1 MB. The embeddings *are* the metadata budget.

### Totals by Configuration

| Config | Roles active | Metadata/hour |
|---|---|---|
| **Minimal** (Phase 1) | Embeddings only @1fps | **~16 MB** |
| **Typical** (Phases 1–3) | Embeddings @2fps + captions + transcript + objects | **~40 MB** |
| **Full** (all roles) | @3fps + audio embeddings + OCR + everything | **~70 MB** |

### As a Percentage of Source Video

Source video size for 1 hour depends entirely on bitrate/resolution:

| Source video (1 hour) | Approx. size | Minimal (16 MB) | Typical (40 MB) | Full (70 MB) |
|---|---|---|---|---|
| 480p @ ~1 Mbps | 0.45 GB | 3.6% | 8.9% | 15.6% |
| 720p @ ~3 Mbps | 1.35 GB | 1.2% | 3.0% | 5.2% |
| **1080p @ ~8 Mbps** | **3.6 GB** | **0.4%** | **1.1%** | **1.9%** |
| 4K @ ~25 Mbps | 11.25 GB | 0.14% | 0.36% | 0.62% |

**Key insight:** Metadata size scales with *sampling rate and duration*, **not** with video bitrate. So the overhead percentage is **highest for low-bitrate / low-resolution video** and negligible for high-quality footage. A heavily-compressed 480p stream pays the largest relative tax; 4K barely notices.

### The Wildcards (can exceed all of the above)

These are *not* in the totals above because they're a deliberate design choice, but they can dominate storage if enabled naively:

- **Stored keyframe images** (`segments.keyframe_paths`): persisting extracted JPEGs at 1–10 keyframes/segment × ~150 KB ≈ **20–180 MB/hour** — potentially larger than all structured metadata combined. *Mitigation:* regenerate keyframes on demand from the source video instead of storing them, or store low-res thumbnails only.
- **Pixel-level masks** (Role 6): raw per-frame masks for every tracked object explode quickly. *Mitigation:* store RLE/polygon-encoded masks (hundreds of bytes each) or only box coordinates, not raster masks.
- **Dual embeddings** (Role 2 + Role 3): roughly doubles the dominant cost — only enable if audio↔visual cross-search is needed (see [Dual Embedding vs Single](#4-dual-embedding-vs-single)).

### Reducing the Dominant Cost: Embedding Dedup for Static Footage

Since frame embeddings *are* the metadata budget, the highest-leverage optimization is to stop storing near-identical vectors. The [stable-scene coalescing](#long-static-scenes-coalescing-captions) technique used for captions applies directly to Role-2 vectors: within a static span, keep one representative embedding (mapped to a time range) instead of one per sampled frame.

Define a **retention ratio** = fraction of sampled frames whose embedding you keep. It depends almost entirely on how dynamic the footage is:

| Footage type | Typical retention | Vectors/hr @1fps (was 3,600) | Embedding storage/hr (was ~16 MB) | Savings |
|---|---|---|---|---|
| CCTV / fixed camera (mostly static) | ~2–5% | ~70–180 | ~0.3–0.8 MB | **~95–98%** |
| Lecture / interview (one angle, talking head) | ~5–15% | ~180–540 | ~0.8–2.4 MB | **~85–95%** |
| Edited content / vlog (frequent cuts, motion) | ~50–80% | ~1,800–2,900 | ~8–13 MB | ~20–50% |
| Sports / action (constant motion) | ~90–100% | ~3,250–3,600 | ~14–16 MB | ~0–10% |

**Worked example:** a 40-minute static scene at 1 fps is 2,400 frames. If the content meaningfully changes only a few dozen times, coalescing drops it from **2,400 → ~50 stored vectors (~98% fewer)** for that span — while a `(start, end)` range on each kept vector preserves "when" for retrieval.

**Caveats to validate against real footage:**
- **Storage is saved for certain; compute depends on the gate.** If you decide redundancy via embedding cosine similarity, you still *embed* every frame (to compare), so you save storage + index-insert cost but not embedding compute. Gate on a cheaper signal first (perceptual hash / frame-difference) to also save compute.
- **Temporal precision trade-off.** A coalesced vector covers a span, so a Tier-1 hit returns the span rather than an exact frame; Tier 4/5 then pinpoints. Acceptable for the progressive-escalation flow, but tune the similarity threshold τ so spans don't get so coarse that search recall suffers.
- These retention numbers are **rough planning estimates** — revisit with measured ratios on actual footage and confirm the table holds before committing capacity plans.

**Takeaway for capacity planning:** budget **~40–70 MB of metadata per video-hour** for a full-featured index (excluding stored keyframes/masks) *as an upper bound for dynamic footage*. For static-heavy corpora (surveillance, lectures), embedding dedup can cut the dominant line item by 85–98%, pulling the total well below 20 MB/hour. At the un-deduped rate, indexing **10,000 hours** of video costs roughly **0.4–0.7 TB** of metadata — small enough that the source video, not the index, remains the storage bottleneck either way.

---

## Trade-offs and Decision Points

### 1. Caption Granularity

| Approach | VLM Calls (1hr video) | Search Quality |
|---|---|---|
| Per scene (~30-60s segments) | ~60-120 | Good for most queries |
| Per 10s chunk | ~360 | Better recall |
| Per keyframe (~3-5s) | ~720-1200 | Best, but expensive |

**Recommendation:** Content-aware scenes (Role 1) as primary segmentation. Increase density only where search recall is poor.

#### Long Static Scenes: Coalescing Captions

The tables above assume scenes are short. The opposite case is just as common: a static security camera, a lecture filmed from one angle, or a long interview can run **one visual scene for 10–40 minutes**. Naively re-captioning every keyframe across that span produces hundreds of near-identical captions.

**First, an honest caveat on what this saves.** Caption *text* is already the cheapest thing we store (~0.1–0.2 MB/hour — see [Metadata Storage Overhead](#metadata-storage-overhead)). Collapsing redundant captions barely moves storage. The real wins are elsewhere:
1. **VLM compute at ingest** — Role 4 is the most expensive ingest model; skipping redundant calls is the big saver.
2. **The same dedup applied to embeddings** — Role 2 vectors are the actual storage hog (~16–50 MB/hour). A static scene stores hundreds of near-identical 768-d vectors. Deduping *those* saves real bytes.

So treat "single caption for a collection of frames" as one instance of a general **stable-scene coalescing** strategy applied across Roles 2 and 4.

**The trap to avoid:** one visual scene is *not* one event. A static camera on a park bench is a single "shot," but a squirrel can enter, eat, and leave within it. Flattening the whole span into one caption destroys the temporal detail that powers queries like "when did the squirrel start eating." The goal is **content-adaptive granularity**, not blind collapse.

**Primary technique — embedding-similarity coalescing (reuses data we already compute):**

During ingest, within a Role-1 scene, walk the sampled keyframes and compare each frame's Role-2 embedding (already computed — free) to the current *anchor* frame via cosine similarity:

```
anchor = first keyframe of scene
for each subsequent keyframe k:
    if cosine_sim(embed[k], embed[anchor]) >= τ   # content unchanged
        extend current caption span to k.timestamp   # no VLM call, no new row
    else                                              # content meaningfully changed
        emit caption for [anchor .. k-1]  (1 VLM call)
        anchor = k
```

This yields **variable-length caption spans driven by actual visual change**: a 30-minute static span becomes a handful of captions instead of hundreds, while a busy span still gets fine-grained coverage. Typical threshold τ ≈ 0.92–0.97 (tune per domain). Because it piggybacks on embeddings we compute anyway, it adds negligible ingest cost.

**Schema:** captions are already stored per segment with `start_time`/`end_time` (see `segments`), so a coalesced caption is simply a span with a wider time range — no schema change needed for the basic case. For sub-scene spans within one Role-1 segment, add a lightweight child table:

```sql
CREATE TABLE caption_spans (
    id BIGSERIAL PRIMARY KEY,
    segment_id UUID REFERENCES segments(id),
    start_time FLOAT,
    end_time FLOAT,
    caption_id BIGINT REFERENCES captions(id)  -- interned, see below
);
```

**Secondary technique — caption interning (storage dedup):** store each unique caption string once in a `captions(id, text)` dictionary and reference it by ID. Identical or near-identical captions from repetitive footage collapse to one row + an 8-byte FK each. Useful for corpora with lots of repeated content (e.g. many clips of the same fixed scene).

**Optional — delta / event captioning:** for very long static scenes, store one *base* caption for the span plus compact change-events: `"base: empty park bench — Δ@2:15 squirrel enters — Δ@5:40 squirrel leaves."` Maximally compact while preserving temporal markers; costs a cheap frame-difference check per keyframe to detect the deltas.

| Strategy | Saves VLM calls | Saves embedding bytes | Preserves temporal detail | Cost to add |
|---|---|---|---|---|
| Embedding-similarity coalescing | ✅ Large | ✅ (if applied to Role 2 too) | ✅ Adaptive | Free (reuses embeddings) |
| Caption interning | — | ✅ Text only (small) | ✅ | Trivial (FK indirection) |
| Delta / event captioning | ✅ | — | ✅ Best | Cheap frame-diff per keyframe |

**Recommendation:** Use embedding-similarity coalescing as the default — it directly answers "one caption for a long unchanging span," cuts the expensive VLM calls, and the *same* similarity signal lets you skip storing redundant Role-2 vectors, which is where the real storage savings are.

### 2. Object Detection: Always-On vs On-Demand

| Approach | Ingest Cost | Query Speed | Coverage |
|---|---|---|---|
| Always-on at ingest (1fps) | Medium | Fast (pre-indexed) | Common objects |
| On-demand at query time | None | Slower first query | Any object |
| Hybrid | Medium | Fast + flexible | Best of both |

**Recommendation:** Hybrid — fast detector (Role 5) at ingest for common objects, precise detector on-demand for specific queries.

### 3. Where Models Run

| Role | Run Locally? | Run on Cloud? | Recommendation |
|---|---|---|---|
| Roles 1-10 | Yes (all fit locally) | Possible but unnecessary cost | **Local** |
| Role 11 (Reasoning) | Yes (self-hosted VLM) | Yes (Claude, Gemini) | **Cloud preferred**, local fallback |

### 4. Dual Embedding vs Single

| Approach | Models | What You Get | Cost |
|---|---|---|---|
| Single (Role 2 only) | Visual embedding | Text→image search | ~3KB per frame |
| Dual (Role 2 + Role 3) | Visual + cross-modal | Text→image + audio↔visual search | ~7KB per frame |

**Recommendation:** Start with single (Role 2). Add Role 3 only if audio↔visual search is needed.

---

## MVP Scope

### Phase 1: "Ctrl-F for Video" (2-3 weeks)

Roles needed: **2** (Visual Embedding)

1. Video upload → extract frames at 1fps → embed via Role 2 → Vector DB
2. Simple web UI: text box + video player + timeline
3. Query → embed → vector search → highlight timeline + thumbnails
4. Click thumbnail → jump to timestamp

**Delivers:** Fast visual search. "Show me the dog." Sub-100ms.

### Phase 2: Captions + Audio (2-3 weeks)

Roles added: **1** (Scene Detector), **4** (VLM Captioner), **8** (Speech-to-Text)

1. Scene detection → segment boundaries
2. VLM captioning per segment → full-text index
3. Whisper transcription → full-text index
4. Unified search: merge visual + transcript + caption results

**Delivers:** Semantic search + audio search. "When did they discuss the budget?"

### Phase 3: Object Tracking + Structured Queries (2-3 weeks)

Roles added: **5** (Object Detector), **6** (Object Tracker), **7** (Action Recognizer)

1. Object detection at 1fps → object tracks
2. Action recognition per segment → event timeline
3. SQL queries: "how many X", "when does Y first appear"

**Delivers:** Counting, tracking, structured analytics.

### Phase 4: Complex Analytical Queries (2-3 weeks)

Roles added: **11** (Reasoning LLM)

1. Query planner (Role 11) classifying intent
2. Multi-source retrieval for complex queries
3. Progressive escalation with streaming results
4. Cited answers with timestamp links

**Delivers:** Full "ask anything about this video" experience.

### Phase 5: Polish + Optional Roles (2-3 weeks)

Roles added: **3** (Cross-Modal), **9** (Speaker Diarizer), **10** (OCR)

1. Cross-modal audio↔visual search (if needed)
2. Speaker identification in transcripts
3. On-screen text extraction
4. UX refinements

---

## Future Extensions

- **Multi-video corpus search**: "Find this person across all videos"
- **Video summarization**: Auto-generate executive summaries
- **Anomaly detection**: "Flag anything unusual"
- **Fine-tuned models**: Train on your domain (wildlife, security, sports)
- **Spatial queries**: "What's happening in the top-right corner?"
- **Comparative analysis**: "How does this video compare to that one?"
- **RAG over video corpus**: Chat with your entire video library
- **Export**: Generate highlight reels, clips, annotated summaries

---

## Key Architectural Insight

```
Cheap models at ingest time (Roles 1-10)    -> Enable fast retrieval
Expensive models at query time (Role 11)    -> Enable deep understanding
Smart routing between them (Planner)        -> Optimize cost/quality
Local-first (Roles 1-10)                    -> Minimize ongoing costs
Cloud only for reasoning (Role 11)          -> Best quality where it matters
```

---

## References

- **Model Selection:** See [video-analytics-model-analysis.md](video-analytics-model-analysis.md)
- **NVIDIA VSS Blueprint:** `github.com/NVIDIA-AI-Blueprints/video-search-and-summarization`
- **Milvus Vector DB:** `milvus.io`
- **Qdrant Vector DB:** `qdrant.tech`
