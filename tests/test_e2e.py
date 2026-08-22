"""End-to-end slice: ingest a local video -> query -> correct moment.

Uses the color-aware hash embedder + a synthetic red/green/blue clip, so the
whole pipeline (sources -> catalog -> frames -> embed -> vector store -> query)
is exercised with a real retrieval assertion and no network/GPU.
"""
from va.cli import main
from va.media.synth import write_color_video
from va.pipeline.ingest import ingest
from va.pipeline.query import query

SEGMENTS = [
    ("red", (220, 30, 30), 3.0),
    ("green", (30, 180, 30), 3.0),
    ("blue", (30, 30, 220), 3.0),
]


def _make(tmp_path):
    return write_color_video(tmp_path / "clip.mp4", SEGMENTS, fps=10)


def test_ingest_then_query_finds_right_color_moment(tmp_path):
    video = _make(tmp_path)
    wd = str(tmp_path / ".va")

    res = ingest(str(video), workdir=wd, fps=1.0)
    assert res.deduped is False
    assert res.frames_indexed >= 8
    # Role 1 ran during ingest: the red/green/blue clip yields 3 segments,
    # persisted to the central catalog DB.
    assert res.segments == 3
    from va.storage.structured.segments import SegmentStore
    assert SegmentStore(f"{wd}/catalog.db").count(res.video.id) == 3

    # red query -> a red moment (0-3s)
    red = query("red sports car", workdir=wd, k=5)
    assert red and red[0].score > 0.99
    assert red[0].timestamp < 3.0

    # green query -> a green moment (3-6s)
    green = query("a green field", workdir=wd, k=5)
    assert green and green[0].timestamp >= 3.0 and green[0].timestamp < 6.0


def test_ingest_is_idempotent(tmp_path):
    video = _make(tmp_path)
    wd = str(tmp_path / ".va")

    first = ingest(str(video), workdir=wd, fps=1.0)
    second = ingest(str(video), workdir=wd, fps=1.0)
    assert first.deduped is False
    assert second.deduped is True          # already ingested
    assert second.frames_indexed == 0
    assert second.video.id == first.video.id


def test_plain_ingest_of_quarantined_is_a_noop(tmp_path):
    # A quarantined clip (deliberately excluded as contaminated) is terminal like `done`:
    # a plain `va ingest` must dedup, NOT re-run roles and flip it back to searchable —
    # that would silently un-quarantine it. (Deliberate re-admission is `va remove` + a
    # fresh `va ingest`, NOT `va reingest` — see test_reingest_of_quarantined_is_refused.)
    from va.contracts.video import IngestStatus
    from va.pipeline.paths import Workspace
    from va.storage.structured.catalog_sqlite import Catalog

    video = _make(tmp_path)
    wd = str(tmp_path / ".va")
    first = ingest(str(video), workdir=wd, fps=1.0)
    assert first.deduped is False

    cat = Catalog(Workspace(wd).catalog_db)
    try:
        cat.set_status(first.video.id, IngestStatus.quarantined)
    finally:
        cat.close()

    again = ingest(str(video), workdir=wd, fps=1.0)
    assert again.deduped is True                 # no-op, not re-processed
    assert again.frames_indexed == 0
    assert again.video.id == first.video.id
    assert again.video.ingest_status is IngestStatus.quarantined   # stays quarantined


def test_reingest_of_quarantined_is_refused(tmp_path):
    # `va reingest` = remove + re-ingest, which would re-run roles on the same (for NVR,
    # preserved) bytes and silently re-admit a quarantined clip. reingest_video must REFUSE
    # a quarantined target BEFORE the destructive remove — so the clip and its status survive.
    import pytest

    from va.contracts.video import IngestStatus
    from va.pipeline.manage import reingest_video
    from va.pipeline.paths import Workspace
    from va.storage.structured.catalog_sqlite import Catalog

    video = _make(tmp_path)
    wd = str(tmp_path / ".va")
    first = ingest(str(video), workdir=wd, fps=1.0)
    cat = Catalog(Workspace(wd).catalog_db)
    try:
        cat.set_status(first.video.id, IngestStatus.quarantined)
    finally:
        cat.close()

    with pytest.raises(ValueError, match="quarantined"):
        reingest_video(wd, str(first.video.id))

    # refused BEFORE remove_video: the row and its quarantined status are intact
    cat = Catalog(Workspace(wd).catalog_db)
    try:
        still = cat.get(first.video.id)
    finally:
        cat.close()
    assert still is not None and still.ingest_status is IngestStatus.quarantined


def test_cli_reingest_of_quarantined_returns_2_not_traceback(tmp_path, capsys):
    # The library refusal must surface at the CLI as a clean `error: …` + exit 2
    # (like `_cmd_reprocess`), not an unhandled traceback / exit 1.
    from va.contracts.video import IngestStatus
    from va.pipeline.paths import Workspace
    from va.storage.structured.catalog_sqlite import Catalog

    video = _make(tmp_path)
    wd = str(tmp_path / ".va")
    first = ingest(str(video), workdir=wd, fps=1.0)
    cat = Catalog(Workspace(wd).catalog_db)
    try:
        cat.set_status(first.video.id, IngestStatus.quarantined)
    finally:
        cat.close()

    rc = main(["--workdir", wd, "reingest", str(first.video.id)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "quarantined" in err


def test_cli_ingest_and_query(tmp_path, capsys):
    video = _make(tmp_path)
    wd = str(tmp_path / ".va")

    assert main(["--workdir", wd, "ingest", str(video)]) == 0
    out = capsys.readouterr().out
    assert "ingested" in out

    assert main(["--workdir", wd, "query", "red sports car"]) == 0
    out = capsys.readouterr().out
    assert "youtube" in out or video.name in out or ":" in out  # prints a ranked line


def test_cli_ingest_of_quarantined_reports_quarantined_not_already_ingested(tmp_path, capsys):
    # The dedup no-op on a quarantined clip must read as excluded, not as a normal
    # already-ingested clip — and must point at `va remove` + fresh ingest, not `va reingest`.
    from va.contracts.video import IngestStatus
    from va.pipeline.paths import Workspace
    from va.storage.structured.catalog_sqlite import Catalog

    video = _make(tmp_path)
    wd = str(tmp_path / ".va")
    assert main(["--workdir", wd, "ingest", str(video)]) == 0
    capsys.readouterr()

    res_video_id = None
    cat = Catalog(Workspace(wd).catalog_db)
    try:
        res_video_id = cat.list()[0].id
        cat.set_status(res_video_id, IngestStatus.quarantined)
    finally:
        cat.close()

    assert main(["--workdir", wd, "ingest", str(video)]) == 0
    out = capsys.readouterr().out
    assert "[quarantined]" in out
    assert "already-ingested" not in out
    assert "NOT searchable" in out and "va remove" in out


def test_query_with_nothing_ingested(tmp_path):
    assert query("anything", workdir=str(tmp_path / ".va"), k=5) == []
