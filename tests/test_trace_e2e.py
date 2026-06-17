"""TR.3 — query/ask instrumentation, end-to-end with stub backends.

With VA_TRACE=1, an `ask()` over a synth clip must produce a trace whose events
reconstruct the run: the plan, the retriever stages, and — the headline — the
VERBATIM text handed to the reasoner plus its raw output.
"""
import json

from va.adapters.speech_to_text.sidecar_inproc import sidecar_path
from va.media.synth import write_boxes_video
from va.pipeline.ask import ask
from va.pipeline.ingest import ingest
from va.runtime.trace import list_runs, load_events, traces_dir


def _ingest_clip(tmp_path, monkeypatch):
    monkeypatch.setattr("va.pipeline.ingest.get_ingest_classes", lambda: ["red", "blue"])
    video = write_boxes_video(
        tmp_path / "clip.mp4", bg_rgb=(128, 128, 128),
        boxes=[{"rgb": (220, 30, 30), "frac": (0.1, 0.1, 0.3, 0.3)}],
        seconds=3.0, fps=10,
    )
    sidecar_path(str(video)).write_text(json.dumps({"lines": [
        {"start_time": 1.0, "end_time": 2.0, "text": "the red box is here"},
    ]}))
    wd = str(tmp_path / ".va")
    ingest(str(video), workdir=wd, fps=1.0)
    return wd


def test_ask_emits_reconstructable_trace(tmp_path, monkeypatch):
    monkeypatch.setenv("VA_TRACE", "1")
    wd = _ingest_clip(tmp_path, monkeypatch)

    res = ask("what did they say about the red box?", workdir=wd)
    assert res.answer is not None

    runs = [r for r in list_runs(wd) if r["kind"] == "ask"]
    assert runs, "ask() should have written a trace under VA_TRACE=1"
    evs = load_events(runs[0]["path"])
    actions = {(e["role"], e["action"]) for e in evs}

    # the run is bracketed, the plan + retriever stages + the reasoner I/O are there
    assert ("pipeline", "start") in actions and ("pipeline", "end") in actions
    assert ("planner", "plan") in actions
    assert ("retriever", "gathered") in actions and ("retriever", "fuse") in actions
    assert ("reasoner", "input") in actions and ("reasoner", "output") in actions

    # the headline: the verbatim reasoner input is captured (not just a summary)
    inp = next(e for e in evs if e["role"] == "reasoner" and e["action"] == "input")
    assert inp["details"]["reasoner_input"]                     # non-empty render
    out = next(e for e in evs if e["role"] == "reasoner" and e["action"] == "output")
    assert "reasoner_output" in out["details"]

    # per-role provenance: gather events preview their items with a source_role,
    # so you can see which evidence came from which role at collection time
    gv = next(e for e in evs if e["action"] == "gather:visual")
    assert gv["details"]["items"] and all("role" in it for it in gv["details"]["items"])


def test_ask_not_traced_when_off(tmp_path, monkeypatch):
    monkeypatch.delenv("VA_TRACE", raising=False)
    wd = _ingest_clip(tmp_path, monkeypatch)
    ask("what did they say about the red box?", workdir=wd)
    assert not traces_dir(wd).exists()                          # default-off writes nothing
