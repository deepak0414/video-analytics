"""Query-time vector-space guard (WS-1 §6-b — TAG-3): ShardedVectorStore skips
per-video shards whose embedder tag doesn't match the current query embedder, so
search never mixes incompatible vector spaces (stub-64 vs SigLIP-1152) and never
crashes on a dimension mismatch. The count of skipped shards is exposed for surfacing.
Offline: hand-built shards, no models.
"""
import numpy as np

from va.media.synth import write_color_video
from va.pipeline.ingest import ingest
from va.storage.vector.numpy_flat import NumpyFlatVectorStore
from va.storage.vector.sharded import ShardedVectorStore, clear_shard_cache


def _shard(root, name, dim, embedder=None):
    d = root / name
    d.mkdir(parents=True)
    s = NumpyFlatVectorStore(d / "vectors")
    s.add(np.random.rand(2, dim).astype(np.float32),
          [{"video_id": name, "i": 0}, {"video_id": name, "i": 1}])
    if embedder is not None:
        s.set_meta({"embedder": embedder})
    s.persist()


def test_search_skips_shards_from_a_different_embedder(tmp_path):
    root = tmp_path / "videos"
    _shard(root, "a", 8, embedder="siglip")
    _shard(root, "b", 8, embedder="hash")          # same dim, WRONG model
    clear_shard_cache()

    store = ShardedVectorStore(root)
    hits = store.search(np.random.rand(8).astype(np.float32), k=10, expect_embedder="siglip")

    assert store.skipped == 1                        # the hash shard was excluded
    assert hits and all(h.payload["video_id"] == "a" for h in hits)  # only siglip's hits


def test_search_skips_dim_mismatch_without_crashing(tmp_path):
    # Different-dim shard would crash `vecs @ q` if searched — the guard skips it first.
    root = tmp_path / "videos"
    _shard(root, "old", 4, embedder="hash")          # foreign dim 4
    clear_shard_cache()

    store = ShardedVectorStore(root)
    hits = store.search(np.random.rand(8).astype(np.float32), k=10, expect_embedder="siglip")

    assert hits == []
    assert store.skipped == 1                        # skipped, no ValueError


def test_untagged_shard_admitted_on_dim_match_skipped_on_mismatch(tmp_path):
    # Legacy (pre-tagging) shards can't be verified by model — admitted only when the
    # dim matches (best-effort; the honest D4 gap), skipped when it would crash.
    root = tmp_path / "videos"
    _shard(root, "match", 8, embedder=None)          # untagged, dim matches query
    _shard(root, "foreign", 4, embedder=None)        # untagged, dim mismatch
    clear_shard_cache()

    store = ShardedVectorStore(root)
    hits = store.search(np.random.rand(8).astype(np.float32), k=10, expect_embedder="hash")

    assert store.skipped == 1                        # only the dim-4 shard skipped
    assert hits and all(h.payload["video_id"] == "match" for h in hits)


def test_no_guard_when_expect_embedder_is_none(tmp_path):
    # Backward compatible: without expect_embedder, no shard is skipped.
    root = tmp_path / "videos"
    _shard(root, "a", 8, embedder="siglip")
    _shard(root, "b", 8, embedder="hash")
    clear_shard_cache()

    store = ShardedVectorStore(root)
    store.search(np.random.rand(8).astype(np.float32), k=10)
    assert store.skipped == 0


def test_empty_tagged_shard_admitted_on_embedder_match(tmp_path):
    # An empty shard tagged {embedder: X, dim: None} must NOT read as a mismatch when
    # the embedder matches (searching it is a no-op) — else a perpetual false
    # "reprocess" warning that re-indexing can never clear.
    root = tmp_path / "videos"
    d = root / "empty"
    d.mkdir(parents=True)
    empty = NumpyFlatVectorStore(d / "vectors")
    empty.set_meta({"embedder": "bge"})          # tagged, nothing added -> dim None
    empty.persist()
    _shard(root, "full", 8, embedder="bge")      # a populated matching shard
    clear_shard_cache()

    store = ShardedVectorStore(root)
    hits = store.search(np.random.rand(8).astype(np.float32), k=10, expect_embedder="bge")
    assert store.skipped == 0                    # empty shard admitted, not falsely skipped
    assert hits and all(h.payload["video_id"] == "full" for h in hits)


def _clip(tmp_path):
    return write_color_video(
        tmp_path / "c.mp4",
        [("red", (220, 30, 30), 2.0), ("green", (30, 180, 30), 2.0)], fps=10)


def test_retrieval_falls_back_to_lexical_on_stale_text_index(tmp_path):
    # A text index left on an OLD embedder is non-empty but unsearchable; retrieval
    # must run the lexical fallback AND surface a note, not silently drop the tier.
    from va.contracts.query_plan import QueryPlan
    from va.pipeline.paths import Workspace
    from va.pipeline.retrieval import retrieve
    from va.storage.structured.catalog_sqlite import Catalog

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)

    ws = Workspace(wd)
    cat = Catalog(ws.catalog_db)
    try:
        v = cat.get(res.video.id)
    finally:
        cat.close()
    tvec = NumpyFlatVectorStore(ws.video_dir(v.source_key, v.title) / "text_vectors")
    if tvec.count() == 0:                         # ensure "stale" (non-empty), not "empty"
        tvec.add(np.random.rand(1, 5).astype(np.float32),
                 [{"video_id": str(v.id), "modality": "transcript",
                   "time_start": 0.0, "time_end": 1.0, "text": "x", "source_role": 8}])
    tvec.set_meta({"embedder": "OLD-EMBEDDER"})   # != the current config's "hash"
    tvec.persist()
    clear_shard_cache()

    plan = QueryPlan(query="budget", search_terms="budget", needs_transcript_search=True)
    ev = retrieve(plan, workdir=wd, k=5)
    assert any("different embedder" in n for n in ev.notes)   # surfaced, not silently lost


def test_query_paths_forward_their_own_role_id(tmp_path, monkeypatch):
    # On the stub config both roles resolve to "hash", so a cross-role mix-up would
    # pass unnoticed offline. Force distinct ids and assert each path forwards its own.
    import va.storage.vector.sharded as sh

    from va.pipeline.query import query
    from va.pipeline.text_search import search_text

    wd = str(tmp_path / ".va")
    ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    clear_shard_cache()

    captured = []
    orig = sh.ShardedVectorStore.search

    def spy(self, q, k, expect_embedder=None):
        captured.append(expect_embedder)
        return orig(self, q, k, expect_embedder=expect_embedder)

    monkeypatch.setattr(sh.ShardedVectorStore, "search", spy)
    monkeypatch.setattr("va.pipeline.query.embedder_id", lambda role: f"ID:{role}")
    monkeypatch.setattr("va.pipeline.text_search.embedder_id", lambda role: f"ID:{role}")

    query("red", workdir=wd, k=5)
    search_text("red", workdir=wd, k=5)
    assert captured == ["ID:visual_embedder", "ID:text_embedder"]


def test_retrieval_falls_back_when_untagged_text_shard_dim_mismatches(tmp_path):
    # An UNTAGGED legacy text shard whose dim != the current embedder must ALSO route
    # to the lexical fallback: count() and the search-time guard must agree on it, or
    # it reads as usable then gets skipped at search — the silent drop finding 1 flagged.
    from va.contracts.query_plan import QueryPlan
    from va.pipeline.paths import Workspace
    from va.pipeline.retrieval import retrieve
    from va.registry import get_text_embedder
    from va.storage.structured.catalog_sqlite import Catalog

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    ws = Workspace(wd)
    cat = Catalog(ws.catalog_db)
    try:
        v = cat.get(res.video.id)
    finally:
        cat.close()

    cur_dim = len(get_text_embedder().embed(["_"])[0])
    tvpath = ws.video_dir(v.source_key, v.title) / "text_vectors"
    for suf in (".npz", ".json"):
        p = tvpath.with_suffix(suf)
        if p.exists():
            p.unlink()
    tv = NumpyFlatVectorStore(tvpath)             # UNTAGGED, foreign dim
    tv.add(np.random.rand(1, cur_dim + 3).astype(np.float32),
           [{"video_id": str(v.id), "modality": "transcript",
             "time_start": 0.0, "time_end": 1.0, "text": "x", "source_role": 8}])
    tv.persist()                                  # no set_meta -> untagged legacy shard
    clear_shard_cache()

    plan = QueryPlan(query="budget", search_terms="budget", needs_transcript_search=True)
    ev = retrieve(plan, workdir=wd, k=5)
    assert any("different embedder" in n for n in ev.notes)


def test_retrieval_notes_a_stale_visual_index(tmp_path):
    # A fully-stale VISUAL index yields zero visual hits — retrieval must SAY the index
    # is stale, not let it read as a true no-match with only a stderr log line.
    from va.contracts.query_plan import QueryPlan
    from va.pipeline.paths import Workspace
    from va.pipeline.retrieval import retrieve
    from va.storage.structured.catalog_sqlite import Catalog

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    ws = Workspace(wd)
    cat = Catalog(ws.catalog_db)
    try:
        v = cat.get(res.video.id)
    finally:
        cat.close()
    vvec = NumpyFlatVectorStore(ws.video_dir(v.source_key, v.title) / "vectors")
    assert vvec.count() > 0
    vvec.set_meta({"embedder": "OLD-VISUAL"})     # != the current config's "hash"
    vvec.persist()
    clear_shard_cache()

    ev = retrieve(QueryPlan(query="red car", search_terms="red car"), workdir=wd, k=5)
    assert any("visual index unusable" in n for n in ev.notes)
