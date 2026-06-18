"""TR.1 — the trace utility (runtime/trace.py). Offline, deterministic.

Exercises the contract that makes tracing safe to add to the pipeline:
default-OFF writes nothing, ON writes readable JSONL, the active run is a
contextvar (so `trace()` no-ops outside a run), writes are best-effort (never
raise), and render/list/prune work.
"""
import json

from va.runtime.trace import (
    current_run_id,
    list_runs,
    load_events,
    prune_traces,
    render_trace,
    trace,
    trace_stage,
    traced_run,
    tracing_enabled,
    traces_dir,
)


def test_default_off_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("VA_TRACE", raising=False)
    assert tracing_enabled() is False
    with traced_run("ingest", tmp_path) as tr:
        assert tr is None                       # no-op tracer
        trace("ocr", "read", "5 lines")         # must not raise, must not write
        assert current_run_id() is None
    assert not traces_dir(tmp_path).exists()    # zero files when off


def test_env_toggles_on(monkeypatch):
    monkeypatch.setenv("VA_TRACE", "1")
    assert tracing_enabled() is True
    monkeypatch.setenv("VA_TRACE", "0")
    assert tracing_enabled() is False
    monkeypatch.setenv("VA_TRACE", "off")
    assert tracing_enabled() is False


def test_on_writes_jsonl_and_noop_outside_run(tmp_path):
    # `enabled=True` forces tracing without touching the env
    with traced_run("ingest", tmp_path, enabled=True) as tr:
        assert tr is not None
        assert current_run_id() == tr.run_id
        trace("scene_detector", "detect", "71 segments", model="pyscenedetect", count=71)
        trace("ocr", "failed", "boom", level="error", traceback="Trace…\nValueError")
    # outside the run, the contextvar is cleared -> no-op
    assert current_run_id() is None

    files = list(traces_dir(tmp_path).glob("*.trace"))
    assert len(files) == 1
    evs = load_events(files[0])
    actions = [(e["role"], e["action"]) for e in evs]
    assert ("pipeline", "start") in actions and ("pipeline", "end") in actions
    assert ("scene_detector", "detect") in actions
    det = next(e for e in evs if e["action"] == "detect")
    assert det["details"]["count"] == 71 and det["details"]["model"] == "pyscenedetect"
    assert any(e["level"] == "error" for e in evs)
    # parsed events all carry the run's id (from the file header)
    assert all(e["run_id"] == tr.run_id for e in evs)
    # the file is human-readable text (not escaped JSON): the traceback is a real block
    text = files[0].read_text()
    assert "----- traceback -----" in text and "ValueError" in text
    assert "# legend:" in text          # self-explanatory header for [NN]/[EN]/✗/!


def test_trace_stage_records_failure_and_reraises(tmp_path):
    with traced_run("ingest", tmp_path, enabled=True):
        try:
            with trace_stage("diarize"):
                raise RuntimeError("torchcodec missing")
        except RuntimeError:
            pass  # caller's best-effort swallow — the trace already recorded it
    evs = load_events(next(traces_dir(tmp_path).glob("*.trace")))
    fail = next(e for e in evs if e["role"] == "diarize")
    assert fail["level"] == "error" and "torchcodec" in fail["summary"]
    assert "RuntimeError" in fail["details"]["traceback"]


def test_writes_are_best_effort(tmp_path, monkeypatch):
    # a broken file handle must not propagate out of event()
    with traced_run("query", tmp_path, enabled=True) as tr:
        import io
        tr._fh = io.StringIO()
        tr._fh.close()                          # writing now raises internally
        trace("retriever", "gather", "should not blow up")   # swallowed
    assert current_run_id() is None             # run still tore down cleanly


def test_render_and_list_and_prune(tmp_path):
    for _ in range(3):
        with traced_run("ask", tmp_path, enabled=True):
            trace("reasoner", "input", "prompt", reasoner_input="EVIDENCE: a red car\nQ: color?")
            trace("reasoner", "output", "answer", reasoner_output="red")

    runs = list_runs(tmp_path)
    assert len(runs) == 3 and all(r["kind"] == "ask" for r in runs)

    md = render_trace(runs[0]["path"])
    # the verbatim dump is a real-newline block, readable in-place (no JSON escaping)
    assert "# trace" in md and "----- reasoner_input -----" in md
    assert "EVIDENCE: a red car\nQ: color?" in md

    assert prune_traces(tmp_path, keep=1) == 2          # keep newest, drop 2
    assert len(list_runs(tmp_path)) == 1
    assert prune_traces(tmp_path, clear_all=True) == 1  # drop the rest
    assert list_runs(tmp_path) == []
