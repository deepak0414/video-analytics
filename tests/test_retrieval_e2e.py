"""SR.4 — the retriever orchestrator (Retrieval Layer).

Two levels, both offline/deterministic with the stub backends:
  - `_fuse` unit tests: the cross-modal ranking math, on hand-built items, with
    the word-overlap reranker (so a language match outranks a stronger-cosine
    frame, and visual items rank by cosine alone).
  - e2e: ingest a multi-modal clip, run a plan through `retrieve`, assert the
    right moment surfaces first ACROSS modalities — the whole point of fusion.
"""
import json

from va.adapters.ocr.sidecar_inproc import sidecar_path as ocr_sidecar
from va.adapters.reranker.wordoverlap_inproc import WordOverlapReranker
from va.adapters.speech_to_text.sidecar_inproc import sidecar_path as tx_sidecar
from va.contracts.evidence import EvidenceItem
from va.contracts.query_plan import QueryPlan
from va.media.synth import write_color_video
from va.pipeline.ingest import ingest
from va.pipeline.retrieval import RelevanceGate, _fuse, _minmax, retrieve


# --- _fuse unit tests (no ingest, no models) --------------------------------

def _items():
    return [
        EvidenceItem(modality="transcript", content="the quarterly budget report", score=0.90),
        EvidenceItem(modality="caption", content="a red ferrari on a road", score=0.80),
        EvidenceItem(modality="visual", content="visual match at 3.0s", score=0.99),
    ]


def test_fuse_language_match_beats_stronger_cosine_frame():
    items = _items()
    _fuse("budget", items, WordOverlapReranker())
    # transcript wins despite the visual frame having the highest raw cosine,
    # because the reranker scores its language and the frame carries none.
    assert items[0].modality == "transcript"
    # the visual frame is rerank-blind -> no rerank score, ranks on cosine only
    visual = next(i for i in items if i.modality == "visual")
    assert visual.attributes["rerank_score"] is None
    # every item carries a fused ordering key, and the winner the top one
    assert all("fused_score" in i.attributes for i in items)
    assert items[0].attributes["fused_score"] == max(i.attributes["fused_score"] for i in items)
    # the language winner's raw rerank score is preserved for SR.5 thresholding
    assert items[0].attributes["rerank_score"] == 1.0


def test_fuse_degrades_when_reranker_raises():
    class Boom:
        def rerank(self, q, cands):
            raise RuntimeError("no model")

    items = _items()
    _fuse("budget", items, Boom())
    # no crash; ordering falls back to native cosine and the gap is noted
    assert all("fused_score" in i.attributes for i in items)
    assert any("rerank skipped" in (i.attributes.get("rerank_note") or "") for i in items)
    # native-only ordering preserves rank WITHIN a lane (raw SigLIP vs bge cosines
    # are never compared cross-lane): the higher-cosine text item leads the text.
    order = [i.modality for i in items]
    assert order.index("transcript") < order.index("caption")


def test_minmax_degenerate_collapses_to_half():
    # a single candidate / an all-equal set carries no relative signal -> 0.5,
    # not a fabricated 1.0 (so it can't masquerade as a confident top hit).
    assert _minmax([0.7]) == [0.5]
    assert _minmax([0.4, 0.4, 0.4]) == [0.5, 0.5, 0.5]
    assert _minmax([0.0, 1.0]) == [0.0, 1.0]


# --- SR.5 relevance gate ------------------------------------------------------

def test_gate_default_is_permissive():
    g = RelevanceGate()
    assert not g.active
    # keeps anything regardless of score
    assert g.keeps(EvidenceItem(modality="visual", score=-0.9))
    assert g.keeps(EvidenceItem(modality="transcript", attributes={"rerank_score": -99.0}))


def test_gate_thresholds_per_signal():
    g = RelevanceGate(min_rerank=-3.0, min_cosine=0.05)
    assert g.active
    # visual gated by raw cosine
    assert g.keeps(EvidenceItem(modality="visual", score=0.12))
    assert not g.keeps(EvidenceItem(modality="visual", score=0.0))
    # language gated by cross-encoder logit — a relevant-but-terse -1.51 survives,
    # clearly-irrelevant -4.9 does not (the gap-not-sign threshold)
    assert g.keeps(EvidenceItem(modality="caption", attributes={"rerank_score": -1.51}))
    assert not g.keeps(EvidenceItem(modality="transcript", attributes={"rerank_score": -4.91}))
    # a language item the reranker couldn't score -> kept (no read, don't guess)
    assert g.keeps(EvidenceItem(modality="caption", attributes={"rerank_score": None}))


def test_retrieve_gate_makes_no_match_empty(tmp_path):
    video = write_color_video(tmp_path / "clip.mp4", [("red", (220, 30, 30), 4.0)], fps=10)
    tx_sidecar(str(video)).write_text(json.dumps({"lines": [
        {"start_time": 2.0, "end_time": 4.0, "text": "let us discuss the quarterly budget"},
    ]}))
    wd = str(tmp_path / ".va")
    ingest(str(video), workdir=wd, fps=1.0)

    # Explicit test gate: min_rerank=0.5 (word-overlap stub: an exact term match
    # scores 1.0, a no-overlap query 0.0); min_cosine high to drop the
    # uninformative stub visual frames so the language gate is what's exercised.
    gate = RelevanceGate(min_rerank=0.5, min_cosine=0.99)
    plan = QueryPlan(query="budget", needs_transcript_search=True)

    hit = retrieve(plan, workdir=wd, k=5, gate=gate)
    assert hit.items and hit.items[0].modality == "transcript"

    miss = retrieve(QueryPlan(query="helicopter aviation", needs_transcript_search=True),
                    workdir=wd, k=5, gate=gate)
    assert miss.items == []                                  # "no match" is real
    assert any("no match" in n for n in miss.notes)          # and it says so


# --- e2e: cross-modal fusion over a real (stub-ingested) clip ----------------

def test_retrieve_ranks_right_moment_first_across_modalities(tmp_path):
    video = write_color_video(tmp_path / "clip.mp4", [("red", (220, 30, 30), 4.0)], fps=10)
    tx_sidecar(str(video)).write_text(json.dumps({"lines": [
        {"start_time": 0.0, "end_time": 2.0, "text": "welcome to the meeting"},
        {"start_time": 2.0, "end_time": 4.0, "text": "let us discuss the quarterly budget"},
    ]}))
    ocr_sidecar(str(video)).write_text(json.dumps({"lines": [
        {"timestamp": 1.0, "text": "ACME CORP"},
    ]}))
    wd = str(tmp_path / ".va")
    ingest(str(video), workdir=wd, fps=1.0)

    plan = QueryPlan(
        query="budget",
        needs_transcript_search=True,
        needs_caption_search=True,
        needs_ocr_search=True,
    )
    ev = retrieve(plan, workdir=wd, k=5)

    assert ev.items
    # visual (Tier 1) always gathered, but the budget transcript line wins the
    # fused ranking over the frame hits and the unrelated caption/OCR.
    assert {i.modality for i in ev.items} >= {"visual", "transcript"}
    assert ev.items[0].modality == "transcript"
    assert "budget" in ev.items[0].content.lower()
    # fusion metadata is recorded for transparency
    assert ev.attributes["fusion"]["rerank_weight"] == 0.6
