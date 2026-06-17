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


def get_relevance_gate(workdir: Optional[str] = None) -> RelevanceGate:
    """Build the gate from the active config's optional `retriever:` block.
    Absent/empty -> permissive (no behavior change for the stub pipeline)."""
    from va.configuration import load_config

    try:
        spec = load_config().roles.get("retriever") or {}
    except Exception:  # noqa: BLE001 — never let config issues break retrieval
        spec = {}
    return RelevanceGate(
        min_rerank=float(spec.get("min_rerank", -math.inf)),
        min_cosine=float(spec.get("min_cosine", -math.inf)),
    )


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
    raw_visual = len(vhits)
    # SR.6: when the planner flags a query an embedding mis-handles (attribute /
    # negation / composition), VLM-verify the candidates before they become
    # evidence. No-op under the passthrough stub; the claim is the FULL query
    # (search_terms may have dropped the discriminating word).
    verified = getattr(plan, "needs_visual_verification", False)
    if verified:
        from va.pipeline.verify import verify_visual_hits

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
        from va.storage.vector.sharded import ShardedVectorStore

        index_populated = (
            ShardedVectorStore(Workspace(workdir).videos_root,
                               shard_name="text_vectors.npz").count() > 0
        )
        nt = len(ev.items)
        if index_populated:
            hits = search_text(terms, workdir=workdir, k=k, modalities=wanted)
            for h in hits:
                ev.items.append(from_text_hit(h))
            trace("retriever", "gather:text", f"{len(hits)} semantic-text hits",
                  modalities=wanted, count=len(hits), items=_preview(ev.items[nt:]))
        else:
            ev.notes.append("semantic text index empty — lexical fallback "
                            "(run `va reingest`/backfill to enable semantic text retrieval)")
            _gather_lexical(ev, wanted, terms, workdir, k)
            trace("retriever", "gather:text",
                  f"{len(ev.items) - nt} lexical-fallback hits (semantic index empty)",
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
    # empties everything.
    gate = gate if gate is not None else get_relevance_gate()
    if gate.active and ev.items:
        kept = [it for it in ev.items if gate.keeps(it)]
        dropped = len(ev.items) - len(kept)
        if dropped:
            ev.notes.append(
                f"relevance gate dropped {dropped}/{len(ev.items)} below floor "
                f"(min_rerank={gate.min_rerank}, min_cosine={gate.min_cosine})"
                + ("; no candidate cleared the floor — no match" if not kept else "")
            )
        trace("retriever", "gate", f"kept {len(kept)}, dropped {dropped}",
              level=("warn" if not kept else "info"), kept=len(kept), dropped=dropped,
              min_rerank=gate.min_rerank, min_cosine=gate.min_cosine)
        ev.items = kept

    if len(ev.items) > MAX_ITEMS:
        ev.notes.append(f"retriever: kept top {MAX_ITEMS} of {len(ev.items)} ranked items")
        ev.items = ev.items[:MAX_ITEMS]
    ev.attributes["fusion"] = {
        "method": "rerank+native min-max blend",
        "rerank_weight": RERANK_WEIGHT,
        "gate": {"min_rerank": gate.min_rerank, "min_cosine": gate.min_cosine},
    }
    return ev
