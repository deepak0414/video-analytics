import numpy as np

from va.storage.vector.numpy_flat import NumpyFlatVectorStore


def test_nearest_neighbor_and_persist(tmp_path):
    store = NumpyFlatVectorStore(tmp_path / "vec")
    vecs = np.array([
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ], dtype=np.float32)
    store.add(vecs, [{"id": "x"}, {"id": "y"}, {"id": "z"}])
    store.persist()

    # query close to the second vector
    hits = store.search(np.array([0.1, 0.9, 0.0], dtype=np.float32), k=2)
    assert hits[0].payload["id"] == "y"
    assert hits[0].score > hits[1].score
    assert store.count() == 3

    # reload from disk preserves data
    store2 = NumpyFlatVectorStore(tmp_path / "vec")
    assert store2.count() == 3
    assert store2.search(np.array([0.9, 0.1, 0.0], dtype=np.float32), k=1)[0].payload["id"] == "x"


def test_empty_search_returns_nothing(tmp_path):
    store = NumpyFlatVectorStore(tmp_path / "v")
    assert store.search(np.array([1.0, 0.0]), k=5) == []
