"""Ingest must PERCOLATE best-effort role failures, not swallow them silently. A failed
best-effort role (here: the detector fails to load) leaves no provenance row — the same silent
gap that hid the .va-24h Role 5/6 loss. The ingest now (1) emits a trace `ingest/degraded` event
and (2) logs a warning on the standard logger, so the degradation is visible even when VA_TRACE
is off (the condition under which the original gap went unnoticed).
"""
import glob
import logging
from pathlib import Path

from va.media.synth import write_color_video
from va.pipeline.ingest import ingest


def _boom(*a, **k):
    raise RuntimeError("detector failed to load")


def test_failed_best_effort_role_is_logged_and_traced(tmp_path, monkeypatch, caplog):
    import va.pipeline.ingest as ingest_mod
    monkeypatch.setattr(ingest_mod, "get_object_detector", _boom)  # Role 5 load fails
    monkeypatch.setenv("VA_TRACE", "1")                            # capture the trace to disk

    video = write_color_video(tmp_path / "clip.mp4", [("red", (220, 30, 30), 2.0)], fps=10)
    wd = str(tmp_path / ".va")

    with caplog.at_level(logging.WARNING):
        res = ingest(str(video), workdir=wd, fps=1.0)

    # ingest still completes (best-effort), but the failure is no longer silent:
    assert res.video is not None
    # (1) visible on the standard logger even with tracing nominally about output files
    warnings = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)
    assert "degraded" in warnings and "object_detector" in warnings

    # (2) present in the ingest trace as a degradation event
    traces = glob.glob(str(Path(wd) / "traces" / "*.trace"))
    assert traces, "no trace file written"
    text = "\n".join(open(t, encoding="utf-8").read() for t in traces)
    assert "degraded" in text and "object_detector" in text


def test_clean_ingest_emits_no_failure_warning(tmp_path, caplog):
    # a normal stub ingest has no failed roles -> no degradation warning (the log stays quiet
    # unless something actually broke).
    video = write_color_video(tmp_path / "clip.mp4", [("red", (220, 30, 30), 2.0)], fps=10)
    wd = str(tmp_path / ".va")
    with caplog.at_level(logging.WARNING):
        ingest(str(video), workdir=wd, fps=1.0)
    assert not [r for r in caplog.records
                if r.levelno >= logging.WARNING and "degraded" in r.getMessage()]
