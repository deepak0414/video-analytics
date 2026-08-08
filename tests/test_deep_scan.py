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


def test_deep_scan_cache_busts_on_captioner_upgrade(tmp_path, monkeypatch):
    """A vlm_captioner upgrade must RE-RUN the sweep, not serve old-model captions.

    Guards the PROV-3 missed-stale fix: deep-scan folds the captioner fingerprint into
    its `observations` cache key, so a model bump busts the cache. Without it, `va ask`
    would keep serving CODE-COUNTED answers from stale captions forever — a missed stale
    invisible to `va stale`. The stable-key cache-HIT is covered above; this asserts the
    complementary invalidation, so a refactor that drops the fingerprint fails loudly.
    """
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

    # first sweep: fresh, one VLM call per Role-1 segment; second: cache hit, zero calls
    first = deep_scan_video(res.video.id, str(video), "the dominant color", workdir=wd)
    assert first.cached is False and calls["n"] == 4
    calls["n"] = 0
    second = deep_scan_video(res.video.id, str(video), "the dominant color", workdir=wd)
    assert second.cached is True and calls["n"] == 0

    # simulate a captioner upgrade: its fingerprint changes -> the cache key must change,
    # forcing a full re-sweep (else old-model captions would silently survive).
    import va.provenance as provenance
    real_fp = provenance.role_fingerprint

    def upgraded_fp(role, cfg=None):
        if role == "vlm_captioner":
            return {"model": "qwen3-vl-30b", "fingerprint": "upgraded00000000"}
        return real_fp(role, cfg)
    monkeypatch.setattr(provenance, "role_fingerprint", upgraded_fp)

    calls["n"] = 0
    third = deep_scan_video(res.video.id, str(video), "the dominant color", workdir=wd)
    assert third.cached is False and calls["n"] == 4


def test_deep_scan_map_cache_busts_on_reasoner_upgrade(tmp_path, monkeypatch):
    """A reasoner upgrade must RE-RUN normalization, not reuse the OLD reasoner's cached
    label mapping. Sibling of the captioner-fold test above: that guards prompt_key (the
    caption sweep); this guards map_key (the normalization mapping). Without the reasoner
    fingerprint in map_key, switching reasoners on a shared workdir would keep serving
    code-counted answers normalized by the old model — the missed-stale class §6-b forbids.
    """
    video = write_color_video(tmp_path / "clip.mp4", SEGMENTS, fps=10)
    wd = str(tmp_path / ".va")
    res = ingest(str(video), workdir=wd, fps=1.0)

    calls = {"n": 0}

    class CountingReasoner:
        def _chat(self, prompt):
            calls["n"] += 1
            # a non-empty mapping so the normalization result is CACHED under map_key
            return '{"mapping": {"a red scene": "a red scene"}}'

    reasoner = CountingReasoner()

    # run 1: fresh sweep + normalization -> mapping cached under map_key
    first = deep_scan_video(res.video.id, str(video), "the dominant color",
                            workdir=wd, reasoner=reasoner)
    assert first.cached is False and calls["n"] >= 1        # normalization ran

    # run 2: same config -> observations AND map cache hit -> normalization skipped
    calls["n"] = 0
    deep_scan_video(res.video.id, str(video), "the dominant color",
                    workdir=wd, reasoner=reasoner)
    assert calls["n"] == 0                                  # map_key hit -> no re-normalize

    # run 3: reasoner fingerprint changes (a model switch), captioner UNCHANGED -> the
    # observations cache still hits (prompt_key stable) but map_key must bust.
    import va.provenance as provenance
    real_fp = provenance.role_fingerprint

    def upgraded_fp(role, cfg=None):
        if role == "reasoner":
            return {"model": "claude-code", "fingerprint": "reasoner-upgraded0"}
        return real_fp(role, cfg)
    monkeypatch.setattr(provenance, "role_fingerprint", upgraded_fp)

    calls["n"] = 0
    third = deep_scan_video(res.video.id, str(video), "the dominant color",
                            workdir=wd, reasoner=reasoner)
    assert third.cached is True                             # captioner fp unchanged -> sweep cached
    assert calls["n"] >= 1                                  # but map_key changed -> re-normalized


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


# --- R11.a: the outfit hijack is dead ---------------------------------------

def test_vetoed_escalation_does_not_re_reason(tmp_path, monkeypatch):
    """Round-4 review: a self-escalation whose sweep is vetoed (profile gate /
    no target) leaves evidence identical — a second reasoner pass over it buys
    nothing and costs another full LLM round trip."""
    import va.pipeline.ask as ask_mod
    from va.contracts.query_plan import Answer, QueryPlan

    calls = {"reason": 0}

    class Humble:
        def plan(self, query):
            return QueryPlan(query=query, needs_caption_search=True)

        def reason(self, query, evidence, keyframes=()):
            calls["reason"] += 1
            return Answer(text="I cannot tell from the available evidence.",
                          attributes={"items": []})

    monkeypatch.setattr(ask_mod, "get_reasoner", lambda: Humble())
    video = write_color_video(tmp_path / "clip.mp4", SEGMENTS, fps=10)
    wd = str(tmp_path / ".va")
    ingest(str(video), workdir=wd, fps=1.0, profile="security")   # scans vetoed

    res = ask("what colors appear at the end of the video?", workdir=wd)
    assert any("self-escalation" in n for n in res.evidence.notes)
    assert not any(i.modality == "deep_scan_count" for i in res.evidence.items)
    assert calls["reason"] == 1        # NOT re-reasoned over identical evidence


def test_security_profile_gates_deep_scan_off(tmp_path, monkeypatch):
    """R11.a done-when: under the security profile the sweep can no longer
    fire — the video's RECORDED profile carries deep_scan: "off"."""
    steps = []
    import va.pipeline.ask as ask_mod
    real_trace = ask_mod.trace
    monkeypatch.setattr(ask_mod, "trace",
                        lambda step, action, *a, **k: (
                            steps.append((step, action)), real_trace(step, action, *a, **k))[1])

    video = write_color_video(tmp_path / "clip.mp4", SEGMENTS, fps=10)
    wd = str(tmp_path / ".va")
    ingest(str(video), workdir=wd, fps=1.0, profile="security")

    res = ask("how many times does the color change in the video?", workdir=wd)
    # the traceability ledger must not claim a vetoed sweep "ran" (round-6)
    assert ("deep_scan", "skipped") in steps
    assert ("deep_scan", "ran") not in steps
    assert res.plan.needs_deep_scan is True          # the plan still asks
    assert not any(i.modality == "deep_scan_count" for i in res.evidence.items)
    assert any("footage profile" in n and "gates deep scans off" in n
               for n in res.evidence.notes)   # THE profile cause, specifically


def test_no_scan_target_means_no_sweep_not_canned_content(tmp_path):
    """R11.a: a deep-scan plan with NO derivable target skips the sweep — the
    hardcoded 'main person's outfit' fallback is gone."""
    from va.contracts.evidence import Evidence
    from va.contracts.query_plan import QueryPlan
    from va.pipeline.deep_scan import run_deep_scan

    video = write_color_video(tmp_path / "clip.mp4", SEGMENTS, fps=10)
    wd = str(tmp_path / ".va")
    ingest(str(video), workdir=wd, fps=1.0)

    plan = QueryPlan(query="?", needs_deep_scan=True)   # no scan_target param
    ds, reason = run_deep_scan(Evidence(), plan, wd)
    assert ds is None and "no scan target" in reason


def test_all_noise_targets_do_not_share_a_sweep_cache():
    """Round-5 review MAJOR (verified live before the fix): distinct targets
    whose tokens are all cache-noise ('the color' vs 'the wearing') used to
    collapse into one 'default' bucket, so the second question read the
    first's observations and reported them as its own CODE-COUNTED fact."""
    from va.pipeline.deep_scan import canonical_key

    assert canonical_key("the color") != canonical_key("the wearing")
    # …while wording drift for ONE intent still shares its bucket
    assert canonical_key("the girl dress") == canonical_key("dress the girl")


def test_derive_scan_target_is_query_content_only():
    from va.adapters.reasoner.rule_inproc import derive_scan_target

    t = derive_scan_target("How many birds come and feed on the feeder?")
    assert "bird" in t and "outfit" not in t
    # a subject noun survives ONLY when nothing else does (round-5 review:
    # an unconditional keep-set injected counting words into ordinary phrasing)
    assert derive_scan_target(
        "how many times does the color change in the video?") == "the color"
    assert derive_scan_target("count the number of visits") == "the visits"
    # words all filtered + nothing left -> None, never canned content
    assert derive_scan_target("how many?") is None
    # pronoun-only referent: pure counting-noise is NOT a subject (round-2
    # review) — honest None, no subject-free sweep
    assert derive_scan_target("how many times did it change?") is None
