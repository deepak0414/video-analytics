"""WS4.d — per-track appearance embeddings land in a SECOND per-video vector
store (`appearance.npz`), refs on the track rows; the Role-2 frame store is
untouched. Stub stack: color detector + iou tracker + hash embedder."""
import shutil
import sqlite3
from pathlib import Path

import yaml

from va.storage.vector.numpy_flat import NumpyFlatVectorStore

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"


def _ingest_red_box(tmp_path, monkeypatch, extra_roles=None):
    from va.media.synth import write_box_video
    from va.pipeline.ingest import ingest

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    roles = {"object_detector": {"classes": ["red", "blue"]}}
    roles.update(extra_roles or {})
    (cdir / "profiles" / "footage" / "colors.yaml").write_text(
        yaml.safe_dump({"roles": roles}))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))
    video = write_box_video(
        tmp_path / "clip.mp4", bg_rgb=(128, 128, 128), box_rgb=(220, 30, 30),
        box_frac=(0.25, 0.25, 0.5, 0.25), seconds=3.0, fps=10,
    )
    return ingest(str(video), workdir=str(tmp_path / ".va"), fps=1.0,
                  profile="colors")


def _video_dir(tmp_path) -> Path:
    # user-owned local files keep local_path OUTSIDE the workdir; the vector
    # stores live in the per-video dir under <workdir>/videos/
    (d,) = list((tmp_path / ".va" / "videos").iterdir())
    return d


def test_tracks_carry_resolvable_appearance_refs(tmp_path, monkeypatch):
    result = _ingest_red_box(tmp_path, monkeypatch)
    assert result.tracks >= 1

    ws = tmp_path / ".va"
    conn = sqlite3.connect(ws / "catalog.db")
    rows = conn.execute(
        "SELECT id, appearance_ref FROM object_tracks").fetchall()
    conn.close()
    assert rows and all(ref == f"track:{tid}" for tid, ref in rows)

    video_dir = _video_dir(tmp_path)
    store = NumpyFlatVectorStore(video_dir / "appearance")
    payload_refs = {p["appearance_ref"] for p in store._payloads}
    assert payload_refs == {ref for _, ref in rows}   # every ref resolves
    assert store.meta["space"] == "appearance-crop"
    assert store.dim > 0

    # the Role-2 FRAME store is a separate, untouched space: one vector per
    # sampled frame, no appearance payloads, no space tag
    frame_store = NumpyFlatVectorStore(video_dir / "vectors")
    assert frame_store.dim > 0
    assert len(frame_store._payloads) == result.frames_indexed
    assert all("appearance_ref" not in p for p in frame_store._payloads)
    assert "space" not in (frame_store.meta or {})


def test_appearance_payload_matches_track(tmp_path, monkeypatch):
    result = _ingest_red_box(tmp_path, monkeypatch)
    video_dir = _video_dir(tmp_path)
    store = NumpyFlatVectorStore(video_dir / "appearance")
    conn = sqlite3.connect(tmp_path / ".va" / "catalog.db")
    track_ids = {r[0] for r in conn.execute("SELECT id FROM object_tracks")}
    conn.close()
    for p in store._payloads:
        assert p["track_id"] in track_ids
        assert p["object_class"] == "red"
        assert p["video_id"] == str(result.video.id)


def test_disabled_tracker_produces_no_appearance_store(tmp_path, monkeypatch):
    result = _ingest_red_box(
        tmp_path, monkeypatch,
        extra_roles={"object_tracker": {"enabled": False}})
    assert result.tracks == 0
    assert result.detections >= 1        # untracked detections still stored
    assert not (_video_dir(tmp_path) / "appearance.npz").exists()
