# Video Analytics — "Ctrl-F for Video"

Ingest a video (YouTube URL or local file) and search it with natural language:
"red sports car" returns ranked, clickable moments. Beyond search, it can answer
questions about the video with cited timestamps ("how many times does she change
dresses?").

**7 of the planned 11 pipeline roles are implemented:**

| Role | What it does | Stub (default, offline) | Real backend (optional extra) |
|---|---|---|---|
| 1 — Scene Detection | split video into shots/segments | `histogram` | PySceneDetect `[scenedetect]` |
| 2 — Visual Embedding | text↔frame search in one vector space | `hash` (color-aware) | SigLIP SO400M `[siglip]` |
| 4 — VLM Captioner | describe each segment in a sentence | `color` | Qwen2.5-VL-7B `[qwenvl]` |
| 5 — Object Detector | which objects appear, when | `color` | YOLO-World `[yolo]` |
| 6 — Object Tracker | distinct instances ("how many cars") | `iou` | ByteTrack `[track]` |
| 8 — Speech-to-Text | search what was said | `sidecar` | Whisper `[whisper]` |
| 11 — Reasoner/Planner | reasoned, cited answers (`va ask`) | `rule` | Qwen2.5-VL-7B / Claude Code |

Every role follows the same pattern: a dependency-free **stub** so everything runs
with no GPU/network/downloads, plus a **real model** selected purely by config.
Design docs: [plan.md](plan.md) (roadmap),
[video-analytics-solution-architecture.md](video-analytics-solution-architecture.md)
(the 11 roles + data model), [solution_code_hike.md](solution_code_hike.md)
(code walkthrough), [web-frontend-plan.md](web-frontend-plan.md) (web UI).

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e .                # core deps — stub backends, runs anywhere
.venv/bin/pip install -e '.[web,dev]'     # web UI + test deps

# real models (each optional; downloads weights on first use; GPU recommended):
.venv/bin/pip install -e '.[siglip,whisper,scenedetect]'   # search + transcripts
.venv/bin/pip install -e '.[qwenvl,yolo,track]'            # captions, objects, ask
```

No system ffmpeg needed — the binary bundled with `imageio-ffmpeg` is used for
frame decode and YouTube downloads.

## The one thing to understand first: stub vs real config

The default config (`config/`) selects the **stubs** — e.g. the `hash` embedder is
a deterministic *color-aware* toy (a red frame matches the word "red"). That's what
the tests use, and it's great for poking at the plumbing, but it is not semantic
search. To run the **real models**, point `VA_CONFIG_DIR` at the prepared config:

```bash
VA_CONFIG_DIR=run-siglip/config .venv/bin/va ...   # SigLIP, Whisper, Qwen, YOLO-World, ByteTrack
VA_CONFIG_DIR=run-claude/config .venv/bin/va ...   # same, but `va ask` reasons via Claude Code CLI
```

**Ingest and query must use the same config.** The stub and SigLIP embed into
different vector spaces (64-dim vs 1152-dim), so pick a config per `--workdir`
(the state directory) and stick with it. Switching models = `va reingest <video>`
(per video — re-embeds into the new space). Keeping one workdir per config is a
good habit:

```bash
alias va-stub='.venv/bin/va --workdir .va-stub'
alias va-real='VA_CONFIG_DIR=run-siglip/config .venv/bin/va --workdir .va'
```

A workdir is one shared library of videos (layout v2):

```
<workdir>/
├── catalog.db                     # ONE SQLite DB: videos, segments, transcripts,
│                                  # detections, tracks, observations (all videos)
├── cache/                         # transient downloads only
└── videos/<key16>-<slug>/         # per-video artifacts, human-readable names
    ├── media.mp4                  # the managed media copy
    ├── vectors.npz/.json          # this video's embedding shard
    └── keyframes/                 # extracted keyframes (ask/deep-scan)
```

Search spans all videos in the workdir (the shards form one logical index).
`va remove <video>` deletes a video everywhere (rows + its directory);
`va reingest <video>` re-processes it (e.g. after switching models) — both accept
a UUID, source_key, URL, or path. Workdirs from before layout v2:
`va --workdir <dir> migrate-layout` (idempotent; old index kept as `*.v1.bak`).

## Use it from the command line

```bash
# ingest — runs the whole pipeline: scenes → captions → speech → objects → frames
# (idempotent: re-ingesting the same source is a no-op)
va-real ingest "https://www.youtube.com/watch?v=<id>"
va-real ingest /path/to/local.mp4

# search, one command per modality — each prints "score  m:ss  ..." rows
va-real query "red sports car" -k 5         # Role 2: what's VISIBLE
va-real caption "the kitchen scene" -k 5    # Role 4: scene descriptions
va-real transcript "the budget" -k 5        # Role 8: what was SAID
va-real objects "car person"                # Role 5: object appearances per frame
va-real count "car"                         # Role 6: DISTINCT instances via tracks

# ask — Role 11: plans which tiers to query, gathers evidence across all of the
# above, looks at keyframes, and answers with hyperlinked timestamps
va-real ask "what color is the car?"
va-real ask "how many times does she change dresses?" --show-evidence
```

Notes for playing around:

- Scores are cosine similarities; **SigLIP scores look small** (relevant ≈ 0.11–0.18,
  irrelevant ≈ 0 or negative). The ranking and the gap matter, not the magnitude.
  There's no relevance threshold yet — a no-match query still prints top-k rows.
- First use of each real model downloads weights (SigLIP ~3.5GB, Qwen2.5-VL-7B
  ~16GB, Whisper base ~150MB, YOLO-World ~100MB). Don't kill the process mid-download.
- Counting questions trigger a deep frame sweep inside `ask` — **~3–4 minutes on
  the first run** per video, cached and fast afterwards.
- `va ask` with an LLM backend takes 5–60s (two LLM calls + keyframe extraction);
  the `rule` stub answers instantly from gathered evidence.

## Use it from the browser (web UI)

```bash
VA_CONFIG_DIR=run-siglip/config .venv/bin/va --workdir .va serve --port 8080
```

The server operates on the same workdir library as the CLI: anything already
ingested there (by the CLI, a script, or another session) shows up in the
dropdown immediately, and videos ingested through the browser are equally
usable from the CLI afterwards.

Open `http://<this-machine's-LAN-IP>:8080` from any browser on the network:

- **Ingest box** — paste a YouTube URL / direct media URL / server-local path;
  ingest runs on a background queue (one at a time — GPU work is serialized) with
  a live status pill.
- **Video dropdown** — every ingested video; selecting one loads the player
  (YouTube embed for YouTube sources, HTML5 `<video>` streaming the ingested copy
  otherwise).
- **Search box** — one query fans out to all four modalities and renders four
  columns (visual / caption / transcript / objects). **Click any hit and the
  player seeks to that moment.**
- **Ask box** — Role 11 over HTTP: the answer renders with clickable timestamp
  links, and the evidence list below it is click-to-seek too. Asks run on a
  background queue (one at a time) and the page polls with an elapsed-time
  pill — most answers take seconds, but an ask that escalates to a deep scan
  can take a few minutes on its first run.

## Test

```bash
.venv/bin/pytest -q                # whole suite, no GPU/network — stubs + synthetic clips
.venv/bin/pytest tests/test_e2e.py -q      # the full ingest→query path
.venv/bin/pytest tests/test_web.py -q      # the web API (ingest queue, search, media, ask)
```

Tests generate synthetic color clips (`media/synth.py`) and assert real retrieval
behavior deterministically — e.g. `test_e2e.py` builds a red/green/blue clip and
asserts `"red sports car"` retrieves a red moment. Golden-query fixtures for real
videos live in `tests/golden_queries/` (human + machine-readable; the runnable
harness for them is future work).

## How it's put together (short version)

Two pipelines over shared stores, joined on `video_id`:

- **Ingest (write path)** — resolve source → dedup via catalog → download/locate →
  detect scenes → caption each segment → transcribe speech → detect objects →
  track instances → embed frames → vector store. Roles 4/5/8 are best-effort: a
  failing model skips that modality rather than failing the ingest.
- **Query (read path)** — embed the text → nearest-neighbor over frame vectors
  (visual), or SQL over the per-role tables (captions/transcripts/objects/tracks).
  `va ask` sits on top: an LLM plans which tiers to query, evidence is assembled
  from all of them, and a second LLM call (with keyframe images) writes the answer.

The architecture's central seam is **hosting-agnostic roles**: each role is a
Python `Protocol` (`src/va/roles/`), backends are interchangeable adapters
(`src/va/adapters/<role>/`), and a registry picks the adapter from config
(`config/roles.yaml` + `config/profiles/`). Swapping stub↔real↔remote is a config
edit, not a code change. Models load once via a shared `ModelManager` (Role 4 and
Role 11 literally share the same loaded Qwen instance).

| Layer | Where |
|---|---|
| Role interfaces (Protocols) | `src/va/roles/` |
| Adapters (stub + real per role) | `src/va/adapters/<role>/` |
| Config → adapter registry | `src/va/registry.py`, `src/va/configuration.py` |
| Pipelines (ingest, query, caption, transcript, objects, ask, deep_scan) | `src/va/pipeline/` |
| Storage: catalog + per-role tables (SQLite), vectors (numpy) | `src/va/storage/` |
| Pydantic contracts (incl. `QueryPlan`/`Evidence`/`Answer`) | `src/va/contracts/` |
| Sources (YouTube via yt-dlp, local files) | `src/va/sources/` |
| Model runtime (load-once cache, cuda→cpu fallback) | `src/va/runtime/` |
| Web UI (FastAPI + vanilla JS, no build step) | `src/va/web/` |
| CLI | `src/va/cli.py` |

> Contributors (human or agent): read [COORDINATION.md](COORDINATION.md) first —
> multiple agents work in this repo and that file is the ownership map + change log.
