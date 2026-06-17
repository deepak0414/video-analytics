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


def test_cli_ingest_and_query(tmp_path, capsys):
    video = _make(tmp_path)
    wd = str(tmp_path / ".va")

    assert main(["--workdir", wd, "ingest", str(video)]) == 0
    out = capsys.readouterr().out
    assert "ingested" in out

    assert main(["--workdir", wd, "query", "red sports car"]) == 0
    out = capsys.readouterr().out
    assert "youtube" in out or video.name in out or ":" in out  # prints a ranked line


def test_query_with_nothing_ingested(tmp_path):
    assert query("anything", workdir=str(tmp_path / ".va"), k=5) == []
