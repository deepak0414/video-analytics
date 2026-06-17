# Golden Queries — SNL "Bar Cold Open" (multi-speaker dialogue)

- **source:** https://www.youtube.com/watch?v=7N68NjL9cMA (6:14, ingested to `.va-shots`)
- **why:** the project's **speech + speaker** regime video (Roles 8 + 9) — genuine multi-voice
  dialogue, chosen to exercise diarization. (Counting regimes are dresses/birdfeeder.)
- **machine-readable:** [7N68NjL9cMA.yaml](7N68NjL9cMA.yaml)

## ✅ Speakers (Role 9, pyannote.audio 4.x) — human-verified

**Ground truth (user, 2026-06-15): 5 distinct speakers.** In the final scene all 5 plus
non-speaking extras speak together in **chorus** (overlapping group speech).

| | distinct speakers |
|---|---|
| Ground truth (human) | **5** |
| pyannote auto (what a plain ingest produces) | 4 — under-counts by 1 |
| pyannote `num_speakers=5` hint | **5 — correct, balanced** (19–51 lines each) |
| `min_speakers=6`/`7` | still 5 — the audio's embeddings cap at 5; the hint is not a hard floor |

**Lesson (same as deep-scan counting):** a diarizer's speaker count is a *model estimate* with
a ceiling — validate it against ground truth, don't trust it blind. Auto-mode under-clusters
brief/overlapping/similar voices; a known-count hint recovers them up to what the audio supports.
Speaker *labels* (`SPEAKER_00`…) are arbitrary and unstable across runs — assert the count and
that lines get attributed, never "SPEAKER_0 said X".

## ✅ Transcript (Role 8, Whisper) — model-regression

`va transcript "world peace"` matches (bartender, ~16s). Dialogue isn't frame-verifiable, so
these pin the validated Whisper run, not absolute truth.

## How to run (speaker check, once the diarization-golden runner exists)

The runnable harness for diarization isn't built yet — it would re-ingest with the pyannote
backend (needs the gated-model chain + HF token, see CLAUDE.md) and count distinct speakers in
the `transcripts` table, asserting within `[4, 5]`. The fixture records the ground truth and the
auto-vs-hint behaviour for when it lands.
