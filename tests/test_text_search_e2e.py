"""SR.2 — end-to-end semantic text index + search with the hash stub.

The hash embedder is lexical (not semantic), so this asserts the PLUMBING:
ingest → index all four text modalities → cosine search finds the right row,
the modality filter works, and removal cleans up the text shard. The
paraphrase-beats-word-overlap claim is a real-model golden fixture (bge-m3)."""
import json

from va.adapters.speech_to_text.sidecar_inproc import sidecar_path as tx_sidecar
from va.adapters.ocr.sidecar_inproc import sidecar_path as ocr_sidecar
from va.media.synth import write_color_video
from va.pipeline.ingest import ingest
from va.pipeline.text_search import search_text


def _ingest_with_text(tmp_path):
    video = write_color_video(tmp_path / "clip.mp4", [("red", (220, 30, 30), 4.0)], fps=10)
    tx_sidecar(str(video)).write_text(json.dumps({"lines": [
        {"start_time": 0.0, "end_time": 2.0, "text": "welcome to the meeting"},
        {"start_time": 2.0, "end_time": 4.0, "text": "let us discuss the quarterly budget"},
    ]}))
    ocr_sidecar(str(video)).write_text(json.dumps({"lines": [
        {"timestamp": 1.0, "text": "ACME CORP"},
    ]}))
    wd = str(tmp_path / ".va")
    return wd, ingest(str(video), workdir=wd, fps=1.0)


def test_text_index_built_and_searchable(tmp_path):
    wd, res = _ingest_with_text(tmp_path)
    # 2 transcript lines + 1 OCR + >=1 caption (color stub) all indexed
    assert res.text_vectors >= 3

    hits = search_text("budget", workdir=wd)
    assert hits and "budget" in hits[0].text.lower()
    assert hits[0].modality == "transcript"


def test_modality_filter(tmp_path):
    wd, _ = _ingest_with_text(tmp_path)
    only_tx = search_text("budget meeting", workdir=wd, modalities=["transcript"])
    assert only_tx and all(h.modality == "transcript" for h in only_tx)

    ocr_hits = search_text("acme corp", workdir=wd, modalities=["on_screen_text"])
    assert ocr_hits and ocr_hits[0].modality == "on_screen_text"


def test_removal_clears_text_shard(tmp_path):
    wd, res = _ingest_with_text(tmp_path)
    assert search_text("budget", workdir=wd)
    from va.pipeline.manage import remove_video
    remove_video(wd, str(res.video.id))
    assert search_text("budget", workdir=wd) == []
