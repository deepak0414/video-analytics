"""End-to-end Role 7 path with the motion stub: ingest a clip with a drifting
box, a 'motion' event lands in action_events, search + evidence answer.
Deterministic, no model/network."""
from va.contracts.query_plan import QueryPlan
from va.media.synth import write_boxes_video, write_color_video
from va.pipeline.actions import search_actions
from va.pipeline.evidence import assemble
from va.pipeline.ingest import ingest


def _patch_vocab(monkeypatch):
    # the stub only grounds motion-vs-static, so make that the ingest vocabulary
    import va.pipeline.ingest as ingest_mod
    monkeypatch.setattr(
        ingest_mod, "get_ingest_actions", lambda: ["motion", "static scene"]
    )


def test_ingest_recognizes_motion_and_searches(tmp_path, monkeypatch):
    _patch_vocab(monkeypatch)
    video = write_boxes_video(
        tmp_path / "clip.mp4", bg_rgb=(128, 128, 128),
        boxes=[{"rgb": (220, 30, 30), "frac": (0.1, 0.4, 0.25, 0.25), "drift": (0.2, 0.0)}],
        seconds=3.0, fps=10,
    )
    wd = str(tmp_path / ".va")
    res = ingest(str(video), workdir=wd, fps=1.0)
    assert res.action_events >= 1

    hits = search_actions("motion", workdir=wd)
    assert hits and hits[0].action_class == "motion"
    assert search_actions("static", workdir=wd) == []

    # evidence assembly now executes action queries (tier implemented)
    ev = assemble(QueryPlan(query="motion", needs_action_query=True), workdir=wd)
    act_items = [i for i in ev.items if i.modality == "action"]
    assert act_items and act_items[0].source_role == 7
    assert not any("Role 7" in n for n in ev.notes)


def test_static_clip_yields_static_event(tmp_path, monkeypatch):
    _patch_vocab(monkeypatch)
    video = write_color_video(tmp_path / "clip.mp4", [("red", (220, 30, 30), 3.0)], fps=10)
    wd = str(tmp_path / ".va")
    res = ingest(str(video), workdir=wd, fps=1.0)
    assert res.action_events >= 1

    hits = search_actions("static scene", workdir=wd)
    assert hits and hits[0].action_class == "static scene"
    assert search_actions("motion", workdir=wd) == []
