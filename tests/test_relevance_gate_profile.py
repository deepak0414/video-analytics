"""R11.b — the relevance floor is calibrated PER FOOTAGE DOMAIN, not per workdir.

An absolute score floor only means something against the footage it was measured
on. The A-EV floor (min_cosine 0.10, edited video where the subject fills the
frame) empties the evidence on static-camera footage, where every frame shares
~95% of its content and the whole cosine distribution shifts down and compresses.
Measured on the 22 real NVR clips: 6/26 ground-truth clips survived the floor, on
6 of 9 queries not one of the 22 clips cleared it, and end to end 3 of 8 questions
came back "no candidate cleared the floor — no match" while the real events sat in
the ungated pool. So the gate now resolves from each candidate's
OWN video's recorded footage profile — the same WS2.c record==reality resolution
the deep-scan veto uses.

Per-video gating is necessary but NOT sufficient for a workdir that mixes
domains: gather and fusion still rank on the same cross-domain-incomparable
cosine, so the higher-scoring domain takes every top-k slot and the other is
buried before its own floor applies. `retrieve` flags a mixed WORKDIR (not the
surfaced pool — burial is what empties the pool) rather than pretending otherwise;
domain-aware gather/fusion is backlogged.
"""
from pathlib import Path
from uuid import uuid4

import yaml

from va.contracts.evidence import EvidenceItem
from va.contracts.video import IngestStatus, SourceType, Video
from va.contracts.query_plan import QueryPlan
from va.media.synth import write_color_video
from va.pipeline import retrieval
from va.pipeline.ingest import ingest
from va.pipeline.retrieval import RelevanceGate, gates_by_video, get_relevance_gate
from va.storage.structured.catalog_sqlite import Catalog

SEGMENTS = [("red", (220, 30, 30), 2.0), ("blue", (30, 30, 220), 2.0)]

ROLES_DOC = {
    "active_profile": "testprof",
    "roles": {
        "visual_embedder": {"backend": "inproc", "model": "hash"},
        "retriever": {"min_rerank": -3.0, "min_cosine": 0.10},
    },
}


def _config_dir(tmp_path: Path) -> Path:
    """A config whose BASE floor is the A-EV one, with `security` overriding the
    visual floor — the shipped run-*/config shape in miniature."""
    cdir = tmp_path / "config"
    (cdir / "profiles" / "footage").mkdir(parents=True)
    (cdir / "roles.yaml").write_text(yaml.safe_dump(ROLES_DOC))
    (cdir / "profiles" / "testprof.yaml").write_text(yaml.safe_dump({"device": "cpu"}))
    (cdir / "profiles" / "footage" / "security.yaml").write_text(
        yaml.safe_dump({"roles": {"retriever": {"min_cosine": 0.0}}})
    )
    return cdir


def test_security_footage_is_not_judged_by_the_a_ev_floor(tmp_path, monkeypatch):
    """The R11.b regression itself, through the PUBLIC api only — no new symbols,
    so it reproduces the original failure rather than merely failing to import.

    A security-profile video whose frames sit below the base floor but above its
    own must survive. Before R11.b `retrieve` read the base config for every
    video, so these items were dropped and the answer read "no match" — which is
    what the 22 real NVR clips showed against the human ground truth.
    """
    cdir = tmp_path / "config"
    (cdir / "profiles" / "footage").mkdir(parents=True)
    (cdir / "roles.yaml").write_text(yaml.safe_dump({
        "active_profile": "testprof",
        "roles": {
            "visual_embedder": {"backend": "inproc", "model": "hash"},
            # A base floor above every achievable score (the stub scores a
            # matching frame exactly 1.0), so ONLY per-video resolution can save
            # these items; the domain floor is the shipped `security` value.
            "retriever": {"min_cosine": 1.01},
        },
    }))
    (cdir / "profiles" / "testprof.yaml").write_text(yaml.safe_dump({"device": "cpu"}))
    (cdir / "profiles" / "footage" / "security.yaml").write_text(
        yaml.safe_dump({"roles": {"retriever": {"min_cosine": 0.0}}})
    )
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    workdir = tmp_path / "wd"
    clip = write_color_video(tmp_path / "cam.mp4", SEGMENTS, fps=10)
    video = ingest(str(clip), workdir=str(workdir)).video
    catalog = Catalog(Path(workdir) / "catalog.db")
    try:
        catalog.set_profile(video.id, "security")
    finally:
        catalog.close()

    ev = retrieval.retrieve(QueryPlan(query="red", search_terms="red"),
                            workdir=str(workdir), k=10)

    assert [it for it in ev.items if it.modality == "visual"], (
        "security-profile frames were judged by the base A-EV floor — the exact "
        "failure R11.b fixes")
    assert not [n for n in ev.notes if "no candidate cleared the floor" in n]


def test_shipped_real_model_configs_carry_the_security_floor(monkeypatch):
    """Guard the SHIPPED calibration, not just the mechanism.

    No golden test covers this: `test_golden_queries` calls `visual_query()`
    directly (never `retrieve()`), and no NVR fixture has an `ask_questions:`
    block — so deleting the override from a run-*/config profile would leave both
    golden gates green while real security footage went back to being judged by
    the A-EV floor. This is the only thing standing there.
    """
    repo = Path(__file__).resolve().parents[1]
    for cdir in ("run-siglip/config", "run-claude/config", "run-qwen3vl/config"):
        monkeypatch.setenv("VA_CONFIG_DIR", str(repo / cdir))
        base = get_relevance_gate(source_type="local")          # A-EV default
        security = get_relevance_gate(profile="security", source_type="local")
        assert base.min_cosine == 0.10, f"{cdir}: A-EV floor moved"
        assert security.min_cosine == 0.0, (
            f"{cdir}: the security retriever override is missing — A-LSSRVF "
            "footage would be judged by the A-EV floor again (R11.b)")
        # the language floor is shared: the cross-encoder's scale does transfer
        assert security.min_rerank == base.min_rerank == -3.0


def test_gate_resolves_from_the_footage_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("VA_CONFIG_DIR", str(_config_dir(tmp_path)))

    base = get_relevance_gate()
    assert (base.min_cosine, base.min_rerank) == (0.10, -3.0)

    # A local video with no recorded profile falls back to the source-derived
    # default (generic) -> the base floor, unchanged.
    generic = get_relevance_gate(profile=None, source_type="local")
    assert generic.min_cosine == 0.10

    # ...and one recorded as `security` gets its domain's calibration, while the
    # language floor (whose cross-encoder scale DOES transfer) is inherited.
    security = get_relevance_gate(profile="security", source_type="local")
    assert security.min_cosine == 0.0
    assert security.min_rerank == -3.0


def test_gates_by_video_maps_each_video_to_its_own_domain(tmp_path, monkeypatch):
    """A workdir holding both domains gates each candidate on its own footage."""
    monkeypatch.setenv("VA_CONFIG_DIR", str(_config_dir(tmp_path)))
    workdir = tmp_path / "wd"
    clip_a = write_color_video(tmp_path / "a.mp4", SEGMENTS, fps=10)
    clip_b = write_color_video(tmp_path / "b.mp4", SEGMENTS[::-1], fps=10)
    edited = ingest(str(clip_a), workdir=str(workdir)).video
    static = ingest(str(clip_b), workdir=str(workdir)).video
    catalog = Catalog(Path(workdir) / "catalog.db")
    try:
        catalog.set_profile(static.id, "security")
    finally:
        catalog.close()

    items = [
        EvidenceItem(modality="visual", video_id=edited.id, score=0.05),
        EvidenceItem(modality="visual", video_id=static.id, score=0.05),
        EvidenceItem(modality="caption", content="no video", score=0.5),
    ]
    gates = gates_by_video(items, str(workdir)).gates

    assert gates[str(edited.id)].min_cosine == 0.10   # A-EV calibration
    assert gates[str(static.id)].min_cosine == 0.0    # A-LSSRVF calibration
    assert gates[None].min_cosine == 0.10             # no video -> base config
    # The same 0.05 frame is junk on edited video and a real candidate on a
    # fixed camera. One gate per workdir cannot express that.
    assert not gates[str(edited.id)].keeps(items[0])
    assert gates[str(static.id)].keeps(items[1])


def test_a_mixed_domain_pool_is_flagged_in_the_evidence(tmp_path, monkeypatch):
    """Per-video floors do not rescue a mixed workdir: gather/fusion still rank
    A-EV and A-LSSRVF frames against each other on a scale that does not compare,
    so the weaker domain is buried before its gate. Say so instead of answering
    confidently off half the evidence."""
    monkeypatch.setenv("VA_CONFIG_DIR", str(_config_dir(tmp_path)))
    workdir = tmp_path / "wd"
    edited = ingest(str(write_color_video(tmp_path / "a.mp4", SEGMENTS, fps=10)),
                    workdir=str(workdir)).video
    static = ingest(str(write_color_video(tmp_path / "b.mp4", SEGMENTS[::-1], fps=10)),
                    workdir=str(workdir)).video
    catalog = Catalog(Path(workdir) / "catalog.db")
    try:
        catalog.set_profile(static.id, "security")
    finally:
        catalog.close()

    ev = retrieval.retrieve(QueryPlan(query="red", search_terms="red"),
                            workdir=str(workdir), k=10)
    mixed = [n for n in ev.notes if "spans 2 footage profiles" in n]
    assert mixed, f"mixed-domain workdir not flagged; notes={ev.notes}"
    assert "security" in mixed[0] and "generic" in mixed[0]

    # THE CASE THAT MATTERS: the security video is BURIED — none of its frames
    # reach the pool, which is what cross-domain ranking actually does on real
    # footage. A warning keyed on the surfaced pool goes silent here, exactly
    # when the answer is being built on the wrong footage.
    only_edited = [EvidenceItem(modality="visual", video_id=edited.id, score=0.9)]
    assert gates_by_video(only_edited, str(workdir)).domains == frozenset(
        {"generic", "security"}), (
        "domains must come from the WORKDIR, not the surfaced pool — a buried "
        "domain would otherwise silence the warning")


def test_a_failed_ingest_does_not_create_a_footage_domain(tmp_path, monkeypatch):
    """A catalog row exists BEFORE fetch succeeds, so a failed nvr:// ingest must
    not register a domain that indexed no frames — otherwise every answer in that
    workdir carries a mixed-domain warning about footage nothing can retrieve."""
    monkeypatch.setenv("VA_CONFIG_DIR", str(_config_dir(tmp_path)))
    workdir = tmp_path / "wd"
    good = ingest(str(write_color_video(tmp_path / "g.mp4", SEGMENTS, fps=10)),
                  workdir=str(workdir)).video
    catalog = Catalog(Path(workdir) / "catalog.db")
    try:
        failed = Video(source_type=good.source_type, source_uri="nvr://1/x/y",
                       source_key="never-fetched", ingest_status=IngestStatus.failed,
                       profile="security")
        catalog.upsert(failed)
    finally:
        catalog.close()

    items = [EvidenceItem(modality="visual", video_id=good.id, score=0.5)]

    assert gates_by_video(items, str(workdir)).domains == frozenset({"generic"})

    # ...and a single-domain pool must NOT carry the warning.
    solo = tmp_path / "solo"
    ingest(str(write_color_video(tmp_path / "c.mp4", SEGMENTS, fps=10)),
           workdir=str(solo))
    ev2 = retrieval.retrieve(QueryPlan(query="red", search_terms="red"),
                             workdir=str(solo), k=10)
    assert not [n for n in ev2.notes if "footage profiles" in n]


def test_source_types_sharing_a_profile_are_one_domain(tmp_path, monkeypatch):
    """A local and a youtube video both resolve to `generic`, so a workdir with
    both is NOT a mixed pool. Keying the domain on (profile, source_type) instead
    of the resolved profile flagged every A-EV workdir — .va-shots holds one
    local and five youtube videos — with the nonsense text "generic, generic"."""
    monkeypatch.setenv("VA_CONFIG_DIR", str(_config_dir(tmp_path)))
    workdir = tmp_path / "wd"
    local = ingest(str(write_color_video(tmp_path / "l.mp4", SEGMENTS, fps=10)),
                   workdir=str(workdir)).video
    # The scenario needs BOTH source types actually present — one local ingest
    # alone leaves nothing for the profile-vs-source-type distinction to bite on.
    catalog = Catalog(Path(workdir) / "catalog.db")
    try:
        catalog.upsert(Video(source_type=SourceType.youtube,
                             source_uri="https://youtu.be/aaaaaaaaaaa",
                             source_key="aaaaaaaaaaa",
                             ingest_status=IngestStatus.done))
        raw = catalog.footage_domains()
    finally:
        catalog.close()
    assert len({s for _, s in raw}) == 2, "the two source types must both be present"

    # The behavior, not just the field: deciding the mix on the raw (profile,
    # source_type) pairs would flag this workdir "generic, generic".
    ev = retrieval.retrieve(QueryPlan(query="red", search_terms="red"),
                            workdir=str(workdir), k=10)
    assert not [n for n in ev.notes if "footage profiles" in n], (
        f"one profile across two source types is not a mixed workdir; {ev.notes}")

    items = [EvidenceItem(modality="visual", video_id=local.id, score=0.5)]

    gmap = gates_by_video(items, str(workdir))

    assert gmap.domains == frozenset({"generic"})


def test_gates_by_video_degrades_to_the_base_gate(tmp_path, monkeypatch):
    """A catalog that can't be READ must not break retrieval — SR.5 is a filter,
    not a dependency.

    NB a missing workdir is not enough to exercise this: `connect()` mkdirs the
    parent and creates an empty DB, so the lookup succeeds with no rows. Corrupt
    the file so sqlite actually raises, or the except branch has no coverage.
    """
    monkeypatch.setenv("VA_CONFIG_DIR", str(_config_dir(tmp_path)))
    workdir = tmp_path / "wd"
    workdir.mkdir()
    (workdir / "catalog.db").write_bytes(b"not a sqlite database at all")
    items = [EvidenceItem(modality="visual", video_id=uuid4(), score=0.5)]

    gates = gates_by_video(items, str(workdir)).gates

    assert set(gates) == {None}
    assert gates[None].min_cosine == 0.10


def test_an_unresolvable_profile_falls_back_to_the_base_floors(tmp_path, monkeypatch):
    """A video recorded under a profile the ACTIVE config dir doesn't define must
    keep the base floors — not become ungated."""
    monkeypatch.setenv("VA_CONFIG_DIR", str(_config_dir(tmp_path)))

    gate = get_relevance_gate(profile="warehouse", source_type="local")

    assert (gate.min_cosine, gate.min_rerank) == (0.10, -3.0)


def test_retrieve_applies_each_items_own_gate(tmp_path, monkeypatch):
    """The wiring: two videos, two calibrations, one query — the strict floor
    drops its video's frames while the permissive one keeps its own."""
    workdir = tmp_path / "wd"
    clip = write_color_video(tmp_path / "c.mp4", SEGMENTS, fps=10)
    strict = ingest(str(clip), workdir=str(workdir)).video
    clip2 = write_color_video(tmp_path / "d.mp4", SEGMENTS[::-1], fps=10)
    lenient = ingest(str(clip2), workdir=str(workdir)).video

    monkeypatch.setattr(retrieval, "gates_by_video", lambda items, wd: retrieval.GateMap({
        None: RelevanceGate(),
        str(strict.id): RelevanceGate(min_cosine=1.01),   # nothing can clear it
        str(lenient.id): RelevanceGate(min_cosine=-1.0),  # everything clears it
    }))
    ev = retrieval.retrieve(QueryPlan(query="red", search_terms="red"),
                            workdir=str(workdir), k=10)

    kept = {str(it.video_id) for it in ev.items if it.modality == "visual"}
    assert str(lenient.id) in kept
    assert str(strict.id) not in kept
    # both calibrations are reported, so the trace/answer can say which applied
    assert isinstance(ev.attributes["fusion"]["gate"], list)


def test_only_the_floors_that_covered_a_candidate_are_reported(tmp_path, monkeypatch):
    """Transparency: the note names the floor an item was actually judged
    against. Listing the base gate when every candidate came from one profile
    would report a threshold nothing was measured against."""
    workdir = tmp_path / "wd"
    clip = write_color_video(tmp_path / "f.mp4", SEGMENTS, fps=10)
    only = ingest(str(clip), workdir=str(workdir)).video

    monkeypatch.setattr(retrieval, "gates_by_video", lambda items, wd: retrieval.GateMap({
        None: RelevanceGate(min_cosine=0.99),          # base — covers nothing here
        str(only.id): RelevanceGate(min_cosine=-1.0),  # the one that applies
    }))
    ev = retrieval.retrieve(QueryPlan(query="red", search_terms="red"),
                            workdir=str(workdir), k=10)

    assert ev.attributes["fusion"]["gate"] == {"min_rerank": float("-inf"),
                                               "min_cosine": -1.0}
    assert not [n for n in ev.notes if "0.99" in n]


def test_explicit_gate_still_applies_to_everything(tmp_path, monkeypatch):
    """The override contract: a caller-supplied gate is not per-video."""
    workdir = tmp_path / "wd"
    clip = write_color_video(tmp_path / "e.mp4", SEGMENTS, fps=10)
    ingest(str(clip), workdir=str(workdir))

    def boom(items, wd):  # must not be consulted when a gate was passed
        raise AssertionError("per-video resolution ran despite an explicit gate")

    monkeypatch.setattr(retrieval, "gates_by_video", boom)
    ev = retrieval.retrieve(QueryPlan(query="red", search_terms="red"),
                            workdir=str(workdir), k=10,
                            gate=RelevanceGate(min_cosine=1.01))

    assert not [it for it in ev.items if it.modality == "visual"]
    assert ev.attributes["fusion"]["gate"] == {"min_rerank": float("-inf"),
                                               "min_cosine": 1.01}
