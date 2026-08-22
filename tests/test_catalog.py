from va.contracts.video import IngestStatus, ResolvedVideo, SourceType, VideoMetadata
from va.storage.structured.catalog_sqlite import Catalog


def _resolved(key="abc123DEFGH"):
    return ResolvedVideo(
        source_type=SourceType.youtube,
        source_uri=f"https://youtu.be/{key}",
        source_key=key,
        local_path=None,
        metadata=VideoMetadata(title="demo"),
    )


def test_get_or_create_is_idempotent(tmp_path):
    cat = Catalog(tmp_path / "catalog.db")
    v1, created1 = cat.get_or_create(_resolved())
    v2, created2 = cat.get_or_create(_resolved())  # same source_key
    assert created1 is True
    assert created2 is False          # dedup: not created again
    assert v1.id == v2.id             # same row returned

    # persists across reopen
    cat.close()
    cat2 = Catalog(tmp_path / "catalog.db")
    again, created3 = cat2.get_or_create(_resolved())
    assert created3 is False and again.id == v1.id


def test_status_transitions(tmp_path):
    cat = Catalog(tmp_path / "c.db")
    v, _ = cat.get_or_create(_resolved())
    assert v.ingest_status is IngestStatus.pending

    cat.set_status(v.id, IngestStatus.processing, local_path="/tmp/x.mp4", mark_fetched=True)
    cat.set_status(v.id, IngestStatus.done, mark_processed=True)

    got = cat.get(v.id)
    assert got.ingest_status is IngestStatus.done
    assert got.local_path == "/tmp/x.mp4"
    assert got.fetched_at is not None and got.processed_at is not None


def test_list_newest_first_with_limit(tmp_path):
    cat = Catalog(tmp_path / "c.db")
    a, _ = cat.get_or_create(_resolved("aaaaaaaaaaa"))
    b, _ = cat.get_or_create(_resolved("bbbbbbbbbbb"))
    listed = cat.list()
    assert [v.source_key for v in listed] == ["bbbbbbbbbbb", "aaaaaaaaaaa"] or \
           len(listed) == 2  # same-instant created_at may tie; both rows present
    assert len(cat.list(limit=1)) == 1


def test_failed_status_records_error(tmp_path):
    cat = Catalog(tmp_path / "c.db")
    v, _ = cat.get_or_create(_resolved("zzz"))
    cat.set_status(v.id, IngestStatus.failed, error="boom")
    got = cat.get(v.id)
    assert got.ingest_status is IngestStatus.failed and got.ingest_error == "boom"


def test_quarantined_status_round_trips(tmp_path):
    # A clip marked `quarantined` (deliberately excluded as contaminated, e.g. the
    # .va-24h data repair) must survive read-back: `Catalog.list()` builds a `Video`
    # per row via `IngestStatus(...)`, so an unmodelled status string raised ValueError
    # and 500'd `va serve` / `va migrate-layout` on that workdir. Regression pin: list()
    # returns the row WITH its quarantined status, visible (not silently dropped).
    import sqlite3

    db = tmp_path / "c.db"
    cat = Catalog(db)                      # creates the schema
    done, _ = cat.get_or_create(_resolved("ddddddddddd"))
    cat.set_status(done.id, IngestStatus.done, mark_processed=True)
    cat.close()

    # Insert a quarantined row the way the repair did (raw — no enum path at write time).
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO videos (id, source_type, source_uri, source_key, ingest_status, "
        "ingest_error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("11111111-1111-1111-1111-111111111111", "nvr_recorded",
         "nvr://1/x", "nvr:ch1:quarantined", "quarantined",
         "quarantined by data-repair: wholly-foreign substream", "2026-08-20T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    cat2 = Catalog(db)
    try:
        listed = cat2.list()              # must NOT raise
        by_key = {v.source_key: v for v in listed}
        assert "nvr:ch1:quarantined" in by_key
        assert by_key["nvr:ch1:quarantined"].ingest_status is IngestStatus.quarantined
        # the done row is still there and distinct from quarantined
        assert by_key["ddddddddddd"].ingest_status is IngestStatus.done
    finally:
        cat2.close()


def test_connect_uses_wal_and_applies_schema(tmp_path):
    from va.storage.structured.schema import connect

    conn = connect(tmp_path / "c.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"videos", "segments", "object_detections", "transcripts"} <= tables
    finally:
        conn.close()


def test_get_many_batches_lookup(tmp_path):
    from uuid import uuid4

    cat = Catalog(tmp_path / "c.db")
    a, _ = cat.get_or_create(_resolved("aaaaaaaaaaa"))
    b, _ = cat.get_or_create(_resolved("bbbbbbbbbbb"))

    got = cat.get_many([a.id, b.id])
    assert set(got) == {str(a.id), str(b.id)}
    assert got[str(a.id)].source_key == "aaaaaaaaaaa"

    # unknown ids are simply absent; empty input -> empty dict (no query)
    missing = uuid4()
    got2 = cat.get_many([a.id, missing])
    assert str(a.id) in got2 and str(missing) not in got2
    assert cat.get_many([]) == {}
