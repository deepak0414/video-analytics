# Golden Query Fixtures

Ground-truth query/answer sets per video, used to test the search system end-to-end.
Each video has a human-readable `<video_id>.md` (queries + answers) and a machine-readable
`<video_id>.yaml` (assertions the test harness runs).

## How fixtures are generated (the "video-analyst" agent)

Claude has **no native video input**, but the agent generates these by:
1. Download the video (yt-dlp) and **sample frames** at a fixed interval.
2. A **vision agent** Reads the frames and proposes queries + answers across modalities.
3. An **adversarial verifier** agent independently re-checks each claim against the frames,
   keeping only confirmed items and pruning hallucinations / unverifiable audio/motion claims.

This is the `video-golden-queries` workflow. To generate fixtures for new videos: ingest
them first (so they're downloaded), extract frames, then run the workflow over them.

## File format (`<video_id>.yaml`)

```yaml
video_id / title / url / source_key
default_min_score: 0.05      # threshold separating relevant from irrelevant (calibrate!)
queries:                     # RUNNABLE NOW (Role 2 visual retrieval)
  - {id, query, expect: match|no_match, time_range: [start_s, end_s]}
future_queries:              # captured, needs later roles (OCR, object-count, action, …)
  - {query, needs_role, modality}
```

**`verify: true`** (optional, per query) — route this query through the **SR.6 VLM
verifier**: visual hits are re-checked frame-by-frame by Qwen2.5-VL (drops SigLIP
attribute/composition false-positives like "blue Ferrari"); an empty object result falls
back to VLM presence (finds the "snake" YOLO can't). Set it ONLY for queries that hit
SigLIP/YOLO's known weaknesses — blanket verification erodes recall on weak true-positives
(measured: it dropped a distant-grandstands match). In production the Role-11 planner sets
this; here the fixture declares it.

## Assertion semantics (what the harness checks)

For each video, ingest it into its **own** workdir with the real models, then per query.
Each query has a `modality` (default `visual`) deciding which command runs it and what
match/no_match means:

| modality | runs | `match` passes when | `no_match` passes when |
|---|---|---|---|
| `visual` (Role 2) | `va query` | top score ≥ `min_score` (opt.: timestamp in `time_range`) | top score < `min_score` |
| `caption` (Role 4) | `va caption` | a hit's caption matches the concept (opt.: in `time_range`) | no hits |
| `transcript` (Role 8) | `va transcript` | a hit contains the queried words (opt.: in `time_range`) | no hits |
| `object` (Role 5) | `va objects` | a summary row exists for the class | no summary row |
| `on_screen_text` (Role 10) | `va ocr` | a hit's text contains the queried words (opt.: in `time_range`) | no hits |
| `action` (Role 7) | `va actions` | an event's label matches the queried action (opt.: in `time_range`) | no hits |
| `object_count` (Role 6) | `va count` | distinct count within `[count_min, count_max]` | no row (count 0) |

`min_score` (visual only) defaults to 0.05 (SigLIP relevant ~0.11–0.18, irrelevant ~0/neg)
but **must be calibrated on a first real run** and may be tuned per query.

**`ask_questions` (Role 11 + deep scan) — RUNNABLE TODAY** via the gated harness
`tests/test_golden_ask.py`: each entry runs `ask(question)` and asserts the named
CODE-COUNTED statistic (`total_episodes`, `distinct_states`, …) lies within
`[expected_min, expected_max]`. The narrator's prose is never asserted — only the
deterministic numbers. Provenance `human-verified` = the user hand-counted the truth.

```bash
RUN_GOLDEN=1 VA_CONFIG_DIR=run-claude/config GOLDEN_WORKDIR=.va-shots \
    .venv/bin/pytest -m golden -q
```
Skipped automatically without `RUN_GOLDEN=1` (needs GPU models, the claude CLI, and an
ingested workdir); individual questions skip if their video isn't in the workdir.

**Provenance labels** (`provenance:` per query):
- `vision-verified` — ground truth independently confirmed from frames by the adversarial
  workflow. Strong: tests *accuracy*.
- `model-regression` — pins behavior we observed from a validated real-model run (e.g.
  Whisper transcript lines we didn't independently hear). Weaker: tests *regressions*,
  not absolute truth. Use when frames can't verify the fact (audio).

## Status

- ✅ Fixtures generated + verified for `GXPRSFL0UUA` (Ferrari), `xDerjsxFkb4` (cobra),
  and `eiLeBJUf1iE` (27 Dresses movie clip).
- ✅ 2026-06-10: caption/transcript/object queries promoted from `future_queries` to
  runnable for the implemented Roles 4/5/8, with `modality` + `provenance` labels.
  `cobra-obj-01` ("snake") is a deliberate known-hard accuracy target (now carries `xfail`).
- ✅ 2026-06-11: `birdfeeder_0413_1405` fixture added (long-take regime); **ask-level
  golden questions** (dress changes = 17, bird visits = 4-5, both human-verified) +
  the gated `pytest -m golden` harness that actually runs them.
- ✅ 2026-06-15: `7N68NjL9cMA` (SNL Bar Cold Open) added — the **speech + speaker regime**
  video (Roles 8/9). Human truth = 5 distinct speakers; documents that pyannote auto
  *under-counts* to 4 and a `num_speakers=5` hint recovers the balanced 5 (a `diarization`
  block, new fixture shape). The runnable diarization-count harness isn't built yet.
- ✅ 2026-06-16: the **runnable per-modality harness** `tests/test_golden_queries.py` is
  built and gated (`pytest -m golden`, same env as the ask harness). It consumes the
  `queries:`, `semantic_text:` (SR.1/SR.2), and `diarization:` (Role 9) blocks; visual match
  = "strongest hit *inside* `time_range` ≥ `min_score`" (multi-instance concepts can peak
  elsewhere). First-real-run calibration set `default_min_score` 0.05 → **0.10** (true-neg
  ≤0.044, true-pos ≥0.108; matches the SR.5 `min_cosine`). **78→79 pass, 5 xfail, 0 fail**
  over `.va-shots`. Documented model limitations carry `xfail: "<reason>"` (strict — an
  improved model xpasses → red alert): SigLIP color-negation (`ferrari-neg-05`), SigLIP
  compositional/dominant-noun (`cobra-neg-03`), SigLIP weak retrieval (`cobra-pos-07`,
  `dresses-pos-04`), YOLO vocab gap (`cobra-obj-01`). Running it also **caught an SR.5
  regression**: the relevance gate emptied evidence and starved the deep-scan target
  resolver — fixed by stashing the pre-gate dominant video.
- ⏳ The diarization block is checked by reading the existing transcripts table's distinct
  speaker count; a *re-ingest-with-hint* harness (auto-4 vs `num_speakers=5`) is not built.
- Each video keeps its `future_queries` so the test set grows as Roles 1/4/5/7/8/10 land.
