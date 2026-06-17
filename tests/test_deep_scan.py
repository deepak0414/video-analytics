"""Deep-scan (Tier 5b) tests — offline via the color stub captioner."""
from uuid import uuid4

from va.adapters.reasoner.rule_inproc import RuleReasoner
from va.media.synth import write_color_video
from va.pipeline.ask import ask
from va.pipeline.deep_scan import analyze, deep_scan_video
from va.pipeline.ingest import ingest

# red -> green -> blue -> red : 3 changes, 3 distinct states
SEGMENTS = [
    ("red", (220, 30, 30), 3.0),
    ("green", (30, 180, 30), 3.0),
    ("blue", (30, 30, 220), 3.0),
    ("red", (220, 30, 30), 3.0),
]


def test_rule_planner_triggers_deep_scan():
    plan = RuleReasoner().plan(
        "the girl in first scene, how many time she changes her dress in the entire video clip?"
    )
    # "how many time(s)" + "changes" -> deep scan, target DERIVED from the query's
    # own nouns (no canned content — CLAUDE.md "Heuristics & validation")
    assert plan.needs_deep_scan is True
    assert "dress" in plan.params["scan_target"]
    assert "girl" in plan.params["scan_target"]
    assert "woman" not in plan.params["scan_target"]   # nothing invented

    plan2 = RuleReasoner().plan("how many times does the traffic light change?")
    assert plan2.needs_deep_scan is True
    assert "traffic" in plan2.params["scan_target"]    # generalizes to other domains

    plan3 = RuleReasoner().plan("what color is the car?")
    assert plan3.needs_deep_scan is False     # plain attribute question: no sweep

    # visit/event counting triggers too (the birdfeeder cross-validation query)
    plan4 = RuleReasoner().plan("count number of birds visiting birdfeeder in the clip")
    assert plan4.needs_deep_scan is True
    assert "birds" in plan4.params["scan_target"]

    # "come and feed" phrasing (user's web query, 2026-06-11)
    plan5 = RuleReasoner().plan("How many birds come and feed on the feeder?")
    assert plan5.needs_deep_scan is True


def test_canonical_key_survives_wording_drift():
    from va.pipeline.deep_scan import canonical_key

    a = canonical_key("the dress/outfit the girl is wearing (color and style)")
    b = canonical_key("the girl's dress outfit")
    assert a == b                              # same intent -> same cached sweep
    assert canonical_key("the traffic light") != a


def test_normalization_applies_mapping_and_drops_other():
    from va.pipeline.deep_scan import analyze, normalize_observations

    class FakeReasoner:
        def _chat(self, prompt):
            return ('{"mapping": {"olive strapless": "green strapless", '
                    '"green strapless": "green strapless", '
                    '"gray suit": "OTHER", "yellow gown": "yellow dress", '
                    '"yellow dress": "yellow dress"}}')

    obs = [(0.0, "olive strapless"), (1.0, "gray suit"), (2.0, "green strapless"),
           (3.0, "yellow gown"), (4.0, "gray suit"), (5.0, "yellow dress")]
    canonical, mapping = normalize_observations(obs, "the girl dress", FakeReasoner())
    assert mapping["gray suit"] == "OTHER"
    runs, low, high, distinct = analyze(canonical)
    # olive==green merged, suits dropped, yellow gown==yellow dress merged:
    # timeline green, green, yellow, yellow -> 1 change, 2 states
    assert (low, high, distinct) == (1, 1, 2)


def test_analyze_counts_runs_and_bounds():
    obs = [(0.0, "a red scene"), (1.0, "a red scene"), (2.0, "a green scene"),
           (3.0, "a blue scene"), (4.0, "a red scene")]
    runs, low, high, distinct = analyze(obs)
    assert len(runs) == 4                      # red, green, blue, red
    assert high == 3 and low == 3              # colors aren't 'similar' -> bounds agree
    assert distinct == 3                       # red counted once

    # fuzzy merge: same garment described two ways -> low bound absorbs it
    obs2 = [(0.0, "pink dress"), (1.0, "blush pink dress"), (2.0, "blue gown")]
    _, low2, high2, _ = analyze(obs2)
    assert high2 == 2 and low2 == 1

    # 'none' frames (subject off-camera) are NOT state changes
    obs3 = [(0.0, "pink dress"), (1.0, "none"), (2.0, "none"),
            (3.0, "pink dress"), (4.0, "blue dress")]
    runs3, low3, high3, _ = analyze(obs3)
    assert len(runs3) == 2                     # pink (bridged over none), blue
    assert high3 == 1 and low3 == 1

    # temporal debounce: per-sample label flicker on the SAME subject (live bird
    # relabeled "speckled"/"striped" on alternating samples) is ONE episode
    obs4 = [(0.0, "brown speckled"), (4.0, "brown striped"), (8.0, "brown speckled"),
            (12.0, "brown striped"), (16.0, "brown speckled")]
    runs4, low4, _, _ = analyze(obs4)
    assert len(runs4) == 1 and low4 == 0       # one continuous visit

    # but a genuine A-B-A with a MULTI-sample middle stays three runs
    obs5 = [(0.0, "pink dress"), (4.0, "blue gown"), (8.0, "blue gown"),
            (12.0, "pink dress")]
    runs5, low5, _, _ = analyze(obs5)
    assert len(runs5) == 3 and low5 == 2


def test_hybrid_sampling_covers_long_takes(tmp_path):
    """A one-shot video must NOT collapse to a single sampled frame: shots
    longer than the intra-shot stride get samples every ~2s inside them."""
    from uuid import uuid4

    from va.contracts.segment import Segment
    from va.pipeline.deep_scan import _sample_timestamps
    from va.storage.structured.segments import SegmentStore

    vid = uuid4()
    store = SegmentStore(tmp_path / ".va" / "catalog.db")
    # a single 60s segment — the fixed-camera / nature-cam case
    store.replace_segments(vid, [Segment(video_id=vid, segment_index=0,
                                         start_time=0.0, end_time=60.0)])
    store.close()

    stamps = _sample_timestamps(vid, "unused.mp4", str(tmp_path / ".va"), 120, 1.0)
    assert len(stamps) >= 12                  # ~every 4s, not 1 midpoint
    assert stamps == sorted(stamps)
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert max(gaps) <= 4.5                   # no blind spans

    # edited-content shots (<= 15s: montage cuts, dialog, end-cards) get EXACTLY
    # their midpoint — extra frames of the same state add label noise (v3
    # regression: +5 phantom dresses; v4: poster art in a ~10s end-card shot)
    vid2 = uuid4()
    store = SegmentStore(tmp_path / ".va" / "catalog.db")
    store.replace_segments(vid2, [
        Segment(video_id=vid2, segment_index=0, start_time=0.0, end_time=1.0),
        Segment(video_id=vid2, segment_index=1, start_time=1.0, end_time=6.0),
        Segment(video_id=vid2, segment_index=2, start_time=6.0, end_time=16.0),
    ])
    store.close()
    assert _sample_timestamps(vid2, "unused.mp4", str(tmp_path / ".va"), 120, 1.0) == [0.5, 3.5, 11.0]


def test_ask_ors_rule_trigger_into_weak_llm_plan(tmp_path, monkeypatch):
    """Real failure (web, 2026-06-11): qwen's planner omitted needs_deep_scan on
    a counting question -> no sweep -> guessed '3' (truth 17). The rule trigger
    is now a deterministic floor under ANY planner."""
    from va.contracts.evidence import Evidence
    from va.contracts.query_plan import Answer, QueryPlan

    class WeakPlanner:  # an LLM planner that misses the escalation
        def plan(self, query):
            return QueryPlan(query=query, needs_caption_search=True)

        def reason(self, query, evidence, keyframes=()):
            return Answer(text="ok", attributes={"items": []})

    import va.pipeline.ask as ask_mod
    monkeypatch.setattr(ask_mod, "get_reasoner", lambda: WeakPlanner())
    import va.pipeline.ingest as ingest_mod

    video = write_color_video(tmp_path / "clip.mp4", SEGMENTS, fps=10)
    wd = str(tmp_path / ".va")
    ingest(str(video), workdir=wd, fps=1.0)

    res = ask("how many dresses she changes in the clip?", workdir=wd)
    assert res.plan.needs_deep_scan is True          # OR'd in despite the planner
    assert res.plan.params.get("scan_target")        # rule target adopted
    assert any(i.modality == "deep_scan_count" for i in res.evidence.items)


def test_self_escalation_on_insufficient_sparse_answer(tmp_path, monkeypatch):
    """Trigger #3: no planner flagged a deep scan, but the sparse answer admits
    insufficiency -> escalate ONCE, re-reason over dense evidence."""
    from va.contracts.query_plan import Answer, QueryPlan

    calls = {"reason": 0}

    class HumblePlanner:
        def plan(self, query):
            return QueryPlan(query=query, needs_caption_search=True)  # no deep scan

        def reason(self, query, evidence, keyframes=()):
            calls["reason"] += 1
            if calls["reason"] == 1:
                return Answer(text="The evidence is insufficient to answer.",
                              attributes={"items": []})
            # second pass: dense evidence available
            ds = [i for i in evidence.items if i.modality == "deep_scan_count"]
            return Answer(text=f"answer from {len(ds)} count item(s)",
                          attributes={"items": []})

    import va.pipeline.ask as ask_mod
    monkeypatch.setattr(ask_mod, "get_reasoner", lambda: HumblePlanner())

    video = write_color_video(tmp_path / "clip.mp4", SEGMENTS, fps=10)
    wd = str(tmp_path / ".va")
    ingest(str(video), workdir=wd, fps=1.0)

    # phrasing deliberately does NOT match the rule deep-scan triggers
    res = ask("what colors appear at the end of the video?", workdir=wd)
    assert calls["reason"] == 2                                  # escalated once
    assert any("self-escalation" in n for n in res.evidence.notes)
    assert any(i.modality == "deep_scan_count" for i in res.evidence.items)
    assert "answer from 1 count item(s)" in res.answer.text      # final = 2nd pass


def test_no_escalation_when_answer_sufficient_or_already_scanned(tmp_path, monkeypatch):
    from va.contracts.query_plan import Answer, QueryPlan

    calls = {"reason": 0}

    class ConfidentPlanner:
        def plan(self, query):
            return QueryPlan(query=query, needs_caption_search=True)

        def reason(self, query, evidence, keyframes=()):
            calls["reason"] += 1
            return Answer(text="Clearly red throughout.", citations=[],
                          attributes={"items": [{"statement": "red", "timestamp": 1.0}]})

    import va.pipeline.ask as ask_mod
    monkeypatch.setattr(ask_mod, "get_reasoner", lambda: ConfidentPlanner())

    video = write_color_video(tmp_path / "clip.mp4", SEGMENTS, fps=10)
    wd = str(tmp_path / ".va")
    ingest(str(video), workdir=wd, fps=1.0)

    res = ask("what colors appear?", workdir=wd)
    assert calls["reason"] == 1                                  # no second pass
    assert not any("self-escalation" in n for n in res.evidence.notes)

    # and a counting question (deep scan already planned by the rule floor)
    # never escalates a second time even if the answer hedges
    calls["reason"] = 0

    class HedgingPlanner(ConfidentPlanner):
        def reason(self, query, evidence, keyframes=()):
            calls["reason"] += 1
            return Answer(text="unknown", attributes={"items": []})

    monkeypatch.setattr(ask_mod, "get_reasoner", lambda: HedgingPlanner())
    res = ask("how many times does the color change?", workdir=wd)
    assert res.plan.needs_deep_scan is True
    assert calls["reason"] == 1                                  # guarded: at most one scan


def test_deep_scan_video_with_cache(tmp_path, monkeypatch):
    video = write_color_video(tmp_path / "clip.mp4", SEGMENTS, fps=10)
    wd = str(tmp_path / ".va")
    res = ingest(str(video), workdir=wd, fps=1.0)

    import va.registry as registry
    real_get = registry.get_vlm_captioner
    calls = {"n": 0}

    def counting_get(cfg=None):
        captioner = real_get(cfg)
        orig = captioner.caption

        def counted(images, prompt=None):
            calls["n"] += 1
            return orig(images, prompt)
        captioner.caption = counted
        return captioner
    monkeypatch.setattr(registry, "get_vlm_captioner", counting_get)

    first = deep_scan_video(res.video.id, str(video), "the dominant color", workdir=wd)
    assert first.cached is False
    # shot-aligned sampling: one VLM call per Role-1 segment (4 color segments)
    assert calls["n"] == 4
    assert first.changes_low == 3 and first.changes_high == 3
    assert first.distinct_states == 3
    # episodes: red appears in TWO separate runs (red,green,blue,red)
    assert first.episodes == {"a red scene": 2, "a green scene": 1, "a blue scene": 1}
    assert first.evidence_items[0].attributes["total_episodes"] == 4
    # evidence: one count item + one item per run
    assert first.evidence_items[0].modality == "deep_scan_count"
    assert first.evidence_items[0].attributes["changes_low"] == 3
    assert len([i for i in first.evidence_items if i.modality == "observation"]) == 4

    # second scan: cache hit, zero new VLM calls, identical counts
    calls["n"] = 0
    second = deep_scan_video(res.video.id, str(video), "the dominant color", workdir=wd)
    assert second.cached is True and calls["n"] == 0
    assert (second.changes_low, second.changes_high) == (3, 3)


def test_ask_uses_deep_scan_end_to_end(tmp_path, monkeypatch):
    video = write_color_video(tmp_path / "clip.mp4", SEGMENTS, fps=10)
    wd = str(tmp_path / ".va")
    ingest(str(video), workdir=wd, fps=1.0)

    res = ask("how many times does the color change in the video?", workdir=wd)
    assert res.plan.needs_deep_scan is True
    count_items = [i for i in res.evidence.items if i.modality == "deep_scan_count"]
    assert count_items and count_items[0].attributes["changes_low"] == 3
    assert any("deep-scan" in n for n in res.evidence.notes)
    # the rule reasoner surfaces the code-counted statement first
    assert "CODE-COUNTED" in res.rendered
