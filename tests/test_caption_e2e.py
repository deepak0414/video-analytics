"""End-to-end Role 4 path with the color stub: ingest a multi-color clip, each
segment is captioned by dominant color, then caption search finds the segment."""
from va.media.synth import write_color_video
from va.pipeline.caption import search_captions
from va.pipeline.ingest import ingest

SEGMENTS = [
    ("red", (220, 30, 30), 3.0),
    ("green", (30, 180, 30), 3.0),
    ("blue", (30, 30, 220), 3.0),
]


def test_ingest_captions_segments_and_searches(tmp_path):
    video = write_color_video(tmp_path / "clip.mp4", SEGMENTS, fps=10)
    wd = str(tmp_path / ".va")

    res = ingest(str(video), workdir=wd, fps=1.0)
    assert res.segments == 3
    assert res.captioned_segments == 3   # Role 4 captioned every segment

    # the green segment (3-6s) is captioned "a green scene"
    hits = search_captions("green", workdir=wd)
    assert hits and hits[0].caption == "a green scene"
    assert 3.0 <= hits[0].start_time < 6.0

    assert search_captions("purple", workdir=wd) == []
