"""WS2.c — footage profiles gate roles and vocabulary at ingest.

Done-conditions from architecture-evolution-loop.md: a test ingest under a
`security`-style profile stub produces no transcript rows and uses the overridden
detector vocab; the `generic` path is unchanged.
"""
import json
from pathlib import Path

import pytest
import yaml

from va.adapters.speech_to_text.sidecar_inproc import sidecar_path
from va.configuration import load_config
from va.media.synth import write_color_video
from va.pipeline.ingest import ingest
from va.registry import get_ingest_classes
from va.storage.structured.provenance_store import ProvenanceStore

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"

SIDECAR = {"lines": [
    {"start_time": 0.0, "end_time": 1.5, "text": "welcome to the meeting"},
    {"start_time": 1.5, "end_time": 3.0, "text": "let us discuss the budget"},
]}


def _clip_with_transcript(tmp_path):
    video = write_color_video(
        tmp_path / "clip.mp4", [("red", (220, 30, 30), 3.0)], fps=10
    )
    sidecar_path(str(video)).write_text(json.dumps(SIDECAR))
    return video


def test_repo_security_profile_parses_and_gates():
    cfg = load_config(REPO_CONFIG, footage_profile="security")
    assert cfg.role("speech_to_text").enabled is False
    assert cfg.role("speaker_diarizer").enabled is False
    assert cfg.role("visual_embedder").enabled is True
    assert "person" in get_ingest_classes(cfg)
    # The overlay must not leak into the default load.
    assert load_config(REPO_CONFIG).role("speech_to_text").enabled is True


def test_security_profile_skips_speech_roles_at_ingest(tmp_path):
    # Same clip, two workdirs: generic transcribes the sidecar, security must not.
    video = _clip_with_transcript(tmp_path)

    generic = ingest(str(video), workdir=str(tmp_path / ".va-generic"), fps=1.0)
    assert generic.transcript_lines == 2  # the baseline actually has speech

    secured = ingest(
        str(video), workdir=str(tmp_path / ".va-security"),
        fps=1.0, profile="security",
    )
    assert secured.transcript_lines == 0
    assert secured.video.profile == "security"
    # The non-speech pipeline still ran.
    assert secured.frames_indexed > 0 and secured.segments > 0


def test_skipped_roles_are_not_stamped_in_provenance(tmp_path):
    # Absent provenance = stale to `va stale` — the safe direction for a role a
    # profile disables (false stale OK, missed stale forbidden).
    video = _clip_with_transcript(tmp_path)
    res = ingest(
        str(video), workdir=str(tmp_path / ".va"), fps=1.0, profile="security"
    )
    store = ProvenanceStore(tmp_path / ".va" / "catalog.db")
    try:
        stamped = {row["role"] for row in store.get(res.video.id)}
    finally:
        store.close()
    assert "speech_to_text" not in stamped
    assert "speaker_diarizer" not in stamped
    assert "scene_detector" in stamped and "visual_embedder" in stamped


def test_embedder_id_honors_the_profile_overlay(tmp_path, monkeypatch):
    # A profile overriding an embedder must change the shard TAG too, or the
    # TAG-3 guard silently drops the (correctly embedded) shard from search.
    import shutil

    from va.registry import embedder_id

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    (cdir / "profiles" / "footage" / "fake.yaml").write_text(yaml.safe_dump(
        {"roles": {"visual_embedder": {"model": "fake-model"}}}
    ))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    assert embedder_id("visual_embedder", load_config(footage_profile="fake")) == "fake-model"
    assert embedder_id("visual_embedder") == "hash"  # base config untouched


def test_security_profile_video_is_not_stale(tmp_path):
    # Stale must compare each video against ITS profile's config: skipped speech
    # roles are deliberately unstamped (not stale), and every role that ran was
    # stamped under the same overlay it will be compared against.
    from va.pipeline.stale import stale_report

    video = _clip_with_transcript(tmp_path)
    wd = str(tmp_path / ".va")
    ingest(str(video), workdir=wd, fps=1.0, profile="security")
    assert stale_report(wd) == []


def test_retry_under_gating_profile_purges_prior_rows(tmp_path):
    # Rows written by an earlier attempt must not survive a retry whose profile
    # disables the role (the "0 transcript rows under security" promise).
    import sqlite3

    from va.contracts.video import IngestStatus
    from va.storage.structured.catalog_sqlite import Catalog as Cat

    video = _clip_with_transcript(tmp_path)
    wd = tmp_path / ".va"
    first = ingest(str(video), workdir=str(wd), fps=1.0)  # generic: 2 lines
    assert first.transcript_lines == 2

    cat = Cat(wd / "catalog.db")  # simulate a not-done retry state
    cat.set_status(first.video.id, IngestStatus.processing)
    cat.close()

    retry = ingest(str(video), workdir=str(wd), fps=1.0, profile="security")
    assert retry.transcript_lines == 0
    conn = sqlite3.connect(wd / "catalog.db")
    n = conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]
    conn.close()
    assert n == 0


def test_disabled_diarizer_is_not_stamped_even_on_a_silent_video(tmp_path, monkeypatch):
    # Gate order regression: with STT enabled but NO transcript lines, a profile
    # disabling only the diarizer must still register it skipped (never stamped).
    import shutil

    from va.media.synth import write_color_video

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    (cdir / "profiles" / "footage" / "nodia.yaml").write_text(yaml.safe_dump(
        {"roles": {"speaker_diarizer": {"enabled": False}}}
    ))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    silent = write_color_video(  # no sidecar -> STT yields zero lines
        tmp_path / "silent.mp4", [("red", (220, 30, 30), 2.0)], fps=10
    )
    res = ingest(str(silent), workdir=str(tmp_path / ".va"), fps=1.0, profile="nodia")
    store = ProvenanceStore(tmp_path / ".va" / "catalog.db")
    try:
        stamped = {row["role"] for row in store.get(res.video.id)}
    finally:
        store.close()
    assert "speech_to_text" in stamped
    assert "speaker_diarizer" not in stamped


def test_profile_vocab_drives_detection_at_ingest(tmp_path, monkeypatch):
    # END-TO-END vocab proof: the profile's classes are COLOR names the stub
    # detector can ground, so detections under the profile (and none under the
    # default vocab) prove the overlaid vocab reached the ingest loop. A
    # regression to zero-arg get_ingest_classes() inside ingest fails this.
    import shutil
    import sqlite3

    from va.media.synth import write_box_video

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    (cdir / "profiles" / "footage" / "colors.yaml").write_text(yaml.safe_dump(
        {"roles": {"object_detector": {"classes": ["red", "blue"]}}}
    ))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))
    video = write_box_video(
        tmp_path / "clip.mp4", bg_rgb=(128, 128, 128), box_rgb=(220, 30, 30),
        box_frac=(0.25, 0.25, 0.5, 0.25), seconds=3.0, fps=10,
    )

    base = ingest(str(video), workdir=str(tmp_path / ".va-base"), fps=1.0)
    assert base.detections == 0  # default vocab has no color classes

    prof = ingest(
        str(video), workdir=str(tmp_path / ".va-prof"), fps=1.0, profile="colors"
    )
    assert prof.detections >= 3
    conn = sqlite3.connect(tmp_path / ".va-prof" / "catalog.db")
    stored = {r[0] for r in conn.execute(
        "SELECT DISTINCT object_class FROM object_detections")}
    conn.close()
    assert stored == {"red"}


def test_disabled_tracker_stores_untracked_detections(tmp_path, monkeypatch):
    # object_tracker: {enabled: false} with the detector ON: detections persist
    # with track_id NULL, no tracks exist, and the tracker is never stamped.
    import shutil
    import sqlite3

    from va.media.synth import write_box_video

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    (cdir / "profiles" / "footage" / "notrack.yaml").write_text(yaml.safe_dump({
        "roles": {
            "object_detector": {"classes": ["red", "blue"]},
            "object_tracker": {"enabled": False},
        }
    }))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))
    video = write_box_video(
        tmp_path / "clip.mp4", bg_rgb=(128, 128, 128), box_rgb=(220, 30, 30),
        box_frac=(0.25, 0.25, 0.5, 0.25), seconds=3.0, fps=10,
    )
    res = ingest(str(video), workdir=str(tmp_path / ".va"), fps=1.0, profile="notrack")
    assert res.detections >= 3 and res.tracks == 0

    conn = sqlite3.connect(tmp_path / ".va" / "catalog.db")
    n_tracks = conn.execute("SELECT COUNT(*) FROM object_tracks").fetchone()[0]
    untracked = conn.execute(
        "SELECT COUNT(*) FROM object_detections WHERE track_id IS NULL").fetchone()[0]
    conn.close()
    assert n_tracks == 0 and untracked == res.detections

    store = ProvenanceStore(tmp_path / ".va" / "catalog.db")
    try:
        stamped = {row["role"] for row in store.get(res.video.id)}
    finally:
        store.close()
    assert "object_detector" in stamped and "object_tracker" not in stamped


def test_core_role_cannot_be_disabled_by_a_profile(tmp_path, monkeypatch):
    # A profile disabling a CORE role must fail at load (ingest would run+stamp it
    # anyway while staleness excluded it — a missed stale, the §6-b forbidden case).
    import shutil

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    (cdir / "profiles" / "footage" / "noembed.yaml").write_text(yaml.safe_dump(
        {"roles": {"visual_embedder": {"enabled": False}}}
    ))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))
    with pytest.raises(ValueError, match="core role"):
        load_config(footage_profile="noembed")


def test_minimal_roles_yaml_still_ingests(tmp_path, monkeypatch):
    # Regression: the enabled-gate must tolerate a roles.yaml that omits roles
    # (stub fallback shape) — cfg.role() would KeyError and fail the whole ingest.
    from va.media.synth import write_color_video

    cdir = tmp_path / "config"
    (cdir / "profiles").mkdir(parents=True)
    (cdir / "roles.yaml").write_text(yaml.safe_dump({
        "active_profile": "p",
        "roles": {"visual_embedder": {"backend": "inproc", "model": "hash"}},
    }))
    (cdir / "profiles" / "p.yaml").write_text(yaml.safe_dump({"device": "cpu"}))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    clip = write_color_video(tmp_path / "c.mp4", [("red", (220, 30, 30), 2.0)], fps=10)
    res = ingest(str(clip), workdir=str(tmp_path / ".va"), fps=1.0)
    assert res.frames_indexed > 0


def test_retry_of_failed_ingest_keeps_the_recorded_profile(tmp_path):
    # A retry (row not done) WITHOUT --profile must carry the recorded profile
    # forward, mirroring reingest — not revert to the source default.
    from va.contracts.video import IngestStatus
    from va.storage.structured.catalog_sqlite import Catalog as Cat

    video = _clip_with_transcript(tmp_path)
    wd = tmp_path / ".va"
    first = ingest(str(video), workdir=str(wd), fps=1.0, profile="security")
    cat = Cat(wd / "catalog.db")  # simulate a mid-ingest failure state
    cat.set_status(first.video.id, IngestStatus.processing)
    cat.close()

    retry = ingest(str(video), workdir=str(wd), fps=1.0)  # no --profile
    assert retry.video.profile == "security"
    assert retry.transcript_lines == 0  # speech roles stayed skipped


def test_broken_profile_yaml_does_not_kill_stale_or_reprocess(tmp_path, monkeypatch):
    # A video whose recorded profile yaml was renamed must degrade per-item
    # (warn+skip in stale, failed-entry in reprocess), never abort the batch.
    import shutil

    from va.pipeline.reprocess import execute_reprocess
    from va.pipeline.stale import stale_report

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    (cdir / "profiles" / "footage" / "temp.yaml").write_text("roles: {}\n")
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip_with_transcript(tmp_path)), workdir=wd, fps=1.0,
                 profile="temp")
    (cdir / "profiles" / "footage" / "temp.yaml").unlink()  # profile renamed away

    assert stale_report(wd) == []  # skipped with a warning, not an exception

    out = execute_reprocess(wd, [{
        "video_id": str(res.video.id), "stale_roles": ["text_embedder"],
        "profile": "temp", "source_type": "local",
    }])
    assert out["reprocessed"] == []
    assert any("footage profile unavailable" in err for _, _, err in out["failed"])


def test_dependency_skipped_roles_are_not_stale(tmp_path, monkeypatch):
    # Disabling ONLY the parent (STT) dependency-skips the diarizer at ingest;
    # stale must apply the same closure or the diarizer reads stale forever.
    import shutil

    from va.pipeline.stale import stale_report

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    (cdir / "profiles" / "footage" / "nostt.yaml").write_text(yaml.safe_dump(
        {"roles": {"speech_to_text": {"enabled": False}}}
    ))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    wd = str(tmp_path / ".va")
    ingest(str(_clip_with_transcript(tmp_path)), workdir=wd, fps=1.0, profile="nostt")
    assert stale_report(wd) == []


def test_dedup_of_done_video_survives_a_broken_carried_profile(tmp_path, monkeypatch):
    # Idempotent no-flag re-ingest of a DONE video must stay a no-op even when its
    # recorded profile yaml was renamed away (only an EXPLICIT name fails fast).
    import shutil

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    (cdir / "profiles" / "footage" / "temp.yaml").write_text("roles: {}\n")
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    video = _clip_with_transcript(tmp_path)
    wd = str(tmp_path / ".va")
    ingest(str(video), workdir=wd, fps=1.0, profile="temp")
    (cdir / "profiles" / "footage" / "temp.yaml").unlink()

    res = ingest(str(video), workdir=wd, fps=1.0)  # no flag: dedup no-op, no raise
    assert res.deduped is True


def test_non_bool_enabled_raises(tmp_path, monkeypatch):
    # `enabled: "false"` (string) would run the role at ingest but exclude it from
    # staleness — a missed stale. Must be rejected at load, naming the file.
    import shutil

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    (cdir / "profiles" / "footage" / "strbool.yaml").write_text(
        'roles:\n  speech_to_text:\n    enabled: "false"\n'
    )
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))
    with pytest.raises(ValueError, match="must be a boolean"):
        load_config(footage_profile="strbool")


def test_role_disabled_after_it_ran_reads_stale(tmp_path, monkeypatch):
    # Editing a profile to disable a role AFTER videos were ingested under it:
    # the stamped rows contradict the profile and must surface as stale.
    import shutil

    from va.pipeline.stale import stale_report

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    prof = cdir / "profiles" / "footage" / "evolving.yaml"
    prof.write_text("roles: {}\n")
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    wd = str(tmp_path / ".va")
    ingest(str(_clip_with_transcript(tmp_path)), workdir=wd, fps=1.0,
           profile="evolving")
    assert stale_report(wd) == []  # everything current under the profile as ingested

    prof.write_text(yaml.safe_dump(  # the profile evolves: speech is now disabled
        {"roles": {"speech_to_text": {"enabled": False},
                   "speaker_diarizer": {"enabled": False}}}
    ))
    report = stale_report(wd)
    assert report and "speech_to_text" in report[0]["stale_roles"]


def test_reprocess_never_reruns_a_profile_disabled_role(tmp_path, monkeypatch):
    # Convergence: a role disabled AFTER ingest reads stale (rows contradict the
    # profile) but reprocess must route it to `skipped`, never re-run it — the
    # remedy is reingest (purges), not regenerating forbidden data forever.
    import shutil

    from va.pipeline.reprocess import execute_reprocess, plan_reprocess

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    prof = cdir / "profiles" / "footage" / "evolving.yaml"
    prof.write_text("roles: {}\n")
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    wd = str(tmp_path / ".va")
    ingest(str(_clip_with_transcript(tmp_path)), workdir=wd, fps=1.0,
           profile="evolving")
    prof.write_text(yaml.safe_dump(
        {"roles": {"vlm_captioner": {"enabled": False}}}
    ))
    plan = plan_reprocess(wd, all_stale=True)
    assert plan and "vlm_captioner" in plan[0]["stale_roles"]

    out = execute_reprocess(wd, plan)
    assert not any(r == "vlm_captioner" for _, r, _ in out["reprocessed"])
    assert any(r == "vlm_captioner" and "profile disables" in reason
               for _, r, reason in out["skipped"])


def test_disabled_tracker_purges_prior_rows_even_with_zero_frames(tmp_path, monkeypatch):
    # The no-live-rows invariant must hold even when the decode yields nothing:
    # prior-attempt track rows are purged whenever the tracker gate is off.
    import shutil
    import sqlite3
    import uuid

    import va.pipeline.ingest as ingest_mod
    from va.contracts.video import IngestStatus
    from va.media.synth import write_color_video
    from va.storage.structured.catalog_sqlite import Catalog as Cat

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    (cdir / "profiles" / "footage" / "notrack.yaml").write_text(yaml.safe_dump(
        {"roles": {"object_tracker": {"enabled": False}}}
    ))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    clip = write_color_video(tmp_path / "c.mp4", [("red", (220, 30, 30), 2.0)], fps=10)
    first = ingest(str(clip), workdir=str(tmp_path / ".va"), fps=1.0)  # generic
    db = tmp_path / ".va" / "catalog.db"
    conn = sqlite3.connect(db)  # simulate a prior attempt that left a track row
    conn.execute("INSERT INTO object_tracks (id, video_id, object_class) VALUES (?, ?, 'car')",
                 (str(uuid.uuid4()), str(first.video.id)))
    conn.commit()
    conn.close()
    cat = Cat(db)
    cat.set_status(first.video.id, IngestStatus.processing)
    cat.close()

    monkeypatch.setattr(ingest_mod, "sample_frames", lambda *a, **k: iter([]))
    ingest(str(clip), workdir=str(tmp_path / ".va"), fps=1.0, profile="notrack")
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM object_tracks").fetchone()[0]
    conn.close()
    assert n == 0


def test_zero_frame_ingest_still_registers_a_disabled_tracker_skip(tmp_path, monkeypatch):
    # With zero decoded frames the tracker branches never run; the gate must
    # still register the skip so the tracker is not provenance-stamped.
    import shutil

    import va.pipeline.ingest as ingest_mod
    from va.media.synth import write_color_video

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    (cdir / "profiles" / "footage" / "notrack.yaml").write_text(yaml.safe_dump(
        {"roles": {"object_tracker": {"enabled": False}}}
    ))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))
    monkeypatch.setattr(ingest_mod, "sample_frames", lambda *a, **k: iter([]))

    clip = write_color_video(tmp_path / "c.mp4", [("red", (220, 30, 30), 2.0)], fps=10)
    res = ingest(str(clip), workdir=str(tmp_path / ".va"), fps=1.0, profile="notrack")
    store = ProvenanceStore(tmp_path / ".va" / "catalog.db")
    try:
        stamped = {row["role"] for row in store.get(res.video.id)}
    finally:
        store.close()
    assert "object_tracker" not in stamped


def test_string_false_enabled_in_roles_yaml_reads_disabled_everywhere(tmp_path, monkeypatch):
    # roles.yaml (not a footage yaml) with enabled: "false": ingest must interpret
    # it exactly as stale/reprocess do (pydantic coercion -> disabled), or the
    # role is run+stamped while staleness calls it disabled — never converging.
    import shutil

    import yaml as _yaml

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    roles_yaml = cdir / "roles.yaml"
    doc = _yaml.safe_load(roles_yaml.read_text())
    doc["roles"]["speech_to_text"]["enabled"] = "false"
    roles_yaml.write_text(_yaml.safe_dump(doc))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    from va.pipeline.stale import stale_report

    res = ingest(str(_clip_with_transcript(tmp_path)), workdir=str(tmp_path / ".va"),
                 fps=1.0)
    assert res.transcript_lines == 0  # ingest treated it as disabled
    store = ProvenanceStore(tmp_path / ".va" / "catalog.db")
    try:
        stamped = {row["role"] for row in store.get(res.video.id)}
    finally:
        store.close()
    assert "speech_to_text" not in stamped
    assert stale_report(str(tmp_path / ".va")) == []  # and staleness agrees


def test_active_footage_profile_gates_roles_end_to_end(tmp_path, monkeypatch):
    # roles.yaml `active_footage_profile: security` (no --profile flag): the row
    # must record `security` AND the gated roles must actually skip at ingest.
    import shutil

    import yaml as _yaml

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    roles_yaml = cdir / "roles.yaml"
    doc = _yaml.safe_load(roles_yaml.read_text())
    doc["active_footage_profile"] = "security"
    roles_yaml.write_text(_yaml.safe_dump(doc))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    res = ingest(str(_clip_with_transcript(tmp_path)), workdir=str(tmp_path / ".va"),
                 fps=1.0)
    assert res.video.profile == "security"   # record==reality
    assert res.transcript_lines == 0         # and the profile actually gated STT


def test_profile_vocab_reaches_the_ingest_classes(tmp_path, monkeypatch):
    # The deep-merged overlay must be what get_ingest_classes serves at ingest time.
    import shutil

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    (cdir / "profiles" / "footage" / "narrow.yaml").write_text(yaml.safe_dump(
        {"roles": {"object_detector": {"classes": ["person", "package"]}}}
    ))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    assert get_ingest_classes(load_config(footage_profile="narrow")) == [
        "person", "package"
    ]
    # generic still serves the defaults
    from va.roles.object_detector import DEFAULT_INGEST_CLASSES
    assert get_ingest_classes(load_config()) == list(DEFAULT_INGEST_CLASSES)
