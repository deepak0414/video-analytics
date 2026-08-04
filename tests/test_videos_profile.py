"""WS2.b — per-ingest footage-profile selection recorded as `videos.profile`.

Done-conditions from architecture-evolution-loop.md: the v3 migration passes;
ingesting with/without the flag stores the expected profile; existing (pre-profile)
ingests read back as NULL unchanged.
"""
import shutil
import sqlite3
from pathlib import Path

import pytest

from va.media.synth import write_color_video
from va.pipeline.ingest import ingest
from va.storage.structured.catalog_sqlite import Catalog
from va.storage.structured.schema import SCHEMA_VERSION, connect

SEGMENTS = [("red", (220, 30, 30), 2.0), ("blue", (30, 30, 220), 2.0)]

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"


def _clip(tmp_path):
    return write_color_video(tmp_path / "clip.mp4", SEGMENTS, fps=10)


def test_fresh_db_has_profile_column_at_v3(tmp_path):
    conn = connect(tmp_path / "fresh.db")
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION >= 3
        cols = [r[1] for r in conn.execute("PRAGMA table_info(videos)")]
        assert "profile" in cols
    finally:
        conn.close()


def test_pre_profile_ingest_reads_back_null(tmp_path):
    # A v2-era row (no profile column) must migrate in place and read back None.
    db = tmp_path / "old.db"
    conn = connect(db)  # current schema...
    conn.close()
    raw = sqlite3.connect(db)  # ...then simulate a pre-v3 DB
    raw.execute("ALTER TABLE videos DROP COLUMN profile")
    raw.execute("PRAGMA user_version = 2")
    raw.execute(
        "INSERT INTO videos (id, source_type, source_uri, source_key, ingest_status,"
        " created_at) VALUES ('00000000-0000-0000-0000-000000000001', 'local', '/x',"
        " 'k1', 'done', '2026-01-01T00:00:00+00:00')"
    )
    raw.commit()
    raw.close()

    cat = Catalog(db)  # opening migrates v2 -> v3
    try:
        v = cat.get_by_source_key("k1")
        assert v is not None and v.profile is None
    finally:
        cat.close()


def test_ingest_without_flag_stores_source_derived_generic(tmp_path):
    res = ingest(str(_clip(tmp_path)), workdir=str(tmp_path / ".va"), fps=1.0)
    cat = Catalog(tmp_path / ".va" / "catalog.db")
    try:
        assert cat.get(res.video.id).profile == "generic"
    finally:
        cat.close()


def test_ingest_with_explicit_profile_stores_it(tmp_path, monkeypatch):
    # A named profile must exist as a footage yaml — build a config dir that has one.
    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    fdir = cdir / "profiles" / "footage"
    fdir.mkdir(exist_ok=True)
    (fdir / "security.yaml").write_text("roles: {}\n")
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    res = ingest(
        str(_clip(tmp_path)), workdir=str(tmp_path / ".va"),
        fps=1.0, profile="security",
    )
    cat = Catalog(tmp_path / ".va" / "catalog.db")
    try:
        assert cat.get(res.video.id).profile == "security"
    finally:
        cat.close()


def _config_with_security_profile(tmp_path, monkeypatch):
    cdir = tmp_path / "config"
    if not cdir.exists():
        shutil.copytree(REPO_CONFIG, cdir)
        fdir = cdir / "profiles" / "footage"
        fdir.mkdir(exist_ok=True)
        (fdir / "security.yaml").write_text("roles: {}\n")
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))


def test_reingest_carries_the_recorded_profile_forward(tmp_path, monkeypatch):
    # The documented maintenance flow (`va reingest`) must not silently reset a
    # recorded profile to the source-derived default.
    from va.pipeline.manage import reingest_video

    _config_with_security_profile(tmp_path, monkeypatch)
    wd = str(tmp_path / ".va")
    ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0, profile="security")

    res = reingest_video(wd, str(_clip(tmp_path)), fps=1.0)  # no profile arg
    cat = Catalog(tmp_path / ".va" / "catalog.db")
    try:
        assert cat.get(res.video.id).profile == "security"
    finally:
        cat.close()

    # An explicit override still wins.
    res = reingest_video(wd, str(_clip(tmp_path)), fps=1.0, profile="generic")
    cat = Catalog(tmp_path / ".va" / "catalog.db")
    try:
        assert cat.get(res.video.id).profile == "generic"
    finally:
        cat.close()


def test_roles_yaml_active_footage_profile_is_what_gets_recorded(tmp_path, monkeypatch):
    # Roles self-load config, which applies roles.yaml `active_footage_profile` —
    # the row must record that same name, not the source default (record==reality).
    import yaml

    _config_with_security_profile(tmp_path, monkeypatch)
    roles_yaml = tmp_path / "config" / "roles.yaml"
    doc = yaml.safe_load(roles_yaml.read_text())
    doc["active_footage_profile"] = "security"
    roles_yaml.write_text(yaml.safe_dump(doc))

    res = ingest(str(_clip(tmp_path)), workdir=str(tmp_path / ".va"), fps=1.0)
    cat = Catalog(tmp_path / ".va" / "catalog.db")
    try:
        assert cat.get(res.video.id).profile == "security"
    finally:
        cat.close()


def test_reingest_with_bad_profile_leaves_the_video_intact(tmp_path):
    # Validation must run BEFORE the destructive removal.
    from va.pipeline.manage import reingest_video

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    with pytest.raises(FileNotFoundError, match="footage profile 'secruity'"):
        reingest_video(wd, str(_clip(tmp_path)), fps=1.0, profile="secruity")
    cat = Catalog(tmp_path / ".va" / "catalog.db")
    try:
        v = cat.get(res.video.id)
        assert v is not None and v.ingest_status.value == "done"
    finally:
        cat.close()


def test_reingest_of_pre_profile_video_with_broken_active_profile_leaves_it_intact(
    tmp_path, monkeypatch
):
    # No --profile and a NULL recorded profile: validation must still run before
    # removal, because ingest's own probe resolves roles.yaml active_footage_profile
    # — if that names a missing yaml, the failure has to come pre-destroy.
    import yaml

    from va.pipeline.manage import reingest_video

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    # Simulate a pre-profile-era row (ingested before the column existed).
    raw = sqlite3.connect(tmp_path / ".va" / "catalog.db")
    raw.execute("UPDATE videos SET profile = NULL")
    raw.commit()
    raw.close()

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    roles_yaml = cdir / "roles.yaml"
    doc = yaml.safe_load(roles_yaml.read_text())
    doc["active_footage_profile"] = "renamed-away"
    roles_yaml.write_text(yaml.safe_dump(doc))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    with pytest.raises(FileNotFoundError, match="footage profile 'renamed-away'"):
        reingest_video(wd, str(_clip(tmp_path)), fps=1.0)
    cat = Catalog(tmp_path / ".va" / "catalog.db")
    try:
        v = cat.get(res.video.id)
        assert v is not None and v.ingest_status.value == "done"
    finally:
        cat.close()


def test_dedup_path_still_validates_an_explicit_profile(tmp_path):
    # "[already-ingested]" must not swallow a typo'd profile name.
    wd = str(tmp_path / ".va")
    ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    with pytest.raises(FileNotFoundError, match="footage profile 'secruity'"):
        ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0, profile="secruity")


def test_ingest_with_unknown_profile_fails_before_any_work(tmp_path):
    wd = tmp_path / ".va"
    with pytest.raises(FileNotFoundError, match="footage profile 'nope'"):
        ingest(str(_clip(tmp_path)), workdir=str(wd), fps=1.0, profile="nope")
    # It failed at validation: the row never advanced past creation.
    cat = Catalog(wd / "catalog.db")
    try:
        vids = cat.list()
        assert all(v.ingest_status.value != "done" for v in vids)
        assert all(v.profile is None for v in vids)
    finally:
        cat.close()
