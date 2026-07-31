"""role_provenance table + its v2 migration + the ProvenanceStore (WS-1 §6-b, PROV-2).
Offline: pure SQLite, no models.
"""
import sqlite3

from va.storage.structured.provenance_store import ProvenanceStore
from va.storage.structured.schema import SCHEMA_VERSION, connect


def test_v2_migration_adds_role_provenance_to_an_old_db(tmp_path):
    # a pre-provenance (v1) DB gains role_provenance on open, stamped to the current version
    p = tmp_path / "old.db"
    raw = sqlite3.connect(p)
    raw.execute("CREATE TABLE videos (id TEXT PRIMARY KEY, source_key TEXT)")
    raw.execute("PRAGMA user_version = 1")
    raw.commit()
    raw.close()

    conn = connect(p)   # apply_schema runs the migration runner
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "role_provenance" in tables
    finally:
        conn.close()


def test_record_and_get_round_trips(tmp_path):
    store = ProvenanceStore(tmp_path / "catalog.db")
    try:
        store.record("vid-1", "visual_embedder", "siglip", "fp-abc",
                     fps=1.0, run_id="run-9", row_count=42)
        rows = store.get("vid-1")
        assert len(rows) == 1
        r = rows[0]
        assert (r["role"], r["model"], r["fingerprint"]) == ("visual_embedder", "siglip", "fp-abc")
        assert r["fps"] == 1.0 and r["run_id"] == "run-9" and r["row_count"] == 42
        assert r["produced_at"]                        # auto-stamped by the DB
    finally:
        store.close()


def test_record_upserts_on_video_and_role(tmp_path):
    store = ProvenanceStore(tmp_path / "catalog.db")
    try:
        store.record("vid-1", "vlm_captioner", "qwen2.5-vl-7b", "fp-old")
        store.record("vid-1", "vlm_captioner", "qwen3-vl-30b-a3b", "fp-new")  # reprocessed
        rows = store.get("vid-1", role="vlm_captioner")
        assert len(rows) == 1                          # replaced, not duplicated
        assert rows[0]["fingerprint"] == "fp-new"
    finally:
        store.close()


def test_get_scopes_to_the_video(tmp_path):
    store = ProvenanceStore(tmp_path / "catalog.db")
    try:
        store.record("vid-1", "ocr", "rapidocr", "fp-1")
        store.record("vid-1", "speech_to_text", "whisper", "fp-2")
        store.record("vid-2", "ocr", "rapidocr", "fp-1")   # a different video
        assert {r["role"] for r in store.get("vid-1")} == {"ocr", "speech_to_text"}
        assert len(store.get("vid-2")) == 1
    finally:
        store.close()
