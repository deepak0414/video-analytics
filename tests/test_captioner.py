from uuid import uuid4

from PIL import Image

from va.adapters.vlm_captioner.color_inproc import ColorCaptioner
from va.contracts.segment import Segment
from va.roles.vlm_captioner import VLMCaptioner
from va.storage.structured.segments import SegmentStore


def _solid(color):
    return Image.new("RGB", (32, 32), color)


def test_color_captioner_satisfies_protocol_and_describes_color():
    cap = ColorCaptioner()
    assert isinstance(cap, VLMCaptioner)
    assert cap.caption([_solid((220, 30, 30))]) == "a red scene"
    assert cap.caption([_solid((30, 30, 220))]) == "a blue scene"
    assert cap.caption([]) == "an empty scene"


def test_caption_store_and_search(tmp_path):
    vid = uuid4()
    store = SegmentStore(tmp_path / "catalog.db")
    segs = [
        Segment(video_id=vid, segment_index=0, start_time=0.0, end_time=3.0),
        Segment(video_id=vid, segment_index=1, start_time=3.0, end_time=6.0),
    ]
    store.replace_segments(vid, segs)
    store.set_caption(segs[0].id, "a red sports car on a track")
    store.set_caption(segs[1].id, "a calm blue ocean")

    hits = store.search_captions("sports car")
    assert len(hits) == 1
    assert hits[0].segment_index == 0 and hits[0].start_time == 0.0

    # uncaptioned/irrelevant queries return nothing
    assert store.search_captions("mountain") == []
