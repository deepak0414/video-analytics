"""Batch reprocess — selection (RPRC-3) + execution (RPRC-1a) (WS-1 §6-b pillar B).

`plan_reprocess` resolves the stale (video, role) set scoped by role/video/all-stale,
read-only; `execute_reprocess` re-runs each wired role and restamps its provenance. Offline:
stub ingest, then poke provenance to simulate a model change. `va reprocess` executes for
`text_embedder` and `visual_embedder` (rebuilt in place); every other stale role is skipped
with a `va reingest` pointer, and `--dry-run` mutates nothing.
"""
from pathlib import Path

import pytest

from va.media.synth import write_color_video
from va.pipeline.ingest import ingest
from va.pipeline.paths import Workspace
from va.pipeline.reprocess import plan_reprocess
from va.storage.structured.provenance_store import ProvenanceStore


_COLORS_A = [("red", (220, 30, 30), 2.0), ("green", (30, 180, 30), 2.0)]
_COLORS_B = [("blue", (30, 30, 200), 2.0), ("yellow", (200, 200, 30), 2.0)]


def _clip(tmp_path, name="clip.mp4", colors=None):
    # distinct content -> distinct sha256 -> distinct source_key (else ingest DEDUPES them)
    return write_color_video(tmp_path / name, colors or _COLORS_A, fps=10)


def _make_stale(wd, video_id, role="ocr"):
    pv = ProvenanceStore(Workspace(wd).catalog_db)
    try:
        pv.record(video_id, role, "old-model", "STALE-FP", fps=2.0)   # != current fingerprint
    finally:
        pv.close()


def test_all_stale_returns_the_stale_set(tmp_path):
    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=2.0)
    _make_stale(wd, res.video.id)
    plan = plan_reprocess(wd, all_stale=True)
    assert len(plan) == 1
    assert plan[0]["stale_roles"] == ["ocr"]
    assert plan[0]["recorded_fps"] == 2.0            # fps carried through for the executor


def test_nothing_stale_gives_empty_plan(tmp_path):
    wd = str(tmp_path / ".va")
    ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    assert plan_reprocess(wd, all_stale=True) == []


def test_video_scope_selects_only_that_video(tmp_path):
    wd = str(tmp_path / ".va")
    a = ingest(str(_clip(tmp_path, "a.mp4", _COLORS_A)), workdir=wd, fps=1.0)
    b = ingest(str(_clip(tmp_path, "b.mp4", _COLORS_B)), workdir=wd, fps=1.0)
    assert a.video.id != b.video.id                  # distinct content -> two videos
    _make_stale(wd, a.video.id)                      # only A is stale
    pa = plan_reprocess(wd, video=str(a.video.id))
    assert len(pa) == 1 and pa[0]["video_id"] == str(a.video.id)
    assert plan_reprocess(wd, video=str(b.video.id)) == []   # B is current -> nothing


def test_role_filter_scopes_within_all_stale(tmp_path):
    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    _make_stale(wd, res.video.id, role="ocr")
    assert plan_reprocess(wd, all_stale=True, role="visual_embedder") == []
    assert plan_reprocess(wd, all_stale=True, role="ocr")[0]["stale_roles"] == ["ocr"]


def test_role_and_video_scopes_combine(tmp_path):
    # --role + --video together must intersect: only that video, only that role's staleness.
    # Guards against a refactor that filters by video before role-scoping (which would leak a
    # video's other stale roles into a --role query, so the executor re-runs current roles).
    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    _make_stale(wd, res.video.id, role="ocr")        # stale for ocr, current for the rest
    vid = str(res.video.id)
    assert plan_reprocess(wd, video=vid, role="ocr")[0]["stale_roles"] == ["ocr"]   # A: stale
    assert plan_reprocess(wd, video=vid, role="visual_embedder") == []              # B: current


def test_requires_exactly_one_video_scope(tmp_path):
    wd = str(tmp_path / ".va")
    ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    with pytest.raises(ValueError):
        plan_reprocess(wd)                           # neither scope
    with pytest.raises(ValueError):
        plan_reprocess(wd, all_stale=True, video="x")    # both scopes


def test_unknown_video_raises(tmp_path):
    wd = str(tmp_path / ".va")
    ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    with pytest.raises(ValueError):
        plan_reprocess(wd, video="not-a-real-video")


def test_unknown_role_raises(tmp_path):
    wd = str(tmp_path / ".va")
    ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    with pytest.raises(ValueError):
        plan_reprocess(wd, all_stale=True, role="reasoner")   # unstamped role


def test_non_done_video_scope_flags_reingest_not_current(tmp_path):
    # a --video target whose ingest never completed must NOT read as "already current" (an
    # empty plan): stale_report done-filters it, so surface that it needs re-ingest instead.
    from va.contracts.video import IngestStatus
    from va.storage.structured.catalog_sqlite import Catalog

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    cat = Catalog(Workspace(wd).catalog_db)
    try:
        cat.set_status(res.video.id, IngestStatus.pending)   # simulate a crashed ingest
    finally:
        cat.close()
    with pytest.raises(ValueError, match="not complete"):
        plan_reprocess(wd, video=str(res.video.id))


def test_execute_reprocesses_text_embedder_and_clears_stale(tmp_path):
    # the wired role (text_embedder) re-runs in place and its provenance is restamped, so it
    # is no longer stale afterward — with rows/shard written BEFORE the restamp.
    from va.pipeline.reprocess import execute_reprocess

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    _make_stale(wd, res.video.id, role="text_embedder")     # fake old fingerprint

    plan = plan_reprocess(wd, all_stale=True)
    assert plan and "text_embedder" in plan[0]["stale_roles"]
    result = execute_reprocess(wd, plan)

    assert [x[:2] for x in result["reprocessed"]] == [(str(res.video.id), "text_embedder")]
    assert not result["skipped"] and not result["failed"]
    assert plan_reprocess(wd, all_stale=True, role="text_embedder") == []   # now current
    pv = ProvenanceStore(Workspace(wd).catalog_db)
    try:
        assert pv.get(res.video.id, "text_embedder")[0]["fps"] == 2.0   # restamp PRESERVED fps
    finally:
        pv.close()


def test_execute_skips_role_without_in_place_reprocess(tmp_path):
    # ocr has no reprocessor -> skipped (NOT restamped), so it stays stale for `va reingest`.
    from va.pipeline.reprocess import execute_reprocess

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    _make_stale(wd, res.video.id, role="ocr")

    result = execute_reprocess(wd, plan_reprocess(wd, all_stale=True))
    assert (str(res.video.id), "ocr") in result["skipped"]
    assert not result["reprocessed"]
    assert plan_reprocess(wd, all_stale=True, role="ocr")   # still stale (untouched)


def test_execute_failure_leaves_role_stale(tmp_path, monkeypatch):
    # a reprocessor that raises must NOT restamp (else stale data reads as current) and must
    # NOT abort the batch — the role stays stale, safe to retry.
    import va.pipeline.reprocess as rp

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    _make_stale(wd, res.video.id, role="text_embedder")

    def boom(workdir, video_id):
        raise RuntimeError("embed boom")
    monkeypatch.setitem(rp._REPROCESSORS, "text_embedder", boom)

    result = rp.execute_reprocess(wd, plan_reprocess(wd, all_stale=True))
    assert result["failed"] and not result["reprocessed"]
    assert plan_reprocess(wd, all_stale=True, role="text_embedder")   # still stale (not restamped)


def test_execute_on_removed_video_fails_without_resurrecting_provenance(tmp_path):
    # video removed between plan and execute: backfill can't resolve it -> the role must FAIL,
    # and NO provenance is restamped (a 0-row "success" would resurrect a purged row current).
    from va.pipeline.manage import remove_video
    from va.pipeline.reprocess import execute_reprocess

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    _make_stale(wd, res.video.id, role="text_embedder")
    plan = plan_reprocess(wd, all_stale=True)         # captured while the video exists
    remove_video(wd, str(res.video.id))               # ...then it's gone

    result = execute_reprocess(wd, plan)
    assert result["failed"] and not result["reprocessed"]
    pv = ProvenanceStore(Workspace(wd).catalog_db)
    try:
        assert pv.get(res.video.id) == []             # NOT resurrected
    finally:
        pv.close()


def test_restamp_uses_one_batch_pinned_config(tmp_path, monkeypatch):
    # every restamp must fingerprint against ONE config pinned at batch start, not a fresh
    # load per (video, role) — else a mid-batch config edit stamps a fingerprint that drifted
    # from what the reprocessor ran under (a missed stale; PROV-3's lesson).
    import va.pipeline.reprocess as rp
    import va.provenance as provenance

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    _make_stale(wd, res.video.id, role="text_embedder")

    pinned = object()
    monkeypatch.setattr(rp, "load_config", lambda: pinned)
    seen = []
    real_fp = provenance.role_fingerprint
    monkeypatch.setattr(provenance, "role_fingerprint",
                        lambda role, cfg=None: seen.append(cfg) or real_fp(role))
    rp.execute_reprocess(wd, plan_reprocess(wd, all_stale=True))
    assert seen and all(c is pinned for c in seen)    # the pinned cfg, never a fresh load


def test_failed_text_rebuild_preserves_the_old_shard(tmp_path):
    # index_text builds to a temp shard and swaps on success, so a rebuild that fails at embed()
    # (e.g. GPU OOM on a model switch) leaves the prior text_vectors shard intact.
    from va.pipeline.paths import Workspace
    from va.pipeline.text_index import index_text

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    ws = Workspace(wd)
    vdir = ws.video_dir(res.video.source_key, res.video.title)
    shard = Path(vdir) / "text_vectors.npz"
    assert shard.exists()
    before = shard.read_bytes()

    class _BoomEmbedder:
        model_id = "boom"

        def embed(self, texts):
            raise RuntimeError("OOM")

    with pytest.raises(RuntimeError):
        index_text(res.video.id, vdir, ws.catalog_db, embedder=_BoomEmbedder())
    assert shard.exists() and shard.read_bytes() == before   # old shard untouched


def test_text_rebuild_persist_failure_preserves_old_shard(tmp_path, monkeypatch):
    # temp+swap must survive a failure DURING persist (disk-full in np.savez / process kill),
    # not just during embed — the swap only happens after a fully-written temp shard, so the
    # prior shard stays intact. (embed-before-unlink alone left an unlink->persist window.)
    import numpy as np

    from va.pipeline.manage import lookup_video
    from va.pipeline.paths import Workspace
    from va.pipeline.text_index import index_text
    from va.storage.structured.catalog_sqlite import Catalog

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    ws = Workspace(wd)
    cat = Catalog(ws.catalog_db)
    try:
        video = lookup_video(cat, str(res.video.id))
    finally:
        cat.close()
    vdir = ws.video_dir(video.source_key, video.title)
    shard = Path(vdir) / "text_vectors.npz"
    before = shard.read_bytes()

    monkeypatch.setattr(np, "savez", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        index_text(video.id, vdir, ws.catalog_db)
    assert shard.read_bytes() == before                        # old shard intact (never swapped)
    # a temp .json may linger (persist writes it before the failing .npz); the next rebuild's
    # temp-clear removes it, and no reader globs `*_rebuild.npz` (which was never written)


def test_execute_reprocesses_visual_embedder_and_clears_stale(tmp_path):
    # the visual reprocessor re-samples at the recorded fps, rebuilds + re-tags the vectors
    # shard, and its provenance is restamped -> no longer stale.
    from va.pipeline.paths import Workspace
    from va.pipeline.reprocess import execute_reprocess
    from va.registry import embedder_id
    from va.storage.vector.numpy_flat import NumpyFlatVectorStore

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    pv = ProvenanceStore(Workspace(wd).catalog_db)
    try:  # stale, but KNOWN fps (== ingest fps) so re-embed can preserve the density
        pv.record(res.video.id, "visual_embedder", "old", "STALE-FP", fps=1.0)
    finally:
        pv.close()

    result = execute_reprocess(wd, plan_reprocess(wd, all_stale=True))
    assert [x[:2] for x in result["reprocessed"]] == [(str(res.video.id), "visual_embedder")]
    assert not result["failed"]
    assert plan_reprocess(wd, all_stale=True, role="visual_embedder") == []   # now current
    vdir = Workspace(wd).video_dir(res.video.source_key, res.video.title)
    shard = NumpyFlatVectorStore(Path(vdir) / "vectors")
    assert shard.meta and shard.meta["embedder"] == embedder_id("visual_embedder")  # re-tagged


def test_visual_reprocess_refuses_unknown_fps(tmp_path):
    # re-embedding at the wrong density would corrupt search; if the recorded fps is unknown,
    # refuse (-> failed, NOT restamped) rather than guess a density.
    from va.pipeline.paths import Workspace
    from va.pipeline.reprocess import execute_reprocess

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    pv = ProvenanceStore(Workspace(wd).catalog_db)
    try:
        pv.record(res.video.id, "visual_embedder", "old", "STALE-FP", fps=None)   # fps unknown
    finally:
        pv.close()

    result = execute_reprocess(wd, plan_reprocess(wd, all_stale=True))
    assert any(r == "visual_embedder" for _, r, _ in result["failed"])
    assert not result["reprocessed"]
    assert plan_reprocess(wd, all_stale=True, role="visual_embedder")   # still stale


def test_failed_visual_reembed_preserves_old_shard(tmp_path):
    # reindex_visual builds to a temp shard and swaps only on success, so a failed embed
    # leaves the prior vectors shard intact (visual search keeps working until a retry).
    from va.pipeline.manage import lookup_video
    from va.pipeline.paths import Workspace
    from va.pipeline.reprocess import reindex_visual
    from va.storage.structured.catalog_sqlite import Catalog

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    ws = Workspace(wd)
    cat = Catalog(ws.catalog_db)
    try:
        video = lookup_video(cat, str(res.video.id))
    finally:
        cat.close()
    vdir = ws.video_dir(video.source_key, video.title)
    shard = Path(vdir) / "vectors.npz"
    assert shard.exists()
    before = shard.read_bytes()

    class _BoomEmbedder:
        model_id = "boom"

        def embed_image(self, images):
            raise RuntimeError("OOM")

    with pytest.raises(RuntimeError):
        reindex_visual(video, vdir, fps=1.0, embedder=_BoomEmbedder())
    assert shard.exists() and shard.read_bytes() == before          # old shard untouched
    assert not (Path(vdir) / "vectors_rebuild.npz").exists()        # temp not left behind


def test_visual_reembed_zero_frames_raises_and_preserves_shard(tmp_path, monkeypatch):
    # a decode that yields ZERO frames (corrupt media that opens but iterates nothing) must NOT
    # swap in an empty shard — the video would vanish from visual search while `va stale` reads
    # clean. Raise so the old shard survives and the role stays stale for retry.
    import va.media.frames as frames_mod
    from va.pipeline.manage import lookup_video
    from va.pipeline.paths import Workspace
    from va.pipeline.reprocess import reindex_visual
    from va.storage.structured.catalog_sqlite import Catalog

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    ws = Workspace(wd)
    cat = Catalog(ws.catalog_db)
    try:
        video = lookup_video(cat, str(res.video.id))
    finally:
        cat.close()
    vdir = ws.video_dir(video.source_key, video.title)
    shard = Path(vdir) / "vectors.npz"
    before = shard.read_bytes()

    monkeypatch.setattr(frames_mod, "sample_frames", lambda path, fps=1.0: iter(()))
    with pytest.raises(ValueError, match="0 frames"):
        reindex_visual(video, vdir, fps=1.0)
    assert shard.read_bytes() == before                             # old shard intact


def test_skip_pointer_carries_recorded_fps(tmp_path, capsys):
    from va.cli import build_parser

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=2.0)   # all roles fps=2.0
    _make_stale(wd, res.video.id, role="ocr")                # poke keeps fps=2.0 -> known
    parser = build_parser()

    args = parser.parse_args(["--workdir", wd, "reprocess", "--all-stale", "--yes"])
    args.func(args)
    cap = capsys.readouterr()
    # the reingest pointer must preserve density (reingest defaults to 1.0)
    assert "va reingest" in cap.out and "--fps 2.0" in cap.out


def test_cli_execute_requires_yes(tmp_path, capsys):
    # executing OVERWRITES shards in place; without --yes the command must NOT mutate (the
    # wrong-VA_CONFIG_DIR foot-gun would re-embed a real corpus with the stub otherwise).
    from va.cli import build_parser

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    _make_stale(wd, res.video.id, role="text_embedder")
    parser = build_parser()

    args = parser.parse_args(["--workdir", wd, "reprocess", "--all-stale"])   # no --yes
    rc = args.func(args)
    cap = capsys.readouterr()
    assert rc == 1
    assert "NOT executed" in cap.err and "--yes" in cap.err
    assert "text_embedder" in cap.out                        # plan still shown as the review step
    assert plan_reprocess(wd, all_stale=True, role="text_embedder")   # unchanged -> still stale


def test_cli_executes_supported_and_skips_unsupported(tmp_path, capsys):
    from va.cli import build_parser

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    _make_stale(wd, res.video.id, role="text_embedder")
    _make_stale(wd, res.video.id, role="ocr")                # one wired, one not
    parser = build_parser()

    args = parser.parse_args(["--workdir", wd, "reprocess", "--all-stale", "--yes"])   # execute
    rc = args.func(args)
    cap = capsys.readouterr()
    assert rc == 0
    assert "text_embedder: reprocessed" in cap.out
    assert "ocr: skipped" in cap.out and "reingest" in cap.out
    # text_embedder now current; ocr still stale
    assert plan_reprocess(wd, all_stale=True, role="text_embedder") == []
    assert plan_reprocess(wd, all_stale=True, role="ocr")


def test_cli_dry_run_mutates_nothing(tmp_path, capsys):
    from va.cli import build_parser

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    _make_stale(wd, res.video.id, role="text_embedder")
    parser = build_parser()

    args = parser.parse_args(["--workdir", wd, "reprocess", "--all-stale", "--dry-run"])
    rc = args.func(args)
    cap = capsys.readouterr()
    assert rc == 0 and "dry run" in cap.out
    assert plan_reprocess(wd, all_stale=True, role="text_embedder")   # unchanged -> still stale


def test_cli_requires_a_scope():
    from va.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit):                  # neither --all-stale nor --video
        parser.parse_args(["--workdir", "x", "reprocess"])
    with pytest.raises(SystemExit):                  # both (mutually exclusive)
        parser.parse_args(["--workdir", "x", "reprocess", "--all-stale", "--video", "v"])
