from uuid import uuid4

from va.adapters.reasoner.prompts import parse_json_block
from va.adapters.reasoner.rule_inproc import RuleReasoner
from va.contracts.detection import Detection
from va.contracts.evidence import Evidence, EvidenceItem
from va.roles.reasoner import Reasoner
from va.storage.structured.detections import DetectionStore


def test_rule_planner_heuristics():
    r = RuleReasoner()
    assert isinstance(r, Reasoner)

    p = r.plan("what did they say about the budget?")
    assert p.needs_transcript_search is True

    p = r.plan("how many distinct cars appear?")
    assert p.needs_object_query is True

    p = r.plan("what's the color of the t-shirt of the person entering the red car?")
    assert p.needs_vlm_reasoning is True
    assert p.needs_object_query is True
    assert p.needs_action_query is True       # "entering"
    assert "person" in p.search_terms and "car" in p.search_terms
    assert "what" not in p.search_terms        # question words stripped


def test_rule_reasoner_extracts_and_cites():
    vid = uuid4()
    ev = Evidence(query="q", items=[
        EvidenceItem(modality="object_count", video_id=vid, time_start=1.0,
                     time_end=5.0, content="2 distinct 'car'", score=1.0),
        EvidenceItem(modality="visual", video_id=vid, time_start=3.0,
                     time_end=3.0, content="visual match", score=0.5),
    ])
    ans = RuleReasoner().reason("how many cars?", ev)
    assert ans.citations and ans.citations[0][0] == vid
    assert any("2 distinct" in i["statement"] for i in ans.attributes["items"])


def test_render_evidence_balances_modalities():
    from va.adapters.reasoner.prompts import render_evidence

    vid = uuid4()
    items = [EvidenceItem(modality="visual", video_id=vid, time_start=float(t),
                          time_end=float(t), content=f"v{t}", score=0.5)
             for t in range(25)]
    items += [EvidenceItem(modality="caption", video_id=vid, time_start=float(t),
                           time_end=float(t), content=f"c{t}", score=0.5)
              for t in range(25)]
    text = render_evidence(Evidence(query="q", items=items), max_items=20)
    # round-robin: captions must NOT be crowded out by the visual flood
    assert "(caption" in text and "(visual" in text
    assert text.count("(caption") == 10 and text.count("(visual") == 10


def test_coerce_timestamp_tolerates_llm_formats():
    from va.adapters.reasoner.prompts import coerce_timestamp

    assert coerce_timestamp(3.5) == 3.5
    assert coerce_timestamp("3.5") == 3.5
    assert coerce_timestamp("3.5s") == 3.5      # the real Qwen output that broke us
    assert coerce_timestamp("1:07") == 67.0
    assert coerce_timestamp("01:02:03") == 3723.0
    assert coerce_timestamp("n/a") is None
    assert coerce_timestamp(None) is None


def test_parse_json_block_tolerates_fences_and_prose():
    assert parse_json_block('bla bla ```json\n{"a": 1}\n``` more') == {"a": 1}
    assert parse_json_block("no json here") is None
    assert parse_json_block('{"nested": {"b": 2}} trailing') == {"nested": {"b": 2}}


def test_co_occurrence_window(tmp_path):
    vid = uuid4()
    store = DetectionStore(tmp_path / "c.db")
    mk = lambda ts, cls: Detection(
        video_id=vid, timestamp=ts, object_class=cls, confidence=0.9,
        bbox_x=0.1, bbox_y=0.1, bbox_w=0.2, bbox_h=0.2,
    )
    # person+car together at t=1,2,3; person alone at t=10; both again at t=20
    dets = [mk(t, "person") for t in (1.0, 2.0, 3.0, 10.0, 20.0)]
    dets += [mk(t, "car") for t in (1.0, 2.0, 3.0, 20.0)]
    store.replace_detections(vid, dets)

    windows = store.co_occurrence(["person", "car"])
    assert len(windows) == 2
    best = windows[0]                       # sorted by frames desc
    assert (best.time_start, best.time_end, best.frames) == (1.0, 3.0, 3)
    assert store.co_occurrence(["person"]) == []   # needs >=2 classes


def test_qwen_plan_salvages_wrong_typed_fields():
    """Real failure (2026-06-11, web ask 500): Qwen emitted well-formed JSON
    with params as a *string* — ValidationError escaped plan() and crashed the
    whole ask. The salvage path drops offending fields; only an unsalvageable
    doc falls back to the rule planner. Offline: model never loaded."""
    from va.adapters.reasoner.qwen_inproc import QwenReasoner

    r = QwenReasoner.__new__(QwenReasoner)   # skip __init__ (no GPU/model)
    r._fallback = RuleReasoner()

    r._chat = lambda prompt, images=(): (
        '{"params": "person\'s dress", "needs_caption_search": true}'
    )
    p = r.plan("who is wearing a green dress?")
    assert p.query == "who is wearing a green dress?"
    assert p.needs_caption_search is True     # valid fields survive the salvage
    assert p.params == {}                     # offending field dropped

    r._chat = lambda prompt, images=(): '{"search_terms": 42}'
    p = r.plan("what did they say about the budget?")
    assert p.search_terms is None             # dropped; defaults make it valid
    assert p.query == "what did they say about the budget?"
