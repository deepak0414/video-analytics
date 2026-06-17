import json
from uuid import uuid4

import pytest

from va.adapters.speech_to_text.sidecar_inproc import SidecarSTT, sidecar_path
from va.contracts.transcript import TranscriptLine
from va.roles.speech_to_text import SpeechToText
from va.storage.structured.transcripts import TranscriptStore


def test_transcript_line_contract():
    ln = TranscriptLine(start_time=1.0, end_time=2.5, text="hello", speaker="A")
    assert ln.text == "hello"
    with pytest.raises(Exception):
        TranscriptLine(start_time=3.0, end_time=1.0, text="bad")


def test_store_roundtrip_and_word_overlap_search(tmp_path):
    vid = uuid4()
    store = TranscriptStore(tmp_path / "catalog.db")
    store.replace_transcripts(vid, [
        TranscriptLine(start_time=0.0, end_time=2.0, text="we discussed the annual budget today"),
        TranscriptLine(start_time=2.0, end_time=4.0, text="then we talked about the weather"),
    ])
    assert store.count(vid) == 2

    hits = store.search("budget")
    assert len(hits) == 1
    assert "budget" in hits[0].text and hits[0].start_time == 0.0

    # ranks by word overlap: "the weather" fully matches line 2, partially line 1
    ranked = store.search("the weather")
    assert ranked[0].start_time == 2.0  # line 2 ("...the weather") scores higher

    # replace is idempotent
    store.replace_transcripts(vid, [TranscriptLine(start_time=0.0, end_time=1.0, text="only one now")])
    assert store.count(vid) == 1


def test_sidecar_backend_reads_sidecar(tmp_path):
    stt = SidecarSTT()
    assert isinstance(stt, SpeechToText)
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"")  # placeholder; sidecar backend ignores audio
    # no sidecar -> empty
    assert stt.transcribe(str(media)) == []
    # with sidecar -> parsed lines
    sidecar_path(str(media)).write_text(json.dumps({"lines": [
        {"start_time": 0.0, "end_time": 1.5, "text": "mention the budget"},
    ]}))
    lines = stt.transcribe(str(media))
    assert len(lines) == 1 and lines[0].text == "mention the budget"
