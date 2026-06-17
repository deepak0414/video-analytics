import pytest

from va.contracts.video import SourceType
from va.sources.base import resolve_source
from va.sources.local import LocalSource
from va.sources.youtube import YoutubeSource, extract_video_id, is_youtube_url
from va.media.synth import write_color_video

VID = "dQw4w9WgXcQ"
URL_FORMS = [
    f"https://www.youtube.com/watch?v={VID}",
    f"https://youtu.be/{VID}",
    f"https://youtube.com/watch?v={VID}&t=42s",
    f"https://m.youtube.com/watch?v={VID}&list=abc",
    f"https://www.youtube.com/shorts/{VID}",
]


@pytest.mark.parametrize("url", URL_FORMS)
def test_all_url_forms_extract_same_id(url):
    assert extract_video_id(url) == VID
    assert is_youtube_url(url)


def test_youtube_resolve_dedup_key_is_video_id():
    r = YoutubeSource().resolve(f"https://youtu.be/{VID}?t=10")
    assert r.source_type is SourceType.youtube
    assert r.source_key == VID
    assert r.local_path is None  # not downloaded during resolve


def test_dispatcher_picks_youtube_for_url():
    assert isinstance(resolve_source(f"https://youtu.be/{VID}"), YoutubeSource)


def test_local_source_resolve_and_fetch(tmp_path):
    v = write_color_video(tmp_path / "clip.mp4", [("red", (220, 30, 30), 2.0)], fps=10)
    src = LocalSource()
    r = src.resolve(str(v))
    assert r.source_type is SourceType.local
    assert r.source_key.startswith("sha256:")
    assert r.local_path == str(v.resolve())

    fetched = src.fetch(r, tmp_path / "cache")
    assert fetched.metadata.resolution is not None
    assert fetched.metadata.duration_seconds >= 1.5


def test_dispatcher_picks_local_for_existing_file(tmp_path):
    v = write_color_video(tmp_path / "c.mp4", [("blue", (30, 30, 220), 1.0)], fps=10)
    assert isinstance(resolve_source(str(v)), LocalSource)


def test_unknown_source_raises():
    with pytest.raises(ValueError):
        resolve_source("ftp://nope/whatever.mp4")
