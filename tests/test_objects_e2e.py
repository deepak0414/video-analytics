"""End-to-end Role 5 path with the color stub: ingest a clip with a red box,
detections land in the central DB, object query + evidence assembly answer."""
from va.contracts.query_plan import QueryPlan
from va.media.synth import write_box_video
from va.pipeline.evidence import assemble
from va.pipeline.ingest import ingest
from va.pipeline.objects import query_objects


def test_ingest_detects_and_queries_objects(tmp_path, monkeypatch):
    # the stub only "detects" colors, so make the ingest vocabulary colors
    import va.pipeline.ingest as ingest_mod
    monkeypatch.setattr(ingest_mod, "get_ingest_classes", lambda: ["red", "blue"])

    video = write_box_video(
        tmp_path / "clip.mp4", bg_rgb=(128, 128, 128), box_rgb=(220, 30, 30),
        box_frac=(0.25, 0.25, 0.5, 0.25), seconds=3.0, fps=10,
    )
    wd = str(tmp_path / ".va")
    res = ingest(str(video), workdir=wd, fps=1.0)
    assert res.detections >= 3        # red box found in each sampled frame

    [summary] = query_objects("red", workdir=wd)
    assert summary.object_class == "red" and summary.frames >= 3
    assert query_objects("blue", workdir=wd) == []   # absent class

    # plural query words match singular detector classes ("reds" -> "red")
    [summary2] = query_objects("reds", workdir=wd)
    assert summary2.object_class == "red"

    # evidence assembly now executes object queries (no "not implemented" note)
    ev = assemble(QueryPlan(query="red", needs_object_query=True), workdir=wd)
    obj_items = [i for i in ev.items if i.modality == "object"]
    assert obj_items and "frames" in obj_items[0].attributes
    assert not any("Role 5" in n for n in ev.notes)
