"""Retrieval Layer (SR.4) — the retriever orchestrator.

This is the vendor-neutral equivalent of NVIDIA VSS's CA-RAG retrieval stage: it
turns a QueryPlan into ONE ranked `Evidence` bundle by fusing every modality,
rather than concatenating per-tier searches in plan order (what the older
`assemble()` did). Four stages:

  GATHER    visual frames (Role 2) + semantic text over the four language
            modalities (caption/transcript/OCR/action, via the SR.2 index) +
            structured object facts (Roles 5/6) -> a pool of EvidenceItems.
  RERANK    the cross-encoder (SR.3) reads (query, item.content) for every
            LANGUAGE-bearing item and scores true relevance on ONE common scale
            — this is what makes a transcript line and a caption comparable.
  FUSE      combine the reranker's relevance with each item's native retrieval
            score into a single ordering (see `_fuse` for the formula + the
            reason it is shaped this way).
  RANK+GATE sort, then drop sub-threshold candidates so "no match" is a real
            outcome (SR.5, `RelevanceGate`) — gating the RAW signals `_fuse`
            preserved, not the min-max ordering score. The gate is permissive by
            default; calibrated floors live in run-*/config.

Why fuse instead of trust one signal? Measured on real data (SR.3 demo): the
cross-encoder is decisive when the language is rich ("harmony among nations" ->
"So, world peace." stood alone as the only positive) but can misfire on terse
utterances (it ranked "Very pretty." above "Twenty-seven dresses." for "elegant
formal gowns", where the bi-encoder had "dresses" #1). So we FUSE the
cross-encoder and the bi-encoder rather than replace one with the other.

HEURISTICS ARE FLAGGED, NOT HIDDEN. Cross-modal score fusion needs weights, and
weights without labeled data are judgement calls. The two knobs below
(`RERANK_WEIGHT`, the rerankable-modality set) are deliberately conservative and
documented; they are tuning targets for the golden-query harness, not settled
constants. The honest limitation: we cannot perfectly calibrate a SigLIP cosine
(~0.1-0.18 for a relevant frame) against a cross-encoder logit without labels, so
visual frames are ranked by their own normalized cosine and language items by the
fused reranker signal — see `_fuse`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

from va.contracts.evidence import (
    Evidence,
    EvidenceItem,
    from_co_occurrence,
    from_distinct_count,
    from_object_summary,
    from_search_hit,
    from_text_hit,
)
from va.contracts.query_plan import QueryPlan
from va.registry import get_reranker
from va.runtime.trace import trace

# Language-bearing modalities whose `content` is real description a cross-encoder
# can judge. Visual frame hits ("visual match at 12.3s") carry NO language, so
# reranking them is meaningless — they rank on their cosine alone (see _fuse).
RERANKABLE = {
    "caption", "transcript", "on_screen_text", "action",
    "object", "object_count", "co_occurrence",
}

# Plan tier-flag -> the text-index modality it requests (SR.2 modalities).
_TEXT_TIERS = {
    "needs_caption_search": "caption",
    "needs_transcript_search": "transcript",
    "needs_ocr_search": "on_screen_text",
    "needs_action_query": "action",
}

# --- fusion heuristics (TUNABLE — flagged per the repo's no-silent-magic rule) --
# Weight on the cross-encoder relevance vs the native retrieval (bi-encoder /
# detector-confidence) score, for language-bearing items. 0.6 leans on the
# sharper cross-encoder while keeping the bi-encoder as a corrective (the "gowns"
# lesson). Visual frames ignore this — they have no rerank term. Calibrate
# against tests/golden_queries/ when the harness lands.
RERANK_WEIGHT = 0.6

# Cap on how many ranked items the bundle carries downstream (keyframe pick +
# reasoner prompt). Not a relevance filter — that is the gate below. Just a
# prompt-size guard; render_evidence caps again at 60.
MAX_ITEMS = 40


@dataclass(frozen=True)
class RelevanceGate:
    """SR.5 — absolute relevance floors that make 'no match' a real outcome
    (closes the documented top-k-always-returns-hits gap).

    Two floors, because the two signals live on incompatible scales and neither
    alone is sufficient (the recurring fusion lesson):
      - min_rerank gates LANGUAGE items by the cross-encoder logit. Measured: a
        relevant-but-terse caption scored -1.51 while clearly-irrelevant lines
        scored -4.9 to -11 and an off-topic action -3.7 — so the floor sits in
        that gap, NOT at the sign boundary (0 would wrongly drop the -1.51).
      - min_cosine gates VISUAL frames by raw native cosine (they carry no
        language to rerank). Measured SigLIP: relevant ~0.11-0.18, irrelevant
        ~0 or negative.

    Defaults keep EVERYTHING (-inf): thresholding only bites with calibrated real
    backends, whose floors live in run-*/config next to the model they were
    measured against. The numeric floors are FLAGGED magic values — calibration
    targets for the golden-query harness, not settled constants.

    R11.b: floors are calibrated PER FOOTAGE DOMAIN, because the same cosine
    means different things on different footage — see `gates_by_video`.
    """

    min_rerank: float = -math.inf
    min_cosine: float = -math.inf

    @property
    def active(self) -> bool:
        return self.min_rerank > -math.inf or self.min_cosine > -math.inf

    def keeps(self, item: EvidenceItem) -> bool:
        if item.modality == "visual":
            return item.score >= self.min_cosine
        rr = item.attributes.get("rerank_score")
        if rr is None:
            # A language item the reranker couldn't score (degraded path): we
            # have no relevance read, so we keep it rather than guess-drop.
            return True
        return rr >= self.min_rerank


def get_relevance_gate(
    workdir: Optional[str] = None,
    *,
    profile: Optional[str] = None,
    source_type: Optional[str] = None,
) -> RelevanceGate:
    """Build the gate from a config's optional `retriever:` block. Absent/empty
    -> permissive (no behavior change for the stub pipeline).

    With `source_type`, the block is read from the config that video's own
    FOOTAGE PROFILE selects (WS2.c record==reality) instead of the base config,
    so a domain can carry its own calibrated floors. Without it, the base config.
    """
    from va.configuration import config_for, load_config

    try:
        cfg = (config_for(profile, source_type) if source_type is not None
               else load_config())
        spec = cfg.roles.get("retriever") or {}
    except Exception as e:  # noqa: BLE001 — never let config issues break retrieval
        if source_type is not None:
            # A profile that won't resolve (recorded in one config dir, absent
            # from the active one) must fall back to the BASE floors, never to a
            # permissive gate: silently ungating one video while its neighbours
            # stay gated is worse than either floor.
            trace("retriever", "gate:profile",
                  f"footage profile {profile!r} unresolvable ({e}) — base floors",
                  level="warn")
            return get_relevance_gate()
        spec = {}
    return RelevanceGate(
        min_rerank=float(spec.get("min_rerank", -math.inf)),
        min_cosine=float(spec.get("min_cosine", -math.inf)),
    )


@dataclass(frozen=True)
class GateMap:
    """Per-video gates, plus the footage domains the WORKDIR spans (not the pool)."""

    gates: dict[Optional[str], RelevanceGate]
    # Resolved FOOTAGE PROFILE names present in the WORKDIR (searchable videos) —
    # deliberately NOT the candidate pool: cross-domain burial is what keeps the
    # weaker domain out of the pool, so a pool-derived set goes silent in exactly
    # the total-burial case. Do not "simplify" this to frozenset(by_domain).
    # Names, not (profile, source_type) pairs: a local and a youtube video both
    # resolve to `generic`, so they are ONE domain.
    domains: frozenset[str] = frozenset()


def gates_by_video(items: Sequence[EvidenceItem], workdir: str) -> GateMap:
    """Map each candidate's video to the gate its OWN footage profile calibrates,
    plus a `None` entry for the base gate (items with no video, and the fallback).

    Why per video and not one gate per query (R11.b): an absolute score floor is
    only meaningful against the footage it was measured on. Measured on the 22
    real NVR clips: the A-EV floor (min_cosine 0.10, calibrated on edited video
    where the subject fills the frame) retains 6 of 26 ground-truth clips, and on
    6 of 9 queries not ONE of the 22 clips clears it — because a fixed camera's
    frames share ~95% of their content, so every cosine is dominated by an
    invariant background term and the whole distribution shifts down and
    compresses (per-query spread across all 22 clips: 0.020-0.077). End to end
    that emptied 3 of 8 questions to "no match".

    LIMIT — per-video gating is NECESSARY BUT NOT SUFFICIENT for a workdir that
    mixes domains. `_gather` still takes ONE global top-k and `_fuse` still
    min-maxes cosines across the whole visual lane, both on the very scale this
    proves is domain-incomparable: A-EV frames (relevant 0.11-0.18) outrank every
    A-LSSRVF frame (0.020-0.077), so in a mixed workdir the static-camera clips
    are buried BEFORE the gate and never reach their own floor. Domain-aware
    gather/fusion is backlogged; until then keep A-LSSRVF chunks in their own
    workdir. `retrieve` flags a mixed WORKDIR in the evidence notes — deliberately
    the workdir and not the surfaced pool, since total burial would otherwise
    silence the warning in the one case that needs it most.

    Any lookup failure degrades to the base gate rather than breaking retrieval.
    """
    gates: dict[Optional[str], RelevanceGate] = {None: get_relevance_gate()}
    vids = {str(it.video_id) for it in items if it.video_id is not None}
    if not vids:
        return GateMap(gates)
    try:
        from uuid import UUID

        from va.pipeline.paths import Workspace
        from va.storage.structured.catalog_sqlite import Catalog

        catalog = Catalog(Workspace(workdir).catalog_db)
        try:
            videos = catalog.get_many([UUID(v) for v in vids])
            workdir_domains = catalog.footage_domains()
        finally:
            catalog.close()
    except Exception as e:  # noqa: BLE001 — retrieval must survive a bad catalog
        trace("retriever", "gate:profile", f"per-video gates unavailable: {e}",
              level="warn")
        return GateMap(gates)

    from va.configuration import default_footage_profile

    by_domain: dict[str, RelevanceGate] = {}
    for vid, v in videos.items():
        profile = getattr(v, "profile", None)
        stype = getattr(getattr(v, "source_type", None), "value", None)
        if stype is None:
            continue  # unresolvable domain -> base gate
        # The domain is the profile a video's roles RAN under, which is what
        # calibrates its scores — NULL rows derive it from the source, exactly as
        # config_for does.
        domain = profile or default_footage_profile(stype)
        if domain not in by_domain:
            by_domain[domain] = get_relevance_gate(profile=profile,
                                                   source_type=stype)
        gates[str(vid)] = by_domain[domain]
    # domains comes from the WHOLE workdir, not `by_domain` (the pool), so total
    # burial of one domain still reports the mix.
    return GateMap(gates, frozenset(
        p or default_footage_profile(s) for p, s in workdir_domains if s))


def _minmax(values: Sequence[float]) -> List[float]:
    """Scale to [0,1] for ordering. Degenerate cases collapse to 0.5 rather than
    a fabricated 1.0 — a single candidate, or an all-equal set, carries no
    relative information and should not look maximally confident. (Absolute
    thresholding for 'no match' is SR.5, and reads the RAW scores, not these.)"""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def _snip(text: str, limit: int = 50) -> str:
    """Short single-line preview that backs off to a word boundary — never cuts a
    word or URL mid-token (fixes the `https://www.youtube.com/w` truncation)."""
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip() + "…"


def _preview(items, limit: int = 8) -> list:
    """Per-item provenance for a gather trace event: which role/modality each
    candidate came from, so the trace shows evidence sources at collection time."""
    return [{"role": it.source_role, "modality": it.modality,
             "t": round(it.time_start, 1), "score": round(it.score, 3),
             "content": _snip(it.content)} for it in items[:limit]]


def _gather(plan: QueryPlan, workdir: str, k: int) -> Evidence:
    """Stage 1: pull candidates from every modality the plan asks for.

    Tier 1 (visual) always runs — the architecture's instant first pass. Text
    modalities go through the SR.2 semantic index; if that index is empty (e.g. a
    pre-SR.2 workdir not yet backfilled) we fall back to the per-modality lexical
    store searches so existing data still retrieves. Structured object facts ride
    along unchanged from the assembler.
    """
    ev = Evidence(query=plan.query)
    terms = plan.search_terms or plan.query

    # Tier 1 — visual frames.
    from va.pipeline.query import query as visual_query

    vhits = visual_query(terms, workdir=workdir, k=k)
    if not vhits:  # distinguish a STALE visual index from a true no-match
        from va.pipeline.paths import Workspace
        from va.registry import embedder_id, get_visual_embedder
        from va.storage.vector.sharded import ShardedVectorStore

        vstore = ShardedVectorStore(Workspace(workdir).videos_root)
        vtotal = vstore.count()
        if vtotal and not vstore.count(
                expect_embedder=embedder_id("visual_embedder"),
                expect_dim=len(get_visual_embedder().embed_text(["_"])[0])):
            ev.notes.append(f"visual index unusable ({vtotal} vector(s) on a different "
                            "embedder) — no visual matches; run `va reingest` to re-embed")
    raw_visual = len(vhits)
    # SR.6: when the planner flags a query an embedding mis-handles (attribute /
    # negation / composition), VLM-verify the candidates before they become
    # evidence. No-op under the passthrough stub; the claim is the FULL query
    # (search_terms may have dropped the discriminating word).
    verified = getattr(plan, "needs_visual_verification", False)
    if verified:
        from va.pipeline.verify import verify_visual_hits

        # KNOWN GAP (R11.b backlog): this floor stays BASE-config, because the
        # candidates' videos aren't resolved until the gate stage. On A-LSSRVF
        # footage every hit then sits below the A-EV 0.10 and passes through
        # unchecked, i.e. verification silently no-ops — the opposite of what
        # that footage needs, since VLM verification is the precision mechanism
        # thresholding a cosine cannot provide there. Making it profile-aware
        # turns verification ON for security footage (a real VLM-cost change),
        # so it is its own item, not a rider on the gate fix.
        gate = get_relevance_gate()
        floor = gate.min_cosine if gate.min_cosine > -math.inf else 0.10
        vhits = verify_visual_hits(vhits, plan.query, workdir=workdir,
                                   floor=floor, stop_after_accepts=1)
    n0 = len(ev.items)
    for h in vhits:
        ev.items.append(from_search_hit(h))
    trace("retriever", "gather:visual",
          f"{len(vhits)} visual hits"
          + (f" (VLM-verified, {raw_visual - len(vhits)} dropped)" if verified else ""),
          count=len(vhits), raw=raw_visual, verified=verified,
          items=_preview(ev.items[n0:]))

    # Text tiers — semantic index (SR.2), with a lexical fallback per modality.
    wanted = [mod for flag, mod in _TEXT_TIERS.items() if getattr(plan, flag, False)]
    if wanted:
        from va.pipeline.text_search import search_text
        from va.pipeline.paths import Workspace
        from va.registry import embedder_id, get_text_embedder
        from va.storage.vector.sharded import ShardedVectorStore

        text_index = ShardedVectorStore(Workspace(workdir).videos_root,
                                        shard_name="text_vectors.npz")
        total = text_index.count()
        # Only shards matching the CURRENT text embedder AND dim are usable: an index
        # left on a stale/mismatched embedder is non-empty but unsearchable, so
        # count() > 0 alone would wrongly pick the semantic branch and silently drop the
        # text tier. Score usability with the SAME (embedder, dim) the search-time guard
        # uses, so both agree even on untagged legacy shards.
        usable = 0
        if total:
            cur_dim = len(get_text_embedder().embed(["_"])[0])
            usable = text_index.count(expect_embedder=embedder_id("text_embedder"),
                                      expect_dim=cur_dim)
        nt = len(ev.items)
        if usable > 0:
            hits = search_text(terms, workdir=workdir, k=k, modalities=wanted)
            for h in hits:
                ev.items.append(from_text_hit(h))
            trace("retriever", "gather:text", f"{len(hits)} semantic-text hits",
                  modalities=wanted, count=len(hits), items=_preview(ev.items[nt:]))
        else:
            reason = (f"unusable ({total} vector(s) on a different embedder)"
                      if total else "empty")
            ev.notes.append(f"semantic text index {reason}; lexical fallback "
                            "(run `va reingest`/backfill to (re)build it)")
            _gather_lexical(ev, wanted, terms, workdir, k)
            trace("retriever", "gather:text",
                  f"{len(ev.items) - nt} lexical-fallback hits (semantic index {reason})",
                  level="warn", modalities=wanted, items=_preview(ev.items[nt:]))

    # Structured object facts (Roles 5/6) — descriptive language, so rerankable.
    if plan.needs_object_query:
        from va.pipeline.objects import count_objects, query_objects
        from va.pipeline.paths import Workspace
        from va.storage.structured.detections import DetectionStore

        ns = len(ev.items)
        for s in query_objects(terms, workdir=workdir):
            ev.items.append(from_object_summary(s))
        for c in count_objects(terms, workdir=workdir):
            ev.items.append(from_distinct_count(c))
        store = DetectionStore(Workspace(workdir).catalog_db)
        try:
            known = set(store.existing_classes())
            asked = [w for w in terms.lower().split() if w in known]
            if len(set(asked)) >= 2:
                for co in store.co_occurrence(asked)[:3]:
                    ev.items.append(from_co_occurrence(co))
        finally:
            store.close()
        trace("retriever", "gather:structured",
              f"{len(ev.items) - ns} object/count/co-occurrence items (Roles 5/6)",
              items=_preview(ev.items[ns:]))

    # Unknown future tier flags: note them, don't fail (same contract as assemble).
    known = set(QueryPlan.model_fields)
    for name, value in (plan.model_extra or {}).items():
        if name.startswith("needs_") and value and name not in known:
            ev.notes.append(f"unknown tier flag {name!r} requested; skipped")
    return ev


def _gather_lexical(ev: Evidence, wanted: Sequence[str], terms: str,
                    workdir: str, k: int) -> None:
    """Fallback gather when the semantic index isn't built: the original
    per-modality word-overlap store searches, one per requested text tier."""
    from va.contracts.evidence import (
        from_action_hit, from_caption_hit, from_ocr_hit, from_transcript_hit,
    )

    if "caption" in wanted:
        from va.pipeline.caption import search_captions
        for h in search_captions(terms, workdir=workdir, k=k):
            ev.items.append(from_caption_hit(h))
    if "transcript" in wanted:
        from va.pipeline.transcript import search_transcripts
        for h in search_transcripts(terms, workdir=workdir, k=k):
            ev.items.append(from_transcript_hit(h))
    if "on_screen_text" in wanted:
        from va.pipeline.ocr import search_ocr
        for h in search_ocr(terms, workdir=workdir, k=k):
            ev.items.append(from_ocr_hit(h))
    if "action" in wanted:
        from va.pipeline.actions import search_actions
        for h in search_actions(terms, workdir=workdir, k=k):
            ev.items.append(from_action_hit(h))


def _fuse(query: str, items: List[EvidenceItem], reranker) -> None:
    """Stages 2-3: rerank the language-bearing items, then fuse into one order.

    Writes two things onto each item's `attributes`, then sorts `items` in place:
      - rerank_score : raw cross-encoder output (None for visual frames). SR.5
                       thresholds on THIS (cross-encoder sign = relevant), not on
                       the fused order.
      - fused_score  : the ordering key, in [0,1].

    Formula, per item:
        fused = RERANK_WEIGHT * norm_rerank + (1 - RERANK_WEIGHT) * norm_native

    norm_rerank is the min-max of the reranker scores ACROSS the language items
    (one common scale — that's the reranker's whole contribution to cross-modal
    fusion); visual frames have no language, so their norm_rerank term is 0 and
    they rank purely on norm_native. norm_native is the min-max of native
    retrieval scores WITHIN a lane (visual cosines compared to visual cosines,
    text/structured scores to each other) because a SigLIP cosine and a bge
    cosine live on different scales and must not be compared raw.
    """
    if not items:
        return

    rerankable = [it for it in items if it.modality in RERANKABLE and it.content]
    rr_raw: dict[int, float] = {}
    if rerankable:
        try:
            scores = reranker.rerank(query, [it.content for it in rerankable])
            for it, s in zip(rerankable, scores):
                rr_raw[id(it)] = float(s)
        except Exception as e:  # noqa: BLE001 — degrade to native-only ordering
            rr_raw = {}
            for it in items:
                it.attributes["rerank_note"] = f"rerank skipped: {e}"

    # norm_rerank: one min-max across all language items that got a score.
    rr_items = [it for it in rerankable if id(it) in rr_raw]
    rr_norm_list = _minmax([rr_raw[id(it)] for it in rr_items])
    rr_norm: dict[int, float] = {id(it): n for it, n in zip(rr_items, rr_norm_list)}

    # norm_native: min-max within each lane (visual vs. everything else), so
    # different cosine scales don't bleed into each other.
    for lane in (lambda it: it.modality == "visual", lambda it: it.modality != "visual"):
        lane_items = [it for it in items if lane(it)]
        for it, n in zip(lane_items, _minmax([it.score for it in lane_items])):
            it.attributes["native_norm"] = n

    for it in items:
        nr = rr_norm.get(id(it), 0.0)
        nn = it.attributes.get("native_norm", 0.5)
        it.attributes["rerank_score"] = rr_raw.get(id(it))  # None for visual
        it.attributes["fused_score"] = RERANK_WEIGHT * nr + (1 - RERANK_WEIGHT) * nn

    # Stage 4 — rank. Tiebreak deterministically (raw native, then earlier time).
    items.sort(key=lambda it: (-it.attributes["fused_score"], -it.score, it.time_start))


def retrieve(
    plan: QueryPlan, workdir: str = ".va", k: int = 5,
    gate: Optional[RelevanceGate] = None,
) -> Evidence:
    """SR.4/SR.5 entry point: QueryPlan -> fused, ranked, thresholded Evidence.

    Drop-in replacement for `assemble()` in the ask path: same signature, same
    return type, but cross-modally ranked (SR.4) and relevance-gated (SR.5)
    instead of tier-ordered and unfiltered. `gate=None` reads the config gate
    (permissive for the stub pipeline); pass one explicitly to override.
    """
    ev = _gather(plan, workdir=workdir, k=k)
    from collections import Counter as _Counter

    trace("retriever", "gathered", f"{len(ev.items)} candidates",
          by_modality=dict(_Counter(it.modality for it in ev.items)))

    _fuse(plan.query, ev.items, get_reranker())
    trace("retriever", "fuse", f"ranked {len(ev.items)} (rerank_weight={RERANK_WEIGHT})",
          top=[{"modality": it.modality,
                "fused": round(it.attributes.get("fused_score", 0.0), 3),
                "rerank": (round(it.attributes["rerank_score"], 2)
                           if it.attributes.get("rerank_score") is not None else None),
                "content": _snip(it.content)}
               for it in ev.items[:3]])

    # Preserve the pre-gate dominant video so a deep-scan escalation can still
    # target the right video when the gate empties the evidence. "No (relevant)
    # match" is the SIGNAL to deep-scan, not a reason to skip it — and picking
    # WHICH video to sweep is a relative-ranking question, distinct from the
    # gate's absolute-relevance one.
    from collections import Counter

    pre = Counter(it.video_id for it in ev.items if it.video_id is not None)
    if pre:
        ev.attributes["primary_video_id"] = str(pre.most_common(1)[0][0])

    # SR.5 — relevance gate. Applied after fusion (so the raw signals exist) and
    # before the size cap. Transparent: records what it dropped; never silently
    # empties everything. R11.b: an explicit `gate` still applies to everything
    # (the override contract), otherwise each item is gated by the floors its own
    # video's footage profile calibrates — see `gates_by_video`.
    gmap = (GateMap({None: gate}) if gate is not None
            else gates_by_video(ev.items, workdir))
    gates, base = gmap.gates, gmap.gates[None]

    # A WORKDIR spanning footage domains is ranked on a scale that does not
    # compare across them (see `gates_by_video`'s LIMIT): the higher-scoring
    # domain takes every top-k slot, so the other is buried before its own floor
    # applies. Keyed on the workdir, not the surfaced pool — total burial would
    # otherwise silence the warning in the very case it exists for.
    if len(gmap.domains) > 1:
        named = ", ".join(sorted(gmap.domains))
        ev.notes.append(
            f"workdir spans {len(gmap.domains)} footage profiles ({named}); "
            "visual scores are not comparable across them, so lower-scoring "
            "footage may be under-represented or absent — keep domains in "
            "separate workdirs")
        trace("retriever", "gate:mixed-domain",
              f"workdir spans {len(gmap.domains)} footage profiles ({named})",
              level="warn", domains=sorted(gmap.domains))

    def gate_for(it: EvidenceItem) -> RelevanceGate:
        key = str(it.video_id) if it.video_id is not None else None
        return gates.get(key, base)

    # Only the gates that actually cover a candidate count — reporting the base
    # gate's floors when every item came from one profile would name a threshold
    # nothing was measured against.
    applied_gates = [gate_for(it) for it in ev.items]
    if any(g.active for g in applied_gates):
        kept = [it for it in ev.items if gate_for(it).keeps(it)]
        dropped = len(ev.items) - len(kept)
        floors = sorted({(g.min_rerank, g.min_cosine) for g in applied_gates})
        shown = "; ".join(f"min_rerank={r}, min_cosine={c}" for r, c in floors)
        if dropped:
            ev.notes.append(
                f"relevance gate dropped {dropped}/{len(ev.items)} below floor "
                f"({shown})"
                + ("; no candidate cleared the floor — no match" if not kept else "")
            )
        trace("retriever", "gate", f"kept {len(kept)}, dropped {dropped}",
              level=("warn" if not kept else "info"), kept=len(kept), dropped=dropped,
              floors=[{"min_rerank": r, "min_cosine": c} for r, c in floors])
        ev.items = kept

    if len(ev.items) > MAX_ITEMS:
        ev.notes.append(f"retriever: kept top {MAX_ITEMS} of {len(ev.items)} ranked items")
        ev.items = ev.items[:MAX_ITEMS]
    # One dict when a single set of floors applied (the common case), a list when
    # the pool spanned footage domains with different calibrations. An empty pool
    # was gated by nothing, so report the base gate rather than an empty list.
    applied = [{"min_rerank": r, "min_cosine": c}
               for r, c in sorted({(g.min_rerank, g.min_cosine)
                                   for g in (applied_gates or [base])})]
    ev.attributes["fusion"] = {
        "method": "rerank+native min-max blend",
        "rerank_weight": RERANK_WEIGHT,
        "gate": applied[0] if len(applied) == 1 else applied,
    }

    # Typed-query tier: deterministic windowed aggregation (code counts, the
    # LLM narrates). Dispatched AFTER fusion/gate/cap on purpose — a
    # code-counted fact does not compete with retrieval candidates on cosine
    # relevance and must never be relevance-dropped or crowded out.
    if plan.needs_aggregation:
        from va.pipeline.aggregate import dispatch_aggregation

        agg_items, agg_notes = dispatch_aggregation(plan.params or {}, workdir)
        ev.items.extend(agg_items)
        ev.notes.extend(agg_notes)
        trace("retriever", "aggregation",
              (agg_items[0].content if agg_items
               else next(iter(agg_notes), "aggregation degraded")),
              level=("info" if agg_items else "warn"),
              items=len(agg_items))
    return ev
