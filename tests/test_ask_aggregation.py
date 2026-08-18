"""Planner-integrated aggregation dispatch (typed-query tier, TQ1.h).

Offline stub-planner coverage of the full path: a QueryPlan carrying
needs_aggregation + params["aggregation"] (what an LLM planner emits — the
prompt example, verbatim) flows through dispatch_aggregation / retrieve() /
ask(), producing a CODE-COUNTED, tz-correct, evidenced answer; every
missing/invalid-argument path degrades to an honest note, never a number.

Ground truth is the same hand-derived fixture as the op tests: 4 in-window
cars — ch1 {a1, a2}, ch2 {e1, b1}.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from va.contracts.query_plan import Answer, QueryPlan
from va.contracts.track import ObjectTrack
from va.contracts.video import Camera, IngestStatus, SourceType, Video
from va.pipeline.aggregate import AGGREGATION_TOOLS, dispatch_aggregation
from va.pipeline.paths import Workspace
from va.storage.structured.cameras import CameraStore
from va.storage.structured.catalog_sqlite import Catalog
from va.storage.structured.tracks import TrackStore

W0 = 1786431600.0   # Aug-11 2026 00:00 PDT (hand-derived; see op tests)
W1 = 1786474800.0   # Aug-11 2026 12:00 PDT

GOOD_ARGS = {"op": "count_objects", "category": "cars",
             "start": "2026-08-11T00:00", "end": "2026-08-11T12:00",
             "tz": "America/Los_Angeles"}


def _video(key, camera_id, start_epoch, duration=120.0):
    return Video(source_type=SourceType.local, source_uri=f"/{key}", source_key=key,
                 camera_id=camera_id, start_epoch=start_epoch,
                 duration_seconds=duration, ingest_status=IngestStatus.done)


def _track(video_id, cls, first_seen, frames=3):
    return ObjectTrack(id=uuid4(), video_id=video_id, object_class=cls,
                       track_confidence=0.9, first_seen=first_seen,
                       last_seen=first_seen + 5.0, frame_count=frames)


@pytest.fixture()
def workdir(tmp_path):
    ws = Workspace(str(tmp_path))
    cams = CameraStore(ws.catalog_db)
    for cid in ("nvr-ch1", "nvr-ch2"):
        cams.get_or_create(Camera(id=cid, name=cid))
    cams.close()
    vids = {
        "A": _video("A", "nvr-ch1", W0 + 3600),
        "B": _video("B", "nvr-ch2", W1 - 30, duration=60.0),
        "E": _video("E", "nvr-ch2", W0),
    }
    cat = Catalog(ws.catalog_db)
    for v in vids.values():
        cat.upsert(v)
    cat.close()
    tracks = [
        _track(vids["E"].id, "car", 0.0),
        _track(vids["A"].id, "car", 10.0),
        _track(vids["A"].id, "car", 100.0),
        _track(vids["B"].id, "car", 20.0),
        _track(vids["B"].id, "car", 40.0),      # past noon -> out
    ]
    ts = TrackStore(ws.catalog_db)
    by_vid: dict = {}
    for t in tracks:
        by_vid.setdefault(t.video_id, []).append(t)
    for vid_id, ts_list in by_vid.items():
        ts.replace_tracks(vid_id, ts_list)
    ts.close()
    return str(tmp_path)


# --- dispatch_aggregation ----------------------------------------------------

def test_dispatch_count_produces_the_code_counted_line(workdir):
    items, notes = dispatch_aggregation({"aggregation": dict(GOOD_ARGS)}, workdir)
    assert len(items) == 1
    item = items[0]
    assert item.modality == "aggregate_count"
    assert item.content.startswith("CODE-COUNTED: 4 'cars' track(s)")
    # tz-correct: the window renders in the requested zone, not UTC
    assert "2026-08-11T00:00:00-07:00" in item.content
    assert "America/Los_Angeles" in item.content
    assert "nvr-ch1 2" in item.content and "nvr-ch2 2" in item.content
    assert item.attributes["total"] == 4
    assert item.attributes["per_camera"] == {"nvr-ch1": 2, "nvr-ch2": 2}
    # the full CountResult travels with the item (evidence manifest included)
    cr = item.attributes["count_result"]
    assert cr["total"] == 4 and len(cr["evidence"]) == 4
    assert any("UPPER BOUND" in n for n in notes)       # caveats surface as notes


def test_dispatch_histogram_and_events(workdir):
    hist_args = {**GOOD_ARGS, "op": "timeline_histogram", "bucket": "6h"}
    items, notes = dispatch_aggregation({"aggregation": hist_args}, workdir)
    assert items[0].attributes["total"] == 4
    assert len(items[0].attributes["buckets"]) == 2
    # the standing caveats travel with EVERY op, not just count (review r1)
    assert any("UPPER BOUND" in n for n in notes)

    ev_args = {**GOOD_ARGS, "op": "list_events", "limit": 2}
    items, notes = dispatch_aggregation({"aggregation": ev_args}, workdir)
    item = items[0]
    # a limit-capped row list must still lead with the UNTRUNCATED total
    # (review r1: 'CODE-COUNTED: 2' for a 4-event window misleads)
    assert item.attributes["total"] == 4
    assert item.attributes["rows_returned"] == 2
    assert len(item.attributes["rows"]) == 2
    assert "CODE-COUNTED: 4 'cars' event(s)" in item.content
    assert "first 2 of 4" in item.content
    assert any("UPPER BOUND" in n for n in notes)


def test_instance_dedup_caveat_travels_with_every_op(workdir):
    """dedup='instance' silently falling back on events/histogram was review
    finding r1-3 — the no-ReID caveat must surface for all ops."""
    for op in ("list_events", "timeline_histogram"):
        args = {**GOOD_ARGS, "op": op, "dedup": "instance"}
        _, notes = dispatch_aggregation({"aggregation": args}, workdir)
        assert any("re-identification" in n and "not yet available" in n
                   for n in notes), op


@pytest.mark.parametrize("mangle,why", [
    (lambda a: a.clear(), "params missing"),
    (lambda a: a.pop("tz"), "tz missing"),
    (lambda a: a.update(tz="Mars/Olympus"), "unknown tz"),
    (lambda a: a.update(start="noonish"), "bad time"),
    (lambda a: a.update(op="drop_tables"), "unknown op"),
    (lambda a: a.update(op="timeline_histogram", bucket="1w"), "bad bucket"),
    (lambda a: a.update(cameras="nvr-ch2"), "cameras not a list"),
    (lambda a: a.update(cameras=[]), "empty camera selection"),
    (lambda a: a.update(min_frames="lots"), "bad min_frames"),
    (lambda a: a.update(op="list_events", limit=None), "null limit degrades, never raises"),
    (lambda a: a.update(op="list_events", limit=[10]), "list limit degrades"),
])
def test_invalid_arguments_degrade_to_a_note_never_a_number(workdir, mangle, why):
    args = dict(GOOD_ARGS)
    mangle(args)
    items, notes = dispatch_aggregation(
        {"aggregation": args} if args else {}, workdir)
    assert items == [], why
    assert len(notes) == 1 and "no count computed" in notes[0], why


# --- retrieve() dispatch ------------------------------------------------------

def _agg_plan(query="how many cars on Aug 11 before noon"):
    return QueryPlan(query=query, needs_aggregation=True,
                     params={"aggregation": dict(GOOD_ARGS)},
                     search_terms="cars")


def test_retrieve_appends_the_aggregate_item_ungated(workdir):
    from va.pipeline.retrieval import retrieve

    ev = retrieve(_agg_plan(), workdir=workdir, k=3)
    agg = [i for i in ev.items if i.modality == "aggregate_count"]
    assert len(agg) == 1
    assert agg[0].attributes["total"] == 4
    assert any("UPPER BOUND" in n for n in ev.notes)


def test_retrieve_degrades_with_note_on_missing_args(workdir):
    from va.pipeline.retrieval import retrieve

    plan = QueryPlan(query="how many cars", needs_aggregation=True,
                     search_terms="cars")
    ev = retrieve(plan, workdir=workdir, k=3)
    assert not [i for i in ev.items if i.modality == "aggregate_count"]
    assert any("no count computed" in n for n in ev.notes)


def test_assemble_dispatches_too(workdir):
    from va.pipeline.evidence import assemble

    ev = assemble(_agg_plan(), workdir=workdir, k=3)
    assert [i for i in ev.items if i.modality == "aggregate_count"]


# --- ask() end to end (offline stub planner) ----------------------------------

class _StubReasoner:
    """What an LLM planner/reasoner pair emits, minus the LLM: the plan carries
    the aggregation tool call (prompt-example shape, verbatim); the narrator
    answers weakly on purpose — the rendered answer must still LEAD with the
    code-counted line (R11.a display discipline)."""

    def plan(self, question):
        return _agg_plan(question)

    def reason(self, question, evidence, keyframes):
        vid = next((i.video_id for i in evidence.items
                    if i.video_id is not None), None)
        agg = [i for i in evidence.items if i.modality == "aggregate_count"]
        text = "Several cars were seen that morning."
        return Answer(text=text,
                      citations=([(vid, 0.0)] if vid is not None else []),
                      attributes={"items": []} if agg else {})


def test_ask_leads_with_the_code_counted_line(workdir, monkeypatch):
    import va.pipeline.ask as ask_mod

    monkeypatch.setattr(ask_mod, "get_reasoner", lambda: _StubReasoner())
    res = ask_mod.ask("how many cars on Aug 11 before noon", workdir=workdir, k=3)
    assert res.rendered.startswith("[CODE-COUNTED: 4 'cars' track(s)")
    assert "2026-08-11T00:00:00-07:00" in res.rendered
    assert res.plan.needs_aggregation
    agg = [i for i in res.evidence.items if i.modality == "aggregate_count"]
    assert len(agg) == 1 and agg[0].attributes["total"] == 4


def _aev_only_workdir(tmp_path):
    ws = Workspace(str(tmp_path))
    video = Video(source_type=SourceType.local, source_uri="/aev",
                  source_key="aev", camera_id=None, start_epoch=None,
                  duration_seconds=120.0, ingest_status=IngestStatus.done)
    cat = Catalog(ws.catalog_db)
    cat.upsert(video)
    cat.close()
    ts = TrackStore(ws.catalog_db)
    ts.replace_tracks(video.id, [_track(video.id, "car", 10.0),
                                 _track(video.id, "car", 30.0)])
    ts.close()
    return str(tmp_path), video


def test_dispatch_on_aev_only_workdir_degrades_never_code_counts_zero(tmp_path):
    """Batch-review major: the planner path must NOT emit '[CODE-COUNTED: 0]'
    on a workdir where nothing is wall-clock-anchored — it degrades to the
    honest not-applicable note."""
    wd, _ = _aev_only_workdir(tmp_path)
    items, notes = dispatch_aggregation({"aggregation": dict(GOOD_ARGS)}, wd)
    assert items == []
    assert len(notes) == 1
    assert "no wall-clock-anchored videos" in notes[0]
    assert "2 matched 'cars' track(s)" in notes[0]
    assert "no count computed" in notes[0]


def test_ask_on_aev_only_workdir_has_no_code_counted_lead(tmp_path, monkeypatch):
    import va.pipeline.ask as ask_mod

    wd, _ = _aev_only_workdir(tmp_path)
    monkeypatch.setattr(ask_mod, "get_reasoner", lambda: _StubReasoner())
    res = ask_mod.ask("how many cars on Aug 11 before noon", workdir=wd, k=3)
    assert not res.rendered.startswith("[CODE-COUNTED")
    assert any("no wall-clock-anchored videos" in n
               for n in res.evidence.notes)


def test_code_counted_line_discloses_partially_excluded_tracks(workdir, tmp_path):
    """With anchored footage present plus un-anchored matched tracks, the
    CODE-COUNTED line itself must carry the exclusion (it is what the
    rendered answer leads with)."""
    wd = workdir
    ws = Workspace(wd)
    aev = Video(source_type=SourceType.local, source_uri="/aev2",
                source_key="aev2", camera_id=None, start_epoch=None,
                duration_seconds=120.0, ingest_status=IngestStatus.done)
    cat = Catalog(ws.catalog_db)
    cat.upsert(aev)
    cat.close()
    ts = TrackStore(ws.catalog_db)
    ts.replace_tracks(aev.id, [_track(aev.id, "car", 5.0)])
    ts.close()

    items, notes = dispatch_aggregation({"aggregation": dict(GOOD_ARGS)}, wd)
    assert items[0].content.startswith("CODE-COUNTED: 4 'cars' track(s)")
    assert "NB 1 matched track(s) on un-anchored" in items[0].content
    assert any("EXCLUDED" in n for n in notes)


class _QuotingReasoner(_StubReasoner):
    """Narrator that quotes a DIFFERENT code-counted line (the deep-scan one) —
    the aggregate lead must still be prepended (a generic 'CODE-COUNTED'
    substring probe would wrongly suppress it)."""

    def reason(self, question, evidence, keyframes):
        a = super().reason(question, evidence, keyframes)
        a.text = ("[CODE-COUNTED: 9-12 changes of the scene] " + a.text)
        return a


def test_lead_guard_keys_on_the_aggregate_line_not_any_code_counted_text(
        workdir, monkeypatch):
    import va.pipeline.ask as ask_mod

    monkeypatch.setattr(ask_mod, "get_reasoner", lambda: _QuotingReasoner())
    res = ask_mod.ask("how many cars on Aug 11 before noon", workdir=workdir, k=3)
    assert res.rendered.startswith("[CODE-COUNTED: 4 'cars' track(s)")


# --- prompt/registry drift guard ---------------------------------------------

def test_planner_prompt_renders_from_the_tool_registry():
    """The prompt is BUILT from AGGREGATION_TOOLS — every op name and the tz
    requirement must appear, so schema and prompt cannot drift apart."""
    from va.adapters.reasoner.prompts import PLANNER_PROMPT

    assert "needs_aggregation" in PLANNER_PROMPT
    for op in AGGREGATION_TOOLS:
        assert op in PLANNER_PROMPT
    assert '"tz" (REQUIRED' in PLANNER_PROMPT
    # the {query} placeholder still formats after the registry text landed
    assert "{query}" in PLANNER_PROMPT
    PLANNER_PROMPT.format(query="probe")   # raises if braces are unescaped


def test_query_plan_round_trips_needs_aggregation():
    plan = QueryPlan.model_validate(
        {"needs_aggregation": True, "params": {"aggregation": GOOD_ARGS}})
    assert plan.needs_aggregation
    assert QueryPlan.model_validate(plan.model_dump()).params["aggregation"][
        "tz"] == "America/Los_Angeles"
    assert QueryPlan().needs_aggregation is False       # default off
