# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A proof-of-concept "Ctrl-F for Video" platform: ingest a video (YouTube URL or local file)
and search it with natural-language text, returning ranked moments (timestamps). **Roles 1
(Scene Detection), 2 (Visual Embedding), 4 (VLM Captioner), 5 (Object Detector), 6 (Object
Tracker), 7 (Action Recognizer), 8 (Speech-to-Text), 9 (Speaker Diarizer), 10 (OCR), and 11
(Reasoning/Planner)** of the planned 11-role pipeline are implemented so far (only Role 3,
cross-modal audio, remains). Design docs:
`plan.md` (implementation plan + role roadmap), `video-analytics-solution-architecture.md`
(the 11 roles + data model), `solution_code_hike.md` (detailed walkthrough of what's built),
`video-analytics-model-analysis.md` (**per-role model-selection decisions + "revisit when…"
triggers** — the place to record/reconsider which model backs each role, e.g. the Role 7
X-CLIP decision).

**Multiple agents work in this repo in separate sessions.** Read `COORDINATION.md` at
session start — it defines ownership boundaries and the cross-layer contract, and has an
append-only log. Log any change to shared interfaces there. The web frontend is planned in
`web-frontend-plan.md`. **Stabilization/QA phase** (traceability → test-authoring UI → failure
root-cause ledger → deferred CI/CD) is planned in `qa-and-traceability-plan.md` — the current
focus is hardening the repo for a stable git baseline, not new roles.

## Commands

Everything runs through a project-local venv (editable install). There is no separate build
step and no linter configured.

```bash
# setup
python3 -m venv .venv
.venv/bin/pip install -e .              # core deps; uses the STUB embedder (no GPU/network)
.venv/bin/pip install -e '.[siglip]'    # optional: real SigLIP (torch+transformers, heavy)

# tests (34, no GPU/network — they use the stub embedder + synthetic clips)
.venv/bin/pytest -q
.venv/bin/pytest tests/test_e2e.py -q                 # one file
.venv/bin/pytest tests/test_catalog.py::test_get_or_create_is_idempotent -q   # one test

# run with the STUB backends (color-only visual + sidecar transcript; default config)
.venv/bin/va --workdir .va ingest "<youtube-url-or-local-path>"
.venv/bin/va --workdir .va query "red sports car" -k 5      # visual search (Role 2)
.venv/bin/va --workdir .va transcript "the budget" -k 5     # speech search (Role 8)
.venv/bin/va --workdir .va transcript "the budget" --speaker SPEAKER_01  # filter by speaker (Role 9)
.venv/bin/va --workdir .va caption "the kitchen scene" -k 5 # caption search (Role 4)
.venv/bin/va --workdir .va ocr "coors light" -k 5           # on-screen text search (Role 10)
.venv/bin/va --workdir .va actions "driving" -k 5           # recognized actions (Role 7)
.venv/bin/va --workdir .va objects "car person"             # object appearances (Role 5)
.venv/bin/va --workdir .va count "car"                      # distinct instances (Role 6)
.venv/bin/va --workdir .va ask "what color is the car?"     # reasoned, cited answer (Role 11)
.venv/bin/va --workdir .va remove "<uuid|source_key|url>"   # delete a video everywhere
.venv/bin/va --workdir .va reingest "<...>"                 # re-process (model changes)

# run with the REAL models (SigLIP + Whisper) on GPU; downloads weights on first use
VA_CONFIG_DIR=run-siglip/config .venv/bin/va --workdir .va ingest "<url>"
VA_CONFIG_DIR=run-siglip/config .venv/bin/va --workdir .va query "<text>" -k 5
VA_CONFIG_DIR=run-siglip/config .venv/bin/va --workdir .va transcript "<text>" -k 5

# web UI (browser on the LAN: ingest, play, search-with-click-to-seek; see web-frontend-plan.md)
.venv/bin/pip install -e '.[web,dev]'
VA_CONFIG_DIR=run-siglip/config .venv/bin/va --workdir .va serve --port 8080
```

`run-siglip/config` selects the real backends per role (visual_embedder=siglip,
speech_to_text=whisper, speaker_diarizer=pyannote, vlm_captioner=qwen2.5-vl-7b,
object_detector=yolo-world,
object_tracker=bytetrack, action_recognizer=xclip, ocr=rapidocr, reasoner=qwen2.5-vl-7b,
scene_detector=**pyscenedetect** — the
histogram default merges montage-style cuts: measured 6 vs 71 segments on the same clip,
which silently destroys per-shot captions; `run-claude/config` = same but reasoner=claude-code). Each role
follows the same pattern: a dependency-free **stub** default (hash / sidecar / color /
histogram / iou / motion / rule) so tests run offline, plus a **real** backend behind an optional
extra (`[siglip]`, `[whisper]`, `[diarize]`, `[qwenvl]`, `[yolo]`, `[track]`, `[action]`, `[scenedetect]`, `[ocr]`).
`va objects` = frame appearances (Role 5); `va count` = distinct instances via tracks
(Role 6). **Tracker caveat:** the default `iou` tracker over-counts fast-moving objects at
1 fps sampling (no motion model) — use `bytetrack` for real footage; measured on the
Ferrari clip: iou 38 "cars" vs bytetrack 6. `va actions` = recognized actions (Role 7).
**Action caveat:** X-CLIP scores a **fixed ingest-time vocabulary** (`DEFAULT_INGEST_ACTIONS`,
overridable via roles.yaml `actions:`) per segment and always picks the least-bad label
(softmax over the requested phrases) — so it answers "is *one of these* actions happening"
well (Ferrari → "driving a car" 0.94-0.99) but cannot recognize a specific action the vocab
doesn't list ("counting dresses" came back "dancing"). Arbitrary-action queries need
query-time recognition (the action analogue of GroundingDINO) — not built. An **abstention
foil** (`NO_ACTION = "no particular action"`, always in the candidate set) gives the softmax
somewhere to park probability when nothing fits; when it wins, no event is stored. Measured:
it left confident-correct labels intact (Ferrari 11/11 driving) while trimming the dresses
montage from 29 → 23 borderline labels.

**Role 11 (`va ask`)**: `pipeline/ask.py` runs plan (LLM call 1) → `assemble()` evidence →
keyframes at top moments (per-video `keyframes/` dirs) → reason (LLM call 2, sees images) →
answer rendered with hyperlinked timestamps (YouTube `&t=` deep links). Deep-scan triggers
are defense-in-depth: LLM planner (primary) + closed regex floor (weak-planner/offline
paths) + **self-escalation** (insufficient sparse answer → one deep-scan re-run). When a
deep scan ran, the rendered answer LEADS with the verbatim CODE-COUNTED line. Reasoner backends:
`rule` (stub/fallback), `qwen2.5-vl-7b` (shares the Role-4 model — same ModelManager key,
no extra VRAM), `claude-code` (headless `claude -p` on the local subscription login),
`claude-api` (**placeholder** — pending the ANTHROPIC_API_KEY decision; raises with
guidance). LLM JSON is parsed tolerantly (`parse_json_block`, `coerce_timestamp` — Qwen
really does emit `"3.5s"`); unparseable output falls back to the rule reasoner.

## Heuristics & validation (engineering conventions)

- **Never introduce hardcoded content, magic values, or canned heuristics silently.** Flag
  them explicitly when proposing them, explain the choice, and ask before relying on them
  (a hardcoded `scan_target` string had to be challenged by the user; don't repeat that).
  Hardcoded *structure* (mechanisms, budgets) is fine; hardcoded *content* (subjects,
  domain strings) almost never is — derive content from the user's query or the data.
- **Determinism is not correctness.** For counting/detection features, validate output
  against known ground truth before declaring success (the deep-scan counted 70-99 "dress
  changes" with perfect stability; truth was ~12-15 — it was reproducibly counting camera
  cuts). Report results alongside the ground-truth comparison.

## The two things most likely to trip you up

1. **Default config uses a stub, not a real model.** `config/roles.yaml` sets
   `visual_embedder.model: hash` — a deterministic *color-aware* stub (a red frame matches the
   word "red"). It exists so the whole pipeline + tests run with no GPU/network/downloads. For
   real semantic search you must select SigLIP via **`VA_CONFIG_DIR=run-siglip/config`** (a
   separate config dir kept apart so tests still use the stub).
2. **Ingest and query must use the same embedder config.** The stub is 64-dim, SigLIP is
   1152-dim — different vector spaces. Switching models = `va reingest <video>` (per
   video, same workdir). Workdir layout v2: `catalog.db` (ONE shared DB for all videos) +
   `videos/<key16>-<slug>/` per-video dirs (media + `vectors.npz` shard + `keyframes/`) +
   transient `cache/`. The shards form one logical index — search spans all videos.
   `va remove <video>` deletes everywhere; pre-v2 workdirs: `va migrate-layout`.

## Architecture: the hosting-agnostic spine

The central design constraint (the DGX Spark may not hold every model, so any role must run
locally OR remotely without caller changes) shapes everything. Three seams per role:

- **Role interface** — `src/va/roles/<role>.py` is a `Protocol`. Callers depend only on this.
- **Adapters** — `src/va/adapters/<role>/*` are interchangeable backends: `*_inproc` (in-process),
  and (future) `http_client` / cloud clients. For Role 2: `hash_inproc.py` (stub) and
  `siglip_inproc.py` (real).
- **Registry** — `src/va/registry.py` reads config and returns the right adapter. Swapping a
  backend is a one-line edit in `config/roles.yaml`; no pipeline code changes.

`src/va/configuration.py` merges `roles.yaml` (which backend+model per role) with the active
`config/profiles/<name>.yaml` (per-model load params: device/dtype/weights) into one
`RoleConfig`. `VA_CONFIG_DIR` overrides the config directory.

## Architecture: two pipelines over shared stores

Both live in `src/va/pipeline/`. The universal join key is `video_id`.

**Ingest (write path, `ingest.py`):** `resolve_source(uri)` → `VideoSource.resolve()` (cheap,
yields a stable `source_key` for dedup) → **`Catalog.get_or_create()`** (skips if already
`done` — this is the idempotency point) → `source.fetch()` (yt-dlp download ≤480p, or locate
local file) → **`SceneDetector.detect()` → `SegmentStore` (Role 1 segments)** →
**`VLMCaptioner.caption()` per segment keyframe → `segments.caption` (Role 4, best-effort)** →
**`SpeechToText.transcribe()` (Role 8) → `SpeakerDiarizer.diarize()` → `assign_speakers()`
joins turns onto the lines by temporal overlap → `TranscriptStore` (Roles 8+9, best-effort)** →
**`OcrReader.read()` → `OcrStore` (Role 10, best-effort)** →
**`ActionRecognizer.recognize()` per Role-1 segment → `ActionStore` (Role 7, best-effort)** →
`media.sample_frames()` at N fps → `VisualEmbedder.embed_image()` (batches of 32) →
`VectorStore.add()` tagged with `video_id`+`timestamp` → mark `done`. There are five query
paths: `query.py` (visual, Role 2), `caption.py` (scene descriptions, Role 4),
`transcript.py` ("what was said", Role 8), `ocr.py` (on-screen text, Role 10), and
`actions.py` (what happens, Role 7); the
`va ask` planner (Role 11) unifies them via QueryPlan tier flags.

**Query (read path, `query.py`):** `embed_text()` → `VectorStore.search()` (cosine top-k) →
join each hit's `video_id` back to the catalog for `source_uri` → ranked `SearchHit`s. Text and
images share one vector space, so search is just nearest-neighbor between a text vector and
pre-computed frame vectors.

Supporting layers:
- **`src/va/runtime/`** — `ModelManager` (singleton `MANAGER`) loads models once and caches
  them; in-process adapters get models via `MANAGER.get()`, never loading directly. `device.py`
  falls back cuda→cpu so the same config runs on the Spark or a laptop.
- **`src/va/sources/`** — `youtube.py` (any URL form → 11-char video_id = `source_key`),
  `local.py` (sha256 = `source_key`); `base.resolve_source()` dispatches.
- **`src/va/storage/`** — the **central correlation DB** is one SQLite file (`<workdir>/catalog.db`)
  whose full schema is `structured/schema.py`: `videos` (catalog/dedup) + one table per role
  (`segments`, `object_tracks`, `object_detections`, `action_events`, `transcripts`, `ocr_results`),
  all keyed by `video_id`. All tables are created up front; complex queries will correlate roles
  via temporal SQL joins on `video_id` + time. Today `catalog_sqlite.py` (videos) and
  `segments.py` (Role 1) write to it. Vectors live separately in `vector/numpy_flat.py` (brute-force
  cosine), also keyed by `video_id`. Everything is behind interfaces so Postgres / Milvus swap in later.
- **`src/va/contracts/`** — pydantic schemas (`Video`, `ResolvedVideo`, `FrameEmbedding`,
  `SearchHit`, `Segment`, `TranscriptLine`) mirroring the architecture doc's data model, plus
  the **runtime contracts** `QueryPlan`/`Evidence`/`Answer` (`query_plan.py`, `evidence.py`).
  The runtime contracts are evolution-tolerant by rule: every field has a default,
  `extra="allow"` preserves unknown fields across round-trips, modality-specific payload goes
  in `attributes`/`params` dicts. `pipeline/evidence.py` assembles an `Evidence` bundle from a
  plan's flags (skipping-and-noting unavailable/unknown tiers) — the input for future Role 11.

## Testing

Tests use the stub embedder + synthetic color clips (`media/synth.py`) so they assert real
retrieval behavior deterministically without models. See `tests/test_e2e.py` for the full
ingest→query path. **Golden-query fixtures** for real videos live in `tests/golden_queries/`
(`<video_id>.md` human + `<video_id>.yaml` machine-readable assertions); they are generated by
a vision+adversarial-verify agent workflow (see that dir's README) and split into `match` /
`no_match` / future-role queries. Two gated harnesses run them against a pre-ingested
real-model workdir (`RUN_GOLDEN=1 VA_CONFIG_DIR=run-claude/config GOLDEN_WORKDIR=.va-shots
.venv/bin/pytest -m golden`): **`test_golden_queries.py`** (per-modality `queries:` +
`semantic_text:` + `diarization:` blocks) and **`test_golden_ask.py`** (deep-scan counts).
Visual match = strongest hit *inside* `time_range` ≥ `min_score` (calibrated to **0.10** on
the first real run). Known model limitations carry `xfail: "<reason>"` (strict, so a better
model alerts). A query may set `verify: true` to route through the SR.6 VLM verifier (Qwen
re-checks SigLIP/YOLO results; selective, since blanket verification erodes recall). In the
live path the Role-11 planner auto-sets `QueryPlan.needs_visual_verification` (applied in
`retrieve()`); `va query --verify` is the manual switch. Current: 83 pass / 1 xfail / 0 fail
+ 2 ask questions pass. (NB: a fixture audit found two defects — cobra "indoor kitchen" was a
HALLUCINATED match (no kitchen; → no_match), and ferrari "grandstands" was passing on a FALSE
POSITIVE (real but distant grandstands SigLIP can't retrieve; → narrowed + xfail). Treat
low-score vision-verified MATCH fixtures as audit candidates; a green test can still be wrong.)

## Gotchas specific to this repo

- **No system ffmpeg** — frame decode and yt-dlp merging use the binary bundled by
  `imageio-ffmpeg`. `sources/youtube.py` symlinks it as `ffmpeg` and passes `ffmpeg_location`.
- **SigLIP** needs `protobuf` (in the `[siglip]` extra) for its tokenizer, and on
  transformers v5 `get_image_features`/`get_text_features` return an output object, not a
  tensor — `siglip_inproc.py` unwraps `image_embeds`/`text_embeds`/`pooler_output`.
- **SigLIP scores are small in absolute terms** (sigmoid training): relevant ≈0.11–0.18,
  irrelevant ≈0 or negative. Rank/relative gap matters, not magnitude.
- **Query always returns top-k regardless of score** — there is no relevance threshold yet, so
  a no-match query still prints hits (with low/negative scores).
- **paddlepaddle segfaults on this aarch64 box** (inference predictor init, PIR param
  loading; v5/v6 models, mkldnn on/off — all crash). Role 10 therefore uses RapidOCR
  (same PP-OCR models on onnxruntime). Don't "upgrade" the OCR backend to paddleocr
  without re-testing predictor init on aarch64.
- **Role 9 (pyannote) has FOUR setup gotchas — validated working 2026-06-15.** (1) pyannote
  **3.x crashes on import** here (uses `torchaudio.AudioMetaData`, removed in torchaudio 2.11 /
  torch 2.12+cu130); the `diarize` extra pins `>=4`. (2) The pipeline composes **four
  separately-gated HF models** — accept ALL of `pyannote/speaker-diarization-3.1`,
  `segmentation-3.0`, `speaker-diarization-community-1`, and `embedding` (wespeaker is ungated);
  each 403s individually until accepted. Authenticate with a read-scope token via
  `huggingface_hub.login(token=...)` (the `huggingface-cli` entrypoint may be absent; use the
  Python `login()` or `hf`). (3) pyannote 4.x decodes audio via **torchcodec**, which needs
  FFmpeg shared libs this box lacks → the adapter loads the WAV itself (`load_wav_mono`) and
  passes a `{"waveform","sample_rate"}` tensor to bypass it. (4) pyannote 4.x's pipeline returns
  a **`DiarizeOutput`** (use `.speaker_diarization`), not an `Annotation`. Diarization is
  best-effort in ingest — any of these failing just leaves `transcripts.speaker` NULL, never
  aborts the ingest. The **sidecar** stub (`<video>.diarization.json`) covers offline tests.
  Ungated alternative if gating is a blocker: **NeMo Sortformer** (Apache-2.0).
