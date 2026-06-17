"""Rule-based Role-11 stub — deterministic, dependency-free, for tests + fallback.

plan(): keyword heuristics over the query (the same intent classes the LLM
planner uses). reason(): extractive — turns the strongest evidence items into
statements with citations, no generation. Tests the entire ask pipeline offline;
also the graceful fallback when an LLM backend fails to produce valid JSON.
"""
from __future__ import annotations

import re
from typing import Sequence
from uuid import UUID

from va.contracts.evidence import Evidence
from va.contracts.query_plan import Answer, QueryPlan
from va.roles.reasoner import Keyframe

_STOP = {
    "what", "whats", "what's", "who", "whos", "which", "when", "where", "how",
    "many", "much", "is", "are", "was", "were", "the", "a", "an", "of", "in",
    "on", "at", "to", "does", "do", "did", "there", "their", "it", "its",
    "color", "colour", "number",
}

_TRANSCRIPT = re.compile(r"\b(say|says|said|saying|mention|mentions|mentioned|talk|talks|talked|speak|speaks|spoke|spoken|discuss|discussed)\b", re.I)
_COUNT = re.compile(r"\b(how many|count|number of|distinct|different)\b", re.I)
_ACTION = re.compile(r"\b(eat|eats|eating|run|running|jump|jumping|enter|enters|entering|exit|leave|leaving|fall|falling|throw|throwing|catch|catching|open|opening|close|closing)\b", re.I)
_VISUAL_Q = re.compile(r"\b(color|colour|wearing|look like|looks like|visible|shown|appearance|who is|what is)\b", re.I)
# text ON screen (Role 10) — intent words for the surface, like _TRANSCRIPT for speech
_ONSCREEN = re.compile(
    r"\b(on[- ]screen|billboard|signs?|subtitles?|title card|title|"
    r"text|written|writing|label|labels?|overlay|slide|slides)\b", re.I,
)
# counting events/changes over time -> Tier 5b exhaustive sweep
_DEEP_SCAN = re.compile(
    r"\b(how many times?|how often|each time|every time)\b"
    r"|\bcount\b.*\bchang(e|es|ed|ing)\b|\bchang(e|es|ed|ing)\b.*\bhow many\b"
    r"|\bhow many\b.*\bchang(e|es|ed|ing)\b"
    # event/visit counting ("count the birds visiting", "how many landings",
    # "how many birds come and feed")
    r"|\b(count|how many)\b.*\b(visit|visits|visiting|appear|appears|appearances?"
    r"|landings?|arrive|arrives|arriving|come|comes|coming|feed|feeds|feeding)\b",
    re.I,
)
# ^ This vocabulary is CLOSED (keyword-zoo guard). It is the deterministic floor
# for weak-planner (qwen) and offline paths only — capable LLM planners flag
# deep-scan from semantics alone (verified: claude planner caught "come and
# feed" with no regex help). New phrasings that miss here are an LLM-planner
# concern, not grounds to grow this list.
# words that describe the COUNTING itself, not the observed subject — excluded
# when deriving a scan target from the query (no canned content: the target is
# built from the user's own nouns; see CLAUDE.md "Heuristics & validation").
_SCAN_NOISE = _STOP | {
    "time", "times", "change", "changes", "changed", "changing", "often",
    "count", "counts", "video", "clip", "entire", "whole", "first", "last",
    "scene", "scenes", "she", "he", "they", "her", "his",
}


class RuleReasoner:
    def plan(self, query: str) -> QueryPlan:
        plan = QueryPlan(query=query)
        plan.needs_caption_search = True  # cheap, almost always useful
        if _TRANSCRIPT.search(query):
            plan.needs_transcript_search = True
        if _ONSCREEN.search(query):
            plan.needs_ocr_search = True
        if _COUNT.search(query) or _ACTION.search(query) or _VISUAL_Q.search(query):
            plan.needs_object_query = True
        if _ACTION.search(query):
            plan.needs_action_query = True
        if _VISUAL_Q.search(query) or _ACTION.search(query):
            plan.needs_vlm_reasoning = True
        if _DEEP_SCAN.search(query):
            plan.needs_deep_scan = True
            plan.needs_vlm_reasoning = True
            # Scan target = the query's own content words (e.g. "the girl dress"),
            # never canned content. Weak grammar is fine — the micro-prompt wraps it.
            subject = [w for w in re.findall(r"[a-z0-9']+", query.lower())
                       if w not in _SCAN_NOISE]
            plan.params["scan_target"] = ("the " + " ".join(subject)) if subject else None
        words = [w for w in re.findall(r"[a-z0-9']+", query.lower()) if w not in _STOP]
        plan.search_terms = " ".join(words) or query
        return plan

    def reason(
        self, query: str, evidence: Evidence, keyframes: Sequence[Keyframe] = ()
    ) -> Answer:
        items = []
        citations: list[tuple[UUID, float]] = []
        # strongest item per modality, in a stable priority order
        priority = ["deep_scan_count", "object_count", "co_occurrence", "action", "transcript", "on_screen_text", "caption", "object", "visual"]
        seen_modalities = set()
        for modality in priority:
            best = max(
                (i for i in evidence.items if i.modality == modality),
                key=lambda i: i.score, default=None,
            )
            if best is None or modality in seen_modalities:
                continue
            seen_modalities.add(modality)
            entry = {"statement": f"{modality} evidence: {best.content}"}
            if best.video_id is not None:
                entry["video_id"] = str(best.video_id)
                entry["timestamp"] = best.time_start
                citations.append((best.video_id, best.time_start))
            items.append(entry)

        summary = (
            f"Found {len(items)} relevant evidence type(s) for: {query}"
            if items else f"No relevant evidence found for: {query}"
        )
        return Answer(
            text=summary, citations=citations,
            attributes={"items": items, "backend": "rule"},
        )
