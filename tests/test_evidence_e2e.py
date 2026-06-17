"""Evidence assembler e2e (offline): ingest a clip with stub backends, then a
QueryPlan pulls visual + caption + transcript evidence into one bundle, and
unavailable/unknown tiers are noted instead of failing."""
import json

from va.adapters.speech_to_text.sidecar_inproc import sidecar_path
from va.contracts.query_plan import QueryPlan
from va.media.synth import write_color_video
from va.pipeline.evidence import assemble
from va.pipeline.ingest import ingest


def test_assemble_multi_modal_evidence(tmp_path):
    video = write_color_video(tmp_path / "clip.mp4", [
        ("red", (220, 30, 30), 3.0),
        ("green", (30, 180, 30), 3.0),
    ], fps=10)
    sidecar_path(str(video)).write_text(json.dumps({"lines": [
        {"start_time": 0.5, "end_time": 2.0, "text": "look at the red one"},
    ]}))
    wd = str(tmp_path / ".va")
    ingest(str(video), workdir=wd, fps=1.0)

    plan = QueryPlan.model_validate({
        "query": "red",
        "needs_caption_search": True,
        "needs_transcript_search": True,
        "needs_audio_event_query": True,   # unknown future flag -> noted
    })
    ev = assemble(plan, workdir=wd, k=3)

    mods = {i.modality for i in ev.items}
    assert {"visual", "caption", "transcript"} <= mods

    # right content per modality
    cap = next(i for i in ev.items if i.modality == "caption")
    assert "red" in cap.content and i_within(cap.time_start, 0.0, 3.0)
    tx = next(i for i in ev.items if i.modality == "transcript")
    assert "red" in tx.content and tx.attributes.get("speaker") is None

    # unknown tiers were recorded, not raised
    assert any("needs_audio_event_query" in n for n in ev.notes)


def i_within(x, lo, hi):
    return lo <= x <= hi
