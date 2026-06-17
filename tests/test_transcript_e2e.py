"""End-to-end Role 8 path with the sidecar stub: ingest a clip that has a
sidecar transcript, then search what was said. Deterministic, no model/network."""
import json

from va.adapters.speech_to_text.sidecar_inproc import sidecar_path
from va.media.synth import write_color_video
from va.pipeline.ingest import ingest
from va.pipeline.transcript import search_transcripts


def test_ingest_transcribes_and_searches(tmp_path):
    video = write_color_video(tmp_path / "clip.mp4", [("red", (220, 30, 30), 3.0)], fps=10)
    sidecar_path(str(video)).write_text(json.dumps({"lines": [
        {"start_time": 0.0, "end_time": 1.5, "text": "welcome to the meeting"},
        {"start_time": 1.5, "end_time": 3.0, "text": "let us discuss the quarterly budget"},
    ]}))
    wd = str(tmp_path / ".va")

    res = ingest(str(video), workdir=wd, fps=1.0)
    assert res.transcript_lines == 2

    hits = search_transcripts("budget", workdir=wd)
    assert hits and "budget" in hits[0].text
    assert hits[0].start_time == 1.5

    assert search_transcripts("nonexistent topic xyz", workdir=wd) == []
