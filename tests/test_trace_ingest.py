"""TR.2 — ingest instrumentation, end-to-end with stub backends.

With VA_TRACE=1, ingest() brackets the run with a per-role event, and — the
headline — a best-effort role that RAISES surfaces as a visible warn (previously
these `except` blocks vanished silently) while the ingest still completes.
"""
from va.media.synth import write_color_video
from va.pipeline import ingest as ingest_mod
from va.pipeline.ingest import ingest
from va.runtime.trace import list_runs, load_events, render_trace, traces_dir


def _clip(tmp_path):
    return write_color_video(
        tmp_path / "clip.mp4",
        [("red", (220, 30, 30), 3.0), ("green", (30, 180, 30), 3.0)],
        fps=10,
    )


def test_ingest_emits_per_role_trace(tmp_path, monkeypatch):
    monkeypatch.setenv("VA_TRACE", "1")
    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    assert res.frames_indexed > 0

    runs = [r for r in list_runs(wd) if r["kind"] == "ingest"]
    assert runs, "ingest() should write a trace under VA_TRACE=1"
    actions = {(e["role"], e["action"]) for e in load_events(runs[0]["path"])}
    assert ("pipeline", "start") in actions and ("pipeline", "end") in actions
    assert ("scene", "segments") in actions
    assert ("ingest", "decode") in actions          # the decode-once point, now live
    assert ("ingest", "done") in actions


def test_ingest_surfaces_swallowed_role_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("VA_TRACE", "1")

    class Boom:
        def read(self, path):
            raise RuntimeError("ocr backend exploded")

    monkeypatch.setattr(ingest_mod, "get_ocr_reader", lambda: Boom())
    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)

    # best-effort preserved: the ingest still completed despite the failure
    assert res.frames_indexed > 0 and res.ocr_lines == 0

    evs = load_events([r for r in list_runs(wd) if r["kind"] == "ingest"][0]["path"])
    fail = next(e for e in evs if e["role"] == "ocr" and e["action"] == "failed")
    assert fail["level"] == "warn"
    assert "ocr backend exploded" in fail["summary"]
    assert "traceback" in fail["details"]           # full traceback captured, not just a count
    # and it surfaces at the top of the rendered view
    assert "degradation" in render_trace(
        [r for r in list_runs(wd) if r["kind"] == "ingest"][0]["path"])


def test_ingest_not_traced_when_off(tmp_path, monkeypatch):
    monkeypatch.delenv("VA_TRACE", raising=False)
    wd = str(tmp_path / ".va")
    ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    assert not traces_dir(wd).exists()              # default-off writes nothing
