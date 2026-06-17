from va.adapters.scene_detector.histogram_inproc import HistogramSceneDetector
from va.media.synth import write_color_video
from va.roles.scene_detector import SceneDetector

SEGMENTS = [
    ("red", (220, 30, 30), 3.0),
    ("green", (30, 180, 30), 3.0),
    ("blue", (30, 30, 220), 3.0),
]


def test_detects_three_scenes_on_hard_cuts(tmp_path):
    v = write_color_video(tmp_path / "clip.mp4", SEGMENTS, fps=10)
    det = HistogramSceneDetector()
    assert isinstance(det, SceneDetector)
    spans = det.detect(str(v))

    # three solid-color segments -> three scenes
    assert len(spans) == 3

    # ordered, non-overlapping, covering ~[0, 9]
    assert spans[0][0] == 0.0
    for (s, e) in spans:
        assert e >= s
    for i in range(1, len(spans)):
        assert spans[i][0] == spans[i - 1][1]   # contiguous
    assert spans[-1][1] >= 8.5

    # cuts land near the 3s and 6s color changes
    assert abs(spans[0][1] - 3.0) < 0.7
    assert abs(spans[1][1] - 6.0) < 0.7


def test_single_color_is_one_scene(tmp_path):
    v = write_color_video(tmp_path / "solid.mp4", [("red", (200, 20, 20), 4.0)], fps=10)
    spans = HistogramSceneDetector().detect(str(v))
    assert len(spans) == 1
    assert spans[0][0] == 0.0
