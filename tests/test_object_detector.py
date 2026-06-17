from uuid import uuid4

from PIL import Image
import numpy as np

from va.adapters.object_detector.color_inproc import ColorDetector
from va.contracts.detection import Detection
from va.roles.object_detector import ObjectDetector
from va.storage.structured.detections import DetectionStore


def _box_image(bg, fg, frac=(0.25, 0.25, 0.5, 0.25), size=(64, 64)):
    w, h = size
    arr = np.zeros((h, w, 3), np.uint8)
    arr[:, :] = bg
    x0, y0 = int(frac[0] * w), int(frac[1] * h)
    arr[y0:y0 + int(frac[3] * h), x0:x0 + int(frac[2] * w)] = fg
    return Image.fromarray(arr)


def test_color_detector_finds_box_with_accurate_bbox():
    det = ColorDetector()
    assert isinstance(det, ObjectDetector)
    img = _box_image(bg=(128, 128, 128), fg=(220, 30, 30))  # red box on gray
    [dets] = det.detect([img], ["red", "blue"])
    assert len(dets) == 1                      # only red found, blue absent
    d = dets[0]
    assert d.object_class == "red"
    assert abs(d.bbox_x - 0.25) < 0.05 and abs(d.bbox_y - 0.25) < 0.05
    assert abs(d.bbox_w - 0.50) < 0.05 and abs(d.bbox_h - 0.25) < 0.05


def test_color_detector_empty_when_class_absent():
    det = ColorDetector()
    img = _box_image(bg=(128, 128, 128), fg=(220, 30, 30))
    [dets] = det.detect([img], ["blue", "person"])  # person isn't a color -> skip
    assert dets == []


def test_detection_store_summarize(tmp_path):
    vid = uuid4()
    store = DetectionStore(tmp_path / "catalog.db")
    mk = lambda ts, cls: Detection(
        video_id=vid, timestamp=ts, object_class=cls, confidence=0.8,
        bbox_x=0.1, bbox_y=0.1, bbox_w=0.2, bbox_h=0.2,
    )
    store.replace_detections(vid, [mk(0.0, "red"), mk(1.0, "red"), mk(1.0, "blue")])
    assert store.count(vid) == 3

    [red] = store.summarize(["red"])
    assert red.frames == 2 and red.first_seen == 0.0 and red.last_seen == 1.0

    # idempotent replace
    store.replace_detections(vid, [mk(2.0, "red")])
    assert store.count(vid) == 1
