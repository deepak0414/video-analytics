"""Evidence assembler — run the searches a QueryPlan asks for, return one
Evidence bundle (the object a Role-11 reasoner consumes).

Forward-compatible by design: tiers that aren't implemented yet (objects,
actions) or unknown flags a future planner might emit are skipped and recorded
in Evidence.notes rather than raising — the reasoner can then see what context
is missing.
"""
from __future__ import annotations

from va.contracts.evidence import (
    Evidence,
    from_action_hit,
    from_caption_hit,
    from_co_occurrence,
    from_distinct_count,
    from_object_summary,
    from_ocr_hit,
    from_search_hit,
    from_transcript_hit,
)
from va.contracts.query_plan import QueryPlan

# Tier flags requested but not yet implemented -> note for the reasoner.
# (needs_vlm_reasoning is handled by the ask pipeline, not the assembler.)
# Empty since Role 7 landed; the mechanism stays for future tiers.
_UNAVAILABLE: dict[str, str] = {}


def assemble(plan: QueryPlan, workdir: str = ".va", k: int = 5) -> Evidence:
    ev = Evidence(query=plan.query)
    terms = plan.search_terms or plan.query

    # Tier 1 (visual) always runs — the architecture's instant first pass.
    from va.pipeline.query import query as visual_query

    for h in visual_query(terms, workdir=workdir, k=k):
        ev.items.append(from_search_hit(h))

    if plan.needs_caption_search:
        from va.pipeline.caption import search_captions

        for h in search_captions(terms, workdir=workdir, k=k):
            ev.items.append(from_caption_hit(h))

    if plan.needs_transcript_search:
        from va.pipeline.transcript import search_transcripts

        for h in search_transcripts(terms, workdir=workdir, k=k):
            ev.items.append(from_transcript_hit(h))

    if plan.needs_action_query:
        from va.pipeline.actions import search_actions

        for h in search_actions(terms, workdir=workdir, k=k):
            ev.items.append(from_action_hit(h))

    if plan.needs_ocr_search:
        from va.pipeline.ocr import search_ocr

        for h in search_ocr(terms, workdir=workdir, k=k):
            ev.items.append(from_ocr_hit(h))

    if plan.needs_object_query:
        from va.pipeline.objects import count_objects, query_objects
        from va.pipeline.paths import Workspace
        from va.storage.structured.detections import DetectionStore

        for s in query_objects(terms, workdir=workdir):
            ev.items.append(from_object_summary(s))
        # Role 6: distinct-instance counts ride along with object queries.
        for c in count_objects(terms, workdir=workdir):
            ev.items.append(from_distinct_count(c))
        # Temporal join: if the query names >=2 known classes, find the windows
        # where they appear together ("person AT the car").
        store = DetectionStore(Workspace(workdir).catalog_db)
        try:
            known = set(store.existing_classes())
            asked = [w for w in terms.lower().split() if w in known]
            if len(set(asked)) >= 2:
                for co in store.co_occurrence(asked)[:3]:
                    ev.items.append(from_co_occurrence(co))
        finally:
            store.close()

    # Requested-but-unavailable tiers: note them instead of failing.
    for flag, note in _UNAVAILABLE.items():
        if getattr(plan, flag, False):
            ev.notes.append(note)

    # Unknown extra flags from a newer planner: preserved by the model; note
    # any that look like tier requests so the gap is visible to the reasoner.
    known = set(QueryPlan.model_fields)
    for name, value in (plan.model_extra or {}).items():
        if name.startswith("needs_") and value and name not in known:
            ev.notes.append(f"unknown tier flag {name!r} requested; skipped")

    return ev
