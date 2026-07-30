"""Vector-shard identity tags (WS-1 §6-b — TAG-1/TAG-2): every per-video shard
records the embedder + dim that produced it, so a later query-time guard can refuse
to mix vector spaces (the stub-64 vs SigLIP-1152 trap in CLAUDE.md). Offline: stub
embedder + synth color clips, no GPU/network.
"""
import numpy as np

from va.media.synth import write_color_video
from va.pipeline.ingest import ingest
from va.pipeline.paths import Workspace
from va.storage.structured.catalog_sqlite import Catalog
from va.storage.vector.numpy_flat import NumpyFlatVectorStore


# --- unit: the store carries + round-trips its tag ------------------------

def test_shard_meta_round_trips(tmp_path):
    store = NumpyFlatVectorStore(tmp_path / "vectors")
    store.add(np.random.rand(3, 8).astype(np.float32), [{"i": i} for i in range(3)])
    store.set_meta({"embedder": "siglip"})
    store.persist()

    reopened = NumpyFlatVectorStore(tmp_path / "vectors")
    assert reopened.meta == {"embedder": "siglip", "dim": 8}   # dim filled from the array


def test_untagged_shard_loads_meta_none(tmp_path):
    store = NumpyFlatVectorStore(tmp_path / "vectors")
    store.add(np.random.rand(2, 4).astype(np.float32), [{"i": 0}, {"i": 1}])
    store.persist()                                    # no set_meta -> a pre-tagging shard
    assert NumpyFlatVectorStore(tmp_path / "vectors").meta is None


def test_empty_shard_still_tags_embedder_dim_none(tmp_path):
    store = NumpyFlatVectorStore(tmp_path / "vectors")
    store.set_meta({"embedder": "hash"})               # tagged but nothing added
    store.persist()
    assert NumpyFlatVectorStore(tmp_path / "vectors").meta == {"embedder": "hash", "dim": None}


# --- integration: ingest stamps both shards -------------------------------

def _clip(tmp_path):
    return write_color_video(
        tmp_path / "clip.mp4",
        [("red", (220, 30, 30), 2.0), ("green", (30, 180, 30), 2.0)],
        fps=10,
    )


def _video_dir(wd, video_id):
    ws = Workspace(wd)
    cat = Catalog(ws.catalog_db)
    try:
        v = cat.get(video_id)
    finally:
        cat.close()
    return ws.video_dir(v.source_key, v.title)


def test_ingest_stamps_visual_shard_with_embedder_and_dim(tmp_path):
    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)

    meta = NumpyFlatVectorStore(_video_dir(wd, res.video.id) / "vectors").meta
    assert meta is not None
    assert meta["embedder"] == "hash"      # the default stub visual embedder
    assert meta["dim"] == 64               # stub dim (CLAUDE.md)


def test_ingest_stamps_text_shard(tmp_path):
    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)

    meta = NumpyFlatVectorStore(_video_dir(wd, res.video.id) / "text_vectors").meta
    assert meta is not None
    assert meta["embedder"] == "hash"      # the default stub text embedder


class _FakeEmbedder:
    """Stand-in for an injected embedder (as a future RPRC-1 reprocess would pass)."""
    def __init__(self, model_id=None):
        if model_id is not None:
            self.model_id = model_id

    def embed(self, texts):
        return np.random.rand(len(texts), 5).astype(np.float32)


def test_index_text_tags_injected_embedder_by_its_identity(tmp_path):
    # The tag must describe the embedder that ACTUALLY produced the vectors, not
    # whatever config happens to say — else TAG-3 would accept foreign vectors.
    from uuid import uuid4

    from va.pipeline.text_index import index_text
    from va.storage.structured.schema import connect

    db = tmp_path / "catalog.db"
    connect(str(db)).close()               # create the (empty) role tables _collect reads
    vdir = tmp_path / "vid"
    vdir.mkdir()

    # injected embedder with NO declared id -> honest "unknown" (TAG-3 skips it),
    # never the config's id stamped over foreign vectors
    index_text(uuid4(), vdir, str(db), embedder=_FakeEmbedder())
    assert NumpyFlatVectorStore(vdir / "text_vectors").meta["embedder"] == "unknown"

    # injected embedder that declares its id -> that id is stamped
    index_text(uuid4(), vdir, str(db), embedder=_FakeEmbedder("e5-large"))
    assert NumpyFlatVectorStore(vdir / "text_vectors").meta["embedder"] == "e5-large"
