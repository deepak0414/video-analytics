import numpy as np
from PIL import Image

from va.adapters.visual_embedder.hash_inproc import HashEmbedder
from va.registry import get_visual_embedder
from va.roles.visual_embedder import VisualEmbedder


def _solid(color):
    return Image.new("RGB", (32, 32), color)


def test_hash_embedder_satisfies_protocol_and_is_unit_norm():
    emb = HashEmbedder(dim=64)
    assert isinstance(emb, VisualEmbedder)
    v = emb.embed_image([_solid((220, 30, 30))])
    assert v.shape == (1, 64) and v.dtype == np.float32
    assert abs(np.linalg.norm(v[0]) - 1.0) < 1e-5


def test_text_query_matches_same_color_frame_best():
    emb = HashEmbedder()
    frames = emb.embed_image([
        _solid((220, 30, 30)),   # red
        _solid((30, 180, 30)),   # green
        _solid((30, 30, 220)),   # blue
    ])
    q = emb.embed_text(["red sports car"])[0]
    scores = frames @ q
    assert int(np.argmax(scores)) == 0           # red frame wins
    assert scores[0] > 0.99                       # near-identical vector
    assert scores[0] > scores[1] and scores[0] > scores[2]


def test_registry_returns_hash_backend_by_default():
    emb = get_visual_embedder()
    assert isinstance(emb, HashEmbedder)
