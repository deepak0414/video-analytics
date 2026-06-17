"""Workspace layout v2: per-video dirs, sharded vectors, remove/reingest, migration."""
import shutil
from pathlib import Path

from va.media.synth import write_color_video
from va.pipeline.ingest import ingest
from va.pipeline.manage import remove_video, reingest_video
from va.pipeline.migrate import migrate_workdir
from va.pipeline.paths import Workspace
from va.pipeline.query import query
from va.storage.structured.catalog_sqlite import Catalog
from va.storage.vector.numpy_flat import NumpyFlatVectorStore


def test_video_dir_naming_and_stability(tmp_path):
    ws = Workspace(tmp_path)
    d1 = ws.video_dir("sha256:deadbeefcafe1234ffff", "My Great Video!! (2024)", create=True)
    assert d1.name == "deadbeefcafe1234-my-great-video-2024"
    # lookup is by key prefix: a different/missing title finds the SAME dir
    assert ws.video_dir("sha256:deadbeefcafe1234ffff", "Renamed Later") == d1
    assert ws.video_dir("sha256:deadbeefcafe1234ffff") == d1
    # youtube ids work as-is
    d2 = ws.video_dir("dQw4w9WgXcQ", None, create=True)
    assert d2.name == "dQw4w9WgXcQ-video"


def _clip(tmp_path, name, color):
    return write_color_video(tmp_path / name, [(name, color, 3.0)], fps=10)


def test_ingest_uses_video_dirs_and_query_spans_shards(tmp_path):
    wd = str(tmp_path / ".va")
    r1 = ingest(str(_clip(tmp_path, "red.mp4", (220, 30, 30))), workdir=wd, fps=1.0)
    r2 = ingest(str(_clip(tmp_path, "blue.mp4", (30, 30, 220))), workdir=wd, fps=1.0)

    ws = Workspace(wd)
    dirs = sorted(p.name for p in ws.videos_root.iterdir())
    assert len(dirs) == 2
    # each video has its own shard; query spans BOTH (one logical index)
    assert all((ws.videos_root / d / "vectors.npz").exists() for d in dirs)
    red_hits = query("red box", workdir=wd, k=3)
    blue_hits = query("blue box", workdir=wd, k=3)
    assert red_hits[0].video_id == r1.video.id
    assert blue_hits[0].video_id == r2.video.id
    # user files outside the workdir are NOT moved
    assert (tmp_path / "red.mp4").exists()


def test_remove_deletes_rows_dir_and_vectors(tmp_path):
    wd = str(tmp_path / ".va")
    r1 = ingest(str(_clip(tmp_path, "red.mp4", (220, 30, 30))), workdir=wd, fps=1.0)
    r2 = ingest(str(_clip(tmp_path, "blue.mp4", (30, 30, 220))), workdir=wd, fps=1.0)
    ws = Workspace(wd)

    removed = remove_video(wd, r1.video.source_key)
    assert removed is not None and removed.id == r1.video.id

    cat = Catalog(ws.catalog_db)
    assert cat.get(r1.video.id) is None
    assert cat.get(r2.video.id) is not None       # the other video untouched
    cat.close()
    assert not ws.video_dir(r1.video.source_key).exists()
    # red is gone from search; blue remains
    assert all(h.video_id != r1.video.id for h in query("red box", workdir=wd, k=5))
    assert query("blue box", workdir=wd, k=3)[0].video_id == r2.video.id

    assert remove_video(wd, "nonexistent") is None


def test_reingest_local_source(tmp_path):
    wd = str(tmp_path / ".va")
    clip = _clip(tmp_path, "red.mp4", (220, 30, 30))
    r1 = ingest(str(clip), workdir=wd, fps=1.0)

    r2 = reingest_video(wd, str(clip), fps=1.0)
    assert r2 is not None and r2.deduped is False      # actually re-processed
    assert r2.video.source_key == r1.video.source_key  # same identity
    assert query("red box", workdir=wd, k=3)           # searchable again


def test_migration_from_v1_layout(tmp_path):
    # build a fresh v2 ingest, then DEGRADE it to v1 (monolith vectors, media in
    # cache/, keyframes in cache/keyframes/), then migrate back up.
    wd = str(tmp_path / ".va")
    ws = Workspace(wd)
    clip = _clip(tmp_path, "red.mp4", (220, 30, 30))
    r = ingest(str(clip), workdir=wd, fps=1.0)
    vdir = ws.video_dir(r.video.source_key, r.video.title)

    # degrade: shard -> monolith
    shard = NumpyFlatVectorStore(vdir / "vectors")
    legacy = NumpyFlatVectorStore(ws.legacy_vectors)
    legacy.add(shard._vecs, shard._payloads)
    legacy.persist()
    # degrade: media -> cache/, catalog points at cache path (v1 style)
    ws.cache.mkdir(parents=True, exist_ok=True)
    cache_media = ws.cache / "old.mp4"
    media = vdir / "media.mp4"
    if media.exists():
        shutil.move(str(media), str(cache_media))
    else:  # local user file was never moved; copy to simulate v1 managed dl
        shutil.copy(str(clip), str(cache_media))
    cat = Catalog(ws.catalog_db)
    cat.set_paths(r.video.id, str(cache_media), source_uri=str(cache_media))
    cat.close()
    # degrade: keyframes in shared cache dir
    (ws.cache / "keyframes").mkdir(parents=True, exist_ok=True)
    (ws.cache / "keyframes" / f"{r.video.id}_3.png").write_bytes(b"png")
    shutil.rmtree(vdir)

    stats = migrate_workdir(wd)
    assert stats["vectors_split"] == 1 and stats["media_moved"] == 1
    assert stats["keyframes_moved"] == 1

    vdir2 = ws.video_dir(r.video.source_key, r.video.title)
    assert (vdir2 / "media.mp4").exists()
    assert (vdir2 / "keyframes" / "3.png").exists()
    assert not (ws.root / "vectors.npz").exists()          # monolith retired
    assert (ws.root / "vectors.npz.v1.bak").exists()       # ...but backed up
    cat = Catalog(ws.catalog_db)
    assert cat.get(r.video.id).local_path == str(vdir2 / "media.mp4")
    cat.close()
    assert query("red box", workdir=wd, k=3)               # search works post-migration

    # idempotent: second run is a no-op
    stats2 = migrate_workdir(wd)
    assert stats2["media_moved"] == 0 and stats2["vectors_split"] == 0