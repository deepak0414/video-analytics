# Golden Queries — Birdfeeder clip (fixed-camera long take)

- **source:** section **4:13–14:05** of https://www.youtube.com/watch?v=2Oy4Fy8vIgw (8-hour stream)
- **identity:** content sha256 `8970ef1c1923…` (local-file ingest of the cut section)
- **duration:** 592s · 854×480 · single 592s take (1 segment)
- **machine-readable assertions:** [birdfeeder_0413_1405.yaml](birdfeeder_0413_1405.yaml)

> This is the project's **long-take regime** cross-validation video (the dress clip covers
> the montage regime). Downloading the section needs full ffmpeg+ffprobe on PATH
> (`static-ffmpeg` pip package; yt-dlp's `ffmpeg_location` is ignored for range downloads).

## ✅ Ask-level golden (Role 11 + deep scan) — run via `pytest -m golden`

| question | statistic | expected | provenance |
|---|---|---|---|
| **"How many birds come and feed on the feeder?"** | `total_episodes` (visits) | **4–5** | **human-verified** (user hand count; the 4-vs-5 ambiguity is a single-frame cardinal touch-and-go @0:18) |

System history: 5 visits / 3 distinct types measured by **four independent sweeps** with
different label vocabularies. Known traps this fixture guards against: tracker "distinct
instances" (~9–10, no re-ID across visits) is NOT the answer; per-sample label flicker
("brown speckled"/"brown striped" = one sparrow) must debounce/normalize away.

## ✅ Visual retrieval (Role 2)

match: "bird eating seeds on a feeder" (throughout), "a tractor" (yellow loader in
background — frame-verified). no_match: "red sports car", "people in an office".

## How to run

```bash
RUN_GOLDEN=1 VA_CONFIG_DIR=run-claude/config GOLDEN_WORKDIR=.va-shots \
    .venv/bin/pytest -m golden -q
```
