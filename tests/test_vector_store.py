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


def test_shard_cache_reuses_and_invalidates(tmp_path):
    import os

    from va.storage.vector.sharded import ShardedVectorStore, clear_shard_cache

    clear_shard_cache()
    root = tmp_path / "videos"
    d = root / "vid1"
    d.mkdir(parents=True)
    s = NumpyFlatVectorStore(d / "vectors")
    s.add(np.array([[1.0, 0.0]], dtype=np.float32), [{"video_id": "a", "timestamp": 0.0}])
    s.persist()

    store = ShardedVectorStore(root)
    sh1 = store._shards()[0]
    sh2 = store._shards()[0]
    assert sh1 is sh2                      # reused from cache, not reloaded per call

    # a changed mtime (what a re-ingest rewrite produces) invalidates -> fresh load
    npz = d / "vectors.npz"
    st = npz.stat()
    os.utime(npz, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    sh3 = store._shards()[0]
    assert sh3 is not sh1                  # mtime changed -> reloaded

    clear_shard_cache()
    assert store._shards()[0] is not sh3   # cleared -> fresh load
