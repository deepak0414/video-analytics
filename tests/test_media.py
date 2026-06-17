from va.media.frames import probe, sample_frames
from va.media.synth import write_color_video

# 3s red, 3s green, 3s blue at 10fps
SEGMENTS = [
    ("red", (220, 30, 30), 3.0),
    ("green", (30, 180, 30), 3.0),
    ("blue", (30, 30, 220), 3.0),
]


def test_probe_reports_duration_and_resolution(tmp_path):
    v = write_color_video(tmp_path / "clip.mp4", SEGMENTS, fps=10, size=(64, 48))
    meta = probe(v)
    assert meta.resolution == "64x48"
    assert meta.duration_seconds is not None and meta.duration_seconds >= 8.5


def test_sample_frames_at_1fps_count_and_timestamps(tmp_path):
    v = write_color_video(tmp_path / "clip.mp4", SEGMENTS, fps=10)
    frames = list(sample_frames(v, fps=1.0))
    # ~9s of video at 1fps -> about 9 frames (allow encoder edge slack)
    assert 8 <= len(frames) <= 10
    timestamps = [t for t, _ in frames]
    assert timestamps == sorted(timestamps)  # monotonic
    assert timestamps[0] == 0.0
