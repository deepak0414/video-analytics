"""WS3.a — camera entity + videos.camera_id (schema v4).

Done-conditions from architecture-evolution-loop.md: migration tests (fresh vs
migrated equivalence) pass; A-EV videos keep camera_id NULL.
"""
import sqlite3

from va.contracts.video import Camera
from va.media.synth import write_color_video
from va.pipeline.ingest import ingest
from va.storage.structured.cameras import CameraStore
from va.storage.structured.catalog_sqlite import Catalog
from va.storage.structured.schema import SCHEMA_VERSION, connect


def _cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def test_fresh_db_has_cameras_at_v4(tmp_path):
    conn = connect(tmp_path / "fresh.db")
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION >= 4
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cameras'"
        ).fetchone()
        assert "camera_id" in _cols(conn, "videos")
    finally:
        conn.close()


def test_v3_db_migrates_to_v4_with_null_camera(tmp_path):
    # Simulate a v3-era DB (no cameras table, no camera_id) holding an A-EV row;
    # opening must migrate in place and the row must read back camera_id=None.
    db = tmp_path / "old.db"
    conn = connect(db)
    conn.close()
    raw = sqlite3.connect(db)
    raw.execute("ALTER TABLE videos DROP COLUMN camera_id")
    raw.execute("DROP TABLE cameras")
    raw.execute("PRAGMA user_version = 3")
    raw.execute(
        "INSERT INTO videos (id, source_type, source_uri, source_key, ingest_status,"
        " created_at) VALUES ('00000000-0000-0000-0000-000000000001', 'local', '/x',"
        " 'k1', 'done', '2026-01-01T00:00:00+00:00')"
    )
    raw.commit()
    raw.close()

    cat = Catalog(db)  # opening migrates v3 -> v4
    try:
        v = cat.get_by_source_key("k1")
        assert v is not None and v.camera_id is None
    finally:
        cat.close()
    conn = connect(db)
    try:
        # fresh-vs-migrated equivalence: same columns either way
        fresh = connect(tmp_path / "fresh.db")
        assert sorted(_cols(conn, "videos")) == sorted(_cols(fresh, "videos"))
        assert sorted(_cols(conn, "cameras")) == sorted(_cols(fresh, "cameras"))
        fresh.close()
    finally:
        conn.close()


def test_aev_ingest_keeps_camera_null(tmp_path):
    clip = write_color_video(tmp_path / "c.mp4", [("red", (220, 30, 30), 2.0)], fps=10)
    res = ingest(str(clip), workdir=str(tmp_path / ".va"), fps=1.0)
    assert res.video.camera_id is None


def test_reingest_preserves_the_camera_link(tmp_path):
    # remove+ingest recreates the row — the camera link must carry across
    # (same invariant as the profile carry-forward).
    from va.pipeline.manage import reingest_video

    clip = write_color_video(tmp_path / "c.mp4", [("red", (220, 30, 30), 2.0)], fps=10)
    wd = str(tmp_path / ".va")
    res = ingest(str(clip), workdir=wd, fps=1.0)
    db = tmp_path / ".va" / "catalog.db"
    store = CameraStore(db)
    try:
        store.get_or_create(Camera(id="cam-1", name="Porch"))
    finally:
        store.close()
    cat = Catalog(db)
    cat.set_camera(res.video.id, "cam-1")
    cat.close()

    again = reingest_video(wd, str(clip), fps=1.0)
    assert again.video.camera_id == "cam-1"
    cat = Catalog(db)
    try:
        assert cat.get(again.video.id).camera_id == "cam-1"
    finally:
        cat.close()


def test_failed_reingest_still_reattaches_the_camera_link(tmp_path, monkeypatch):
    # A reingest whose ingest fails hard (after recreating the row) must still
    # leave the camera link on the failed row — a later retry completes it as-is.
    import pytest as _pytest

    import va.pipeline.ingest as ingest_mod
    from va.pipeline.manage import reingest_video

    clip = write_color_video(tmp_path / "c.mp4", [("red", (220, 30, 30), 2.0)], fps=10)
    wd = str(tmp_path / ".va")
    res = ingest(str(clip), workdir=wd, fps=1.0)
    db = tmp_path / ".va" / "catalog.db"
    store = CameraStore(db)
    try:
        store.get_or_create(Camera(id="cam-1", name="Porch"))
    finally:
        store.close()
    cat = Catalog(db)
    cat.set_camera(res.video.id, "cam-1")
    cat.close()

    def boom(*a, **k):
        raise RuntimeError("decode exploded")

    monkeypatch.setattr(ingest_mod, "sample_frames", boom)  # critical-path failure
    with _pytest.raises(RuntimeError, match="decode exploded"):
        reingest_video(wd, str(clip), fps=1.0)

    cat = Catalog(db)
    try:
        row = cat.get_by_source_key(res.video.source_key)
        assert row is not None and row.camera_id == "cam-1"
        assert row.ingest_status.value == "failed"
    finally:
        cat.close()


def test_camera_store_roundtrip_and_video_reference(tmp_path):
    db = tmp_path / "catalog.db"
    store = CameraStore(db)
    try:
        cam = Camera(id="cam-2", name="Driveway", source_ref="lnr608:ch2",
                     location="front, facing street")
        created, was_new = store.get_or_create(cam)
        assert was_new and created.id == "cam-2"
        again, was_new = store.get_or_create(Camera(id="cam-2", name="Renamed"))
        assert not was_new and again.name == "Driveway"  # id is the idempotency key
        assert [c.id for c in store.list()] == ["cam-2"]
    finally:
        store.close()

    clip = write_color_video(tmp_path / "c.mp4", [("red", (220, 30, 30), 2.0)], fps=10)
    res = ingest(str(clip), workdir=str(tmp_path), fps=1.0)  # workdir root holds catalog.db
    cat = Catalog(db)
    try:
        cat.set_camera(res.video.id, "cam-2")
        assert cat.get(res.video.id).camera_id == "cam-2"
    finally:
        cat.close()
