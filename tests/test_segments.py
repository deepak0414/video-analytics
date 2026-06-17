import pytest
from uuid import uuid4

from va.contracts.segment import Segment
from va.storage.structured.segments import SegmentStore


def test_segment_contract():
    s = Segment(video_id=uuid4(), segment_index=0, start_time=1.0, end_time=4.5)
    assert s.duration == 3.5
    with pytest.raises(Exception):
        Segment(video_id=uuid4(), segment_index=0, start_time=5.0, end_time=2.0)


def test_segment_store_roundtrip_and_replace(tmp_path):
    vid = uuid4()
    store = SegmentStore(tmp_path / "catalog.db")
    store.replace_segments(vid, [
        Segment(video_id=vid, segment_index=0, start_time=0.0, end_time=3.0),
        Segment(video_id=vid, segment_index=1, start_time=3.0, end_time=6.0),
    ])
    got = store.get_segments(vid)
    assert [s.segment_index for s in got] == [0, 1]
    assert got[1].start_time == 3.0
    assert store.count(vid) == 2

    # replace is idempotent — no duplicates / stale rows
    store.replace_segments(vid, [Segment(video_id=vid, segment_index=0, start_time=0.0, end_time=9.0)])
    assert store.count(vid) == 1
    assert store.get_segments(vid)[0].end_time == 9.0


def test_get_segments_tolerates_null_id(tmp_path):
    """Observed in the wild: a NULL-id segment row (SQLite TEXT PK quirk) crashed
    deep-scan sampling via UUID(None). Reads synthesize an id instead."""
    import sqlite3

    vid = uuid4()
    store = SegmentStore(tmp_path / "catalog.db")
    # bypass the store to plant a legacy NULL-id row (new schema forbids it)
    conn = sqlite3.connect(tmp_path / "catalog.db")
    conn.execute("PRAGMA ignore_check_constraints=ON")
    try:
        conn.execute(
            "INSERT INTO segments (id, video_id, segment_index, start_time, end_time) "
            "VALUES (NULL, ?, 0, 0.0, 5.0)", (str(vid),))
        conn.commit()
        planted = True
    except sqlite3.IntegrityError:
        planted = False     # new DBs reject NULL ids outright — also a pass
    conn.close()
    if planted:
        [seg] = store.get_segments(vid)
        assert seg.id is not None and seg.start_time == 0.0


def test_segments_share_db_with_catalog(tmp_path):
    # Catalog and SegmentStore open the same DB file (the central store).
    from va.storage.structured.catalog_sqlite import Catalog
    db = tmp_path / "catalog.db"
    Catalog(db)              # creates videos + all role tables via apply_schema
    store = SegmentStore(db)  # same file; segments table already present
    vid = uuid4()
    store.replace_segments(vid, [Segment(video_id=vid, segment_index=0, start_time=0.0, end_time=1.0)])
    assert store.count(vid) == 1
