"""Schema-evolution tests for the runtime contracts (QueryPlan / Evidence).

The point: these payloads flow between learned components and will change over
time. Adding a field must not break old readers; a missing/removed field must
not break parsing; unknown fields must survive a round-trip.
"""
import json
from uuid import uuid4

from va.contracts.evidence import Evidence, EvidenceItem
from va.contracts.query_plan import Answer, QueryPlan


def test_plan_parses_with_missing_fields():
    # an old/minimal payload: only one flag present
    plan = QueryPlan.model_validate({"query": "q", "needs_transcript_search": True})
    assert plan.needs_transcript_search is True
    assert plan.needs_caption_search is False     # default fills the gap
    assert plan.schema_version == 1


def test_plan_preserves_unknown_fields_round_trip():
    # a NEWER producer emits a flag and a knob we don't know yet
    payload = {
        "query": "q",
        "needs_caption_search": True,
        "needs_ocr_search": True,          # future tier
        "rerank_strategy": "fusion-v2",    # future knob
    }
    plan = QueryPlan.model_validate(payload)
    dumped = json.loads(plan.model_dump_json())
    assert dumped["needs_ocr_search"] is True          # not dropped
    assert dumped["rerank_strategy"] == "fusion-v2"    # not dropped
    assert plan.needs_caption_search is True


def test_evidence_item_attributes_and_extras():
    item = EvidenceItem.model_validate({
        "modality": "ocr",                  # novel modality string is fine
        "video_id": str(uuid4()),
        "time_start": 1.0, "time_end": 2.0,
        "content": "STOP sign",
        "attributes": {"bbox": [0.1, 0.2, 0.3, 0.4]},   # role-specific payload
        "saliency": 0.9,                    # unknown future field
    })
    dumped = json.loads(item.model_dump_json())
    assert dumped["attributes"]["bbox"] == [0.1, 0.2, 0.3, 0.4]
    assert dumped["saliency"] == 0.9


def test_evidence_bundle_round_trip():
    ev = Evidence(query="q", items=[EvidenceItem(modality="visual", content="x")])
    again = Evidence.model_validate_json(ev.model_dump_json())
    assert again.items[0].modality == "visual"


def test_answer_defaults_and_citations():
    a = Answer.model_validate({"text": "3 nuts", "citations": [[str(uuid4()), 12.5]]})
    assert a.citations[0][1] == 12.5
    assert Answer().text == ""  # everything has defaults
