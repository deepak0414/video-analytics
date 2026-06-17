"""End-to-end Role 10 path with the sidecar stub: ingest a clip that has a
sidecar OCR file, then search the on-screen text. Deterministic, no model/network."""
import json

from va.adapters.ocr.sidecar_inproc import sidecar_path
from va.adapters.reasoner.rule_inproc import RuleReasoner
from va.contracts.query_plan import QueryPlan
from va.media.synth import write_color_video
from va.pipeline.evidence import assemble
from va.pipeline.ingest import ingest
from va.pipeline.ocr import search_ocr

_LINES = {"lines": [
    {"timestamp": 0.5, "text": "COORS LIGHT",
     "bbox_x": 0.1, "bbox_y": 0.1, "bbox_w": 0.3, "bbox_h": 0.1},
    {"timestamp": 1.0, "text": "COORS LIGHT",
     "bbox_x": 0.1, "bbox_y": 0.1, "bbox_w": 0.3, "bbox_h": 0.1},
    {"timestamp": 2.0, "text": "27 Dresses"},
]}


def _ingest_clip(tmp_path):
    video = write_color_video(tmp_path / "clip.mp4", [("red", (220, 30, 30), 3.0)], fps=10)
    sidecar_path(str(video)).write_text(json.dumps(_LINES))
    wd = str(tmp_path / ".va")
    return wd, ingest(str(video), workdir=wd, fps=1.0)


def test_ingest_reads_and_searches_on_screen_text(tmp_path):
    wd, res = _ingest_clip(tmp_path)
    assert res.ocr_lines == 3

    hits = search_ocr("coors light billboard", workdir=wd)
    assert hits and hits[0].text == "COORS LIGHT"
    # the two sightings of identical text collapse into one spanning hit
    assert hits[0].sightings == 2
    assert (hits[0].time_start, hits[0].time_end) == (0.5, 1.0)

    title = search_ocr("dresses title card", workdir=wd)
    assert title and title[0].text == "27 Dresses"

    assert search_ocr("nonexistent xyz", workdir=wd) == []


def test_rule_planner_flags_ocr_and_evidence_assembles_it(tmp_path):
    wd, _ = _ingest_clip(tmp_path)

    # Word-overlap retrieval: the query must name the text it's looking for
    # (same semantics as transcript search; "what does the billboard say" with
    # no quoted text is a VLM/caption concern, not an OCR-index lookup).
    plan = RuleReasoner().plan("coors light billboard text")
    assert plan.needs_ocr_search

    ev = assemble(plan, workdir=wd)
    ocr_items = [i for i in ev.items if i.modality == "on_screen_text"]
    assert ocr_items and "COORS LIGHT" in ocr_items[0].content
    assert ocr_items[0].source_role == 10


def test_search_is_space_insensitive(tmp_path):
    """OCR merges words it read correctly ('Coors Light' -> 'COOrSLIGHT' on the
    Ferrari billboard); the collapsed-phrase match must still find it."""
    from uuid import uuid4

    from va.contracts.ocr import OcrLine
    from va.pipeline.paths import Workspace
    from va.storage.structured.ocr import OcrStore

    store = OcrStore(Workspace(str(tmp_path / ".va")).catalog_db)
    vid = uuid4()
    # the videos row doesn't matter for search; ocr_results has no FK enforcement
    store.replace_lines(vid, [OcrLine(timestamp=35.0, text="COOrSLIGHT")])
    hits = store.search("coors light")
    assert hits and hits[0].score == 1.0
    # 1-3 char collapsed queries must NOT phrase-match into longer strings
    assert store.search("ors") == []
    store.close()


def test_plan_without_ocr_flag_skips_tier(tmp_path):
    wd, _ = _ingest_clip(tmp_path)
    ev = assemble(QueryPlan(query="red things"), workdir=wd)
    assert not [i for i in ev.items if i.modality == "on_screen_text"]
