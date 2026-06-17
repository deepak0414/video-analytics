"""Web layer e2e: submit ingest job -> poll -> search all modalities -> media.

Same offline discipline as the rest of the suite: stub backends + synthetic
color clips, no GPU/network. TestClient is used as a context manager so the
app lifespan starts/stops the ingest worker thread.
"""
import time

import pytest
from fastapi.testclient import TestClient

from va.media.synth import write_color_video
from va.web.app import create_app

SEGMENTS = [
    ("red", (220, 30, 30), 3.0),
    ("green", (30, 180, 30), 3.0),
    ("blue", (30, 30, 220), 3.0),
]


@pytest.fixture()
def client(tmp_path):
    app = create_app(str(tmp_path / ".va"))
    with TestClient(app) as c:
        yield c


def _make_clip(tmp_path):
    return write_color_video(tmp_path / "clip.mp4", SEGMENTS, fps=10)


def _ingest(client, uri, timeout=120.0):
    r = client.post("/api/videos", json={"uri": uri})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["state"] in ("done", "failed"):
            return j
        time.sleep(0.1)
    raise AssertionError("ingest job did not finish in time")


def test_index_and_empty_catalog(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    # assets are mtime-stamped so stale browser caches can't outlive a change
    assert "/static/app.js?v=" in r.text
    assert "/static/style.css?v=" in r.text
    assert r.headers["cache-control"] == "no-cache"
    assert client.get("/api/videos").json() == []


def test_ingest_job_flow_and_video_list(client, tmp_path):
    clip = _make_clip(tmp_path)

    job = _ingest(client, str(clip))
    assert job["state"] == "done", job["error"]
    assert job["video_id"]
    assert job["result"]["deduped"] is False
    assert job["result"]["frames_indexed"] >= 8
    assert job["result"]["segments"] == 3

    vids = client.get("/api/videos").json()
    assert len(vids) == 1
    v = vids[0]
    assert v["id"] == job["video_id"]
    assert v["ingest_status"] == "done"
    assert v["source_type"] == "local"
    assert v["has_media"] is True

    # resubmitting the same file dedupes through the web layer too
    again = _ingest(client, str(clip))
    assert again["state"] == "done"
    assert again["result"]["deduped"] is True
    assert again["video_id"] == job["video_id"]


def test_ingest_failure_reports_error(client, tmp_path):
    job = _ingest(client, str(tmp_path / "does-not-exist.mp4"))
    assert job["state"] == "failed"
    assert job["error"]


def test_search_all_modalities(client, tmp_path):
    job = _ingest(client, str(_make_clip(tmp_path)))
    assert job["state"] == "done", job["error"]

    res = client.get("/api/search", params={"q": "red sports car", "k": 5}).json()
    assert set(res) == {"visual", "caption", "transcript", "objects"}
    for col in res.values():
        assert "hits" in col and "note" in col

    # visual column: stub embedder is color-aware -> red query hits the 0-3s span
    hits = res["visual"]["hits"]
    assert hits, res["visual"]["note"]
    top = hits[0]
    assert set(top) == {"video_id", "t", "score", "label"}
    assert top["video_id"] == job["video_id"]
    assert top["t"] < 3.0
    assert top["score"] > 0.99


def test_media_endpoint_full_and_range(client, tmp_path):
    clip = _make_clip(tmp_path)
    job = _ingest(client, str(clip))
    vid = job["video_id"]
    size = clip.stat().st_size

    full = client.get(f"/api/media/{vid}")
    assert full.status_code == 200
    assert full.headers["accept-ranges"] == "bytes"
    assert len(full.content) == size

    part = client.get(f"/api/media/{vid}", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206
    assert len(part.content) == 100
    assert part.headers["content-range"] == f"bytes 0-99/{size}"
    assert part.content == full.content[:100]

    tail = client.get(f"/api/media/{vid}", headers={"Range": "bytes=-50"})
    assert tail.status_code == 206
    assert tail.content == full.content[-50:]

    bad = client.get(f"/api/media/{vid}", headers={"Range": f"bytes={size + 10}-"})
    assert bad.status_code == 416

    assert client.get("/api/media/not-a-uuid").status_code == 404


def test_job_not_found(client):
    assert client.get("/api/jobs/nope").status_code == 404


def _ask(client, question, k=5, timeout=60.0):
    """Submit an ask job and poll it to completion (asks are async like ingest)."""
    r = client.post("/api/ask", json={"question": question, "k": k})
    assert r.status_code == 202
    ask_id = r.json()["ask_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = client.get(f"/api/asks/{ask_id}").json()
        if j["state"] in ("done", "failed"):
            return j
        time.sleep(0.05)
    raise AssertionError("ask job did not finish in time")


def test_ask_endpoint(client, tmp_path):
    """Role 11 over the web: rule-stub reasoner, offline."""
    job = _ingest(client, str(_make_clip(tmp_path)))
    assert job["state"] == "done", job["error"]

    j = _ask(client, "when does the red part start?")
    assert j["state"] == "done", j["error"]
    body = j["result"]
    assert set(body) == {"question", "rendered", "evidence", "notes"}
    assert body["rendered"]
    for ev in body["evidence"]:
        assert set(ev) == {"modality", "video_id", "t", "score", "content"}
    # evidence items from this workdir's only video must be seekable in the UI
    assert any(ev["video_id"] == job["video_id"] for ev in body["evidence"])

    assert client.get("/api/asks/nope").status_code == 404


def test_media_resolves_via_video_dir_when_local_path_is_stale(client, tmp_path):
    """Layout v2: catalog `local_path` may be relative to the CWD of whichever
    session ingested the video. The web layer must fall back to the canonical
    per-video dir (videos/<key16>-*/media.*) so cross-session ingests play."""
    import shutil
    import sqlite3

    from va.pipeline.paths import Workspace

    clip = _make_clip(tmp_path)
    job = _ingest(client, str(clip))
    assert job["state"] == "done", job["error"]
    vid = job["video_id"]

    # Local-source media stays at the user's path; move it into the per-video
    # dir (where downloaded media lands) and break local_path the way a
    # different-CWD session would see it.
    ws = Workspace(tmp_path / ".va")
    db = sqlite3.connect(ws.catalog_db)
    source_key = db.execute(
        "select source_key from videos where id=?", (vid,)
    ).fetchone()[0]
    vdir = ws.video_dir(source_key, "clip", create=True)
    shutil.copy(clip, vdir / "media.mp4")
    db.execute(
        "update videos set local_path=? where id=?",
        ("some-other-cwd/.va/videos/x/media.mp4", vid),
    )
    db.commit()
    db.close()

    v = next(v for v in client.get("/api/videos").json() if v["id"] == vid)
    assert v["has_media"] is True

    r = client.get(f"/api/media/{vid}")
    assert r.status_code == 200
    assert len(r.content) == clip.stat().st_size


def test_ask_error_surfaces_detail(client, monkeypatch):
    """A reasoner crash must come back as a readable failed job, not vanish."""
    import va.pipeline.ask as ask_mod

    def boom(*args, **kwargs):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(ask_mod, "ask", boom)
    j = _ask(client, "anything")
    assert j["state"] == "failed"
    assert j["error"] == "RuntimeError: model exploded"
