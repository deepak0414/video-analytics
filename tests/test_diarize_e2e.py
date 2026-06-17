"""End-to-end Role 9 path with the sidecar stubs: ingest a clip with both a
transcript sidecar (Role 8) and a diarization sidecar (Role 9); speakers get
joined onto transcript lines by temporal overlap and become searchable/filterable.
Deterministic, no model/network."""
import json

from va.adapters.speaker_diarizer.sidecar_inproc import sidecar_path as diar_sidecar
from va.adapters.speech_to_text.sidecar_inproc import sidecar_path as tx_sidecar
from va.contracts.diarization import SpeakerTurn
from va.contracts.transcript import TranscriptLine
from va.media.synth import write_color_video
from va.pipeline.diarize import assign_speakers
from va.pipeline.ingest import ingest
from va.pipeline.transcript import search_transcripts


def test_ingest_assigns_speakers_and_filters(tmp_path):
    video = write_color_video(tmp_path / "clip.mp4", [("red", (220, 30, 30), 4.0)], fps=10)
    tx_sidecar(str(video)).write_text(json.dumps({"lines": [
        {"start_time": 0.0, "end_time": 1.5, "text": "hello there"},
        {"start_time": 1.5, "end_time": 3.0, "text": "how are you"},
        {"start_time": 3.0, "end_time": 4.0, "text": "the budget is fine"},
    ]}))
    diar_sidecar(str(video)).write_text(json.dumps({"turns": [
        {"start_time": 0.0, "end_time": 1.5, "speaker": "SPEAKER_00"},
        {"start_time": 1.5, "end_time": 4.0, "speaker": "SPEAKER_01"},
    ]}))
    wd = str(tmp_path / ".va")

    res = ingest(str(video), workdir=wd, fps=1.0)
    assert res.transcript_lines == 3
    assert res.speakers == 2

    # speakers assigned by max temporal overlap
    assert search_transcripts("hello", workdir=wd)[0].speaker == "SPEAKER_00"
    assert search_transcripts("budget", workdir=wd)[0].speaker == "SPEAKER_01"

    # --speaker filter: "what did SPEAKER_01 say"
    only01 = search_transcripts("you", workdir=wd, speaker="SPEAKER_01")
    assert only01 and all(h.speaker == "SPEAKER_01" for h in only01)
    # the budget line belongs to SPEAKER_01, so filtering to 00 finds nothing
    assert search_transcripts("budget", workdir=wd, speaker="SPEAKER_00") == []


def test_transcript_without_diarization_keeps_speaker_none(tmp_path):
    # no diarization sidecar -> lines keep speaker=None, ingest still fine
    video = write_color_video(tmp_path / "clip.mp4", [("red", (220, 30, 30), 2.0)], fps=10)
    tx_sidecar(str(video)).write_text(json.dumps({"lines": [
        {"start_time": 0.0, "end_time": 2.0, "text": "solo narration"},
    ]}))
    wd = str(tmp_path / ".va")
    res = ingest(str(video), workdir=wd, fps=1.0)
    assert res.transcript_lines == 1 and res.speakers == 0
    assert search_transcripts("narration", workdir=wd)[0].speaker is None


def test_assign_speakers_overlap_and_empty():
    lines = [
        TranscriptLine(start_time=0.0, end_time=2.0, text="a"),
        TranscriptLine(start_time=2.0, end_time=4.0, text="b"),
    ]
    turns = [
        SpeakerTurn(start_time=0.0, end_time=1.8, speaker="A"),
        SpeakerTurn(start_time=1.8, end_time=4.0, speaker="B"),
    ]
    out = assign_speakers(lines, turns)
    assert out[0].speaker == "A"   # 0-2 overlaps A by 1.8 vs B by 0.2
    assert out[1].speaker == "B"   # 2-4 fully inside B
    # no turns -> unchanged copies, speaker stays None
    assert [ln.speaker for ln in assign_speakers(lines, [])] == [None, None]
