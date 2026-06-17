"""SR.3 — the reranker role (Retrieval Layer). Tests the word-overlap stub's
ordering behaviour + registry wiring, deterministically (no model)."""
from va.adapters.reranker.wordoverlap_inproc import WordOverlapReranker
from va.registry import get_reranker


def test_rerank_scores_align_and_order():
    r = WordOverlapReranker()
    cands = ["a cat on a mat", "the quarterly budget report", "a red ferrari"]
    scores = r.rerank("how much was the budget", cands)
    assert len(scores) == len(cands)                 # aligned to input order
    assert scores[1] == max(scores)                  # the budget doc wins
    # reorder by score -> budget doc first
    ranked = [c for _, c in sorted(zip(scores, cands), reverse=True)]
    assert ranked[0] == "the quarterly budget report"


def test_rerank_deterministic_and_empty():
    r = WordOverlapReranker()
    assert r.rerank("budget", ["the budget", "a cat"]) == r.rerank("budget", ["the budget", "a cat"])
    assert r.rerank("anything", []) == []
    assert r.rerank("", ["x", "y"]) == [0.0, 0.0]    # empty query -> no signal


def test_registry_default_is_wordoverlap_stub():
    assert type(get_reranker()).__name__ == "WordOverlapReranker"
