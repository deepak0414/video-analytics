"""SR.1 — the text-embedder role (Retrieval Layer). Tests the hash stub's
lexical-retrieval behaviour + the registry wiring, deterministically (no model)."""
import numpy as np

from va.adapters.text_embedder.hash_inproc import HashTextEmbedder
from va.registry import get_text_embedder


def _cos(a, b):
    return float(a @ b)  # vectors are L2-normalized


def test_hash_embedder_shape_and_norm():
    emb = HashTextEmbedder()
    v = emb.embed(["the quarterly budget", "a red sports car"])
    assert v.shape == (2, emb.dim) and v.dtype == np.float32
    assert np.allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-5)


def test_hash_embedder_is_deterministic():
    a = HashTextEmbedder().embed(["hello world"])
    b = HashTextEmbedder().embed(["hello world"])
    assert np.array_equal(a, b)


def test_shared_words_score_higher_than_disjoint():
    emb = HashTextEmbedder()
    q = emb.embed(["the budget meeting"])[0]
    near = emb.embed(["we discussed the budget"])[0]   # shares "the", "budget"
    far = emb.embed(["a cat sat on a mat"])[0]          # no shared words
    assert _cos(q, near) > _cos(q, far)
    assert _cos(q, near) > 0.1


def test_retrieval_picks_right_document():
    emb = HashTextEmbedder()
    docs = ["birds feeding at the feeder", "a red ferrari on the track",
            "the quarterly budget report"]
    doc_vecs = emb.embed(docs)
    qv = emb.embed(["how much was the budget"])[0]
    best = int(np.argmax(doc_vecs @ qv))
    assert docs[best] == "the quarterly budget report"


def test_registry_default_is_hash_stub():
    emb = get_text_embedder()
    assert type(emb).__name__ == "HashTextEmbedder" and emb.dim > 0
