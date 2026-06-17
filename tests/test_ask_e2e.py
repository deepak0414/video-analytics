"""ask pipeline e2e (offline, rule reasoner + stub backends): plan -> evidence
-> keyframes -> reason -> rendered answer with timestamp references."""
import json

from va.adapters.speech_to_text.sidecar_inproc import sidecar_path
from va.media.synth import write_boxes_video
from va.pipeline.ask import ask
from va.pipeline.ingest import ingest


def _setup(tmp_path, monkeypatch):
    import va.pipeline.ingest as ingest_mod
    monkeypatch.setattr(ingest_mod, "get_ingest_classes", lambda: ["red", "blue"])
    video = write_boxes_video(
        tmp_path / "clip.mp4", bg_rgb=(128, 128, 128),
        boxes=[
            {"rgb": (220, 30, 30), "frac": (0.1, 0.1, 0.3, 0.3)},
            {"rgb": (30, 30, 220), "frac": (0.6, 0.6, 0.3, 0.3)},
        ],
        seconds=4.0, fps=10,
    )
    sidecar_path(str(video)).write_text(json.dumps({"lines": [
        {"start_time": 1.0, "end_time": 2.0, "text": "the red box is next to the blue box"},
    ]}))
    wd = str(tmp_path / ".va")
    ingest(str(video), workdir=wd, fps=1.0)
    return wd


def test_ask_counting_question(tmp_path, monkeypatch):
    wd = _setup(tmp_path, monkeypatch)
    res = ask("how many distinct red objects are there?", workdir=wd)

    assert res.plan.needs_object_query is True       # planner classified it
    mods = {i.modality for i in res.evidence.items}
    assert "object_count" in mods                    # evidence includes counts
    assert res.answer.citations                      # cited
    assert "distinct" in res.rendered                # extractive statement
    assert "[0:0" in res.rendered                    # a local-file timestamp ref


def test_ask_collects_keyframes_and_co_occurrence(tmp_path, monkeypatch):
    wd = _setup(tmp_path, monkeypatch)
    res = ask("what is near the red and blue objects?", workdir=wd)

    # red+blue co-occur every frame -> temporal-join evidence present
    assert any(i.modality == "co_occurrence" for i in res.evidence.items)
    # keyframes were extracted to disk for the reasoner (layout v2: per-video dirs)
    from pathlib import Path
    assert any(Path(wd, "videos").glob("*/keyframes/*.png"))


def test_ask_transcript_question(tmp_path, monkeypatch):
    wd = _setup(tmp_path, monkeypatch)
    res = ask("what did they say about the red box?", workdir=wd)
    assert res.plan.needs_transcript_search is True
    assert any(i.modality == "transcript" for i in res.evidence.items)
    assert "red box" in res.rendered                 # transcript line quoted
