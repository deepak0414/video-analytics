"""Shared prompt templates + parsing helpers for LLM reasoner backends."""
from __future__ import annotations

import json
import re
from typing import Optional, Sequence

from va.contracts.evidence import Evidence
from va.roles.reasoner import Keyframe

PLANNER_PROMPT = """You are the query planner for a video search system. Decide which \
retrieval tiers a user query needs. Available flags (set true only when useful):
- needs_transcript_search: the query is about something SAID/spoken in the video
- needs_caption_search: the query is about scene content/descriptions ("the kitchen scene")
- needs_ocr_search: the query is about text visible ON SCREEN (signs, billboards,
  title cards, burned-in captions, slides) — reading, not listening
- needs_object_query: the query involves specific objects or counting them
- needs_action_query: the query involves an action/event over time (eating, falling)
- needs_vlm_reasoning: answering requires LOOKING at frames (attributes, colors, relations)
- needs_visual_verification: the query hinges on a specific visual ATTRIBUTE (e.g. a
  colour: "blue car"), a NEGATION, or a COMPOSITION/relation ("person feeding a snake")
  that an image embedding often gets wrong — so candidate frames should be double-checked
  by a VLM. Set this for "blue Ferrari" or "two people hugging", NOT for plain scenes
- needs_deep_scan: the query counts EVENTS/CHANGES OVER TIME ("how many times...",
  "each time...") which sparse retrieval cannot answer; when set, also put
  "scan_target" inside "params": the thing to observe per frame, as a SHORT PLAIN
  NOUN PHRASE of 2-5 words taken from the query (e.g. "the girl's dress") — no
  parentheses, no examples, no style notes
Also set "search_terms": short keywords for retrieval (drop question words).

User query: {query}

Respond with ONLY a JSON object, e.g.:
{{"needs_caption_search": true, "needs_object_query": true, "needs_vlm_reasoning": true, "search_terms": "person red car"}}"""

REASONER_PROMPT = """You are answering a question about video content using retrieved \
evidence and keyframe images. Be precise and conservative: claim only what the evidence \
or images support. If several distinct instances answer the question (e.g. different \
people at different times), report each as its own item.

If the evidence contains a "deep_scan_count" item, its numbers were computed \
DETERMINISTICALLY IN CODE from an exhaustive frame scan — do NOT recount from the other \
items yourself. It offers THREE statistics; pick the one matching the question:
- "how many different/distinct X" -> distinct states
- "how many times does X change" on edited footage -> distinct states (raw transitions \
overcount montage intercutting)
- "how many visits/appearances/landings" -> EPISODES (separate contiguous appearances)
Use the "observation" items only to describe and timestamp the individual events.

Question: {query}

Retrieved evidence:
{evidence}

{keyframe_note}

Respond with ONLY a JSON object:
{{"items": [{{"statement": "<one finding, human-readable>", "video_id": "<uuid of the video>", "timestamp": <seconds, the moment supporting it>}}, ...],
  "summary": "<one-sentence overall answer; say so plainly if the evidence is insufficient>"}}
Use the video_id/timestamp printed beside the supporting evidence item or keyframe. \
Omit "video_id"/"timestamp" only when a finding isn't tied to one moment."""


NORMALIZE_PROMPT = """You are normalizing noisy per-frame labels from a video scan so \
that code can count state changes. The scan was watching: {subject}

Below are the RAW labels observed and the TIMELINE of when each was seen. Map EVERY \
raw label:
- Same real-world state described differently -> the SAME short canonical label \
(e.g. 'olive strapless' and 'green strapless gown' are one dress -> 'green strapless').
- Do NOT merge labels that differ in garment type or style detail, even with the same \
color: 'pink satin gown' and 'pink fluffy dress' are DIFFERENT states. Merge only true \
respellings/synonyms of the same item.
- TIMELINE EXCEPTION: if two similar labels (same base color/kind, different \
texture/pattern words) ALTERNATE within one continuous span of the timeline, they are \
usually the SAME moving subject seen from different angles (e.g. a bird's 'brown \
speckled' side vs 'brown striped' back) -> merge them. Separated-in-time same-color \
labels with clearly different style words remain distinct.
- A label describing something OTHER than {subject} (a different person/object, \
a cut-away) -> exactly "OTHER".
- A label meaning nothing relevant is visible -> exactly "NONE".

Raw labels:
{labels}

Timeline (state @ seconds):
{timeline}

Respond with ONLY JSON: {{"mapping": {{"<raw label>": "<canonical|OTHER|NONE>", ...}}}} \
covering every raw label."""


def render_evidence(ev: Evidence, max_items: int = 60) -> str:
    """Text rendering of an Evidence bundle for LLM consumption.

    Items are selected ROUND-ROBIN across modalities (not list order): with a
    large visual top-k, list order would let visual hits crowd every caption/
    transcript/object item out of the truncated prompt — observed in practice
    (the dress-counting query: 25 visual hits hid all 25 caption items).
    Within a modality, original (score) order is kept; rendering is sorted by
    time so the reasoner sees a chronological timeline.
    """
    by_modality: dict[str, list] = {}
    order: list[str] = []
    for item in ev.items:
        if item.modality not in by_modality:
            by_modality[item.modality] = []
            order.append(item.modality)
        by_modality[item.modality].append(item)

    picked = []
    rank = 0
    while len(picked) < max_items and any(by_modality.values()):
        for modality in order:
            bucket = by_modality[modality]
            if rank < len(bucket) and len(picked) < max_items:
                picked.append(bucket[rank])
        rank += 1
        if all(rank >= len(b) for b in by_modality.values()):
            break

    picked.sort(key=lambda i: (i.time_start, i.modality))
    lines = []
    for i, item in enumerate(picked, 1):
        span = (f"{item.time_start:.1f}s"
                if item.time_start == item.time_end
                else f"{item.time_start:.1f}-{item.time_end:.1f}s")
        lines.append(
            f"[E{i}] ({item.modality} @ {span}, video_id={item.video_id}, "
            f"score={item.score:.2f}) {item.content}"
        )
    for note in ev.notes:
        lines.append(f"[note] {note}")
    return "\n".join(lines) if lines else "(no evidence retrieved)"


def render_keyframe_note(keyframes: Sequence[Keyframe], with_paths: bool = False) -> str:
    if not keyframes:
        return "(no keyframes attached)"
    lines = ["Attached keyframes (in order):"]
    for i, kf in enumerate(keyframes, 1):
        loc = f" file={kf.path}" if with_paths and kf.path else ""
        lines.append(f"[K{i}] video_id={kf.video_id} @ {kf.timestamp:.1f}s{loc}")
    return "\n".join(lines)


def coerce_timestamp(value) -> Optional[float]:
    """LLMs write timestamps loosely: 3.5, "3.5", "3.5s", "1:07", "01:02:03".
    Coerce to seconds, or None if hopeless."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lower().rstrip("s").strip()
    if ":" in s:
        try:
            parts = [float(p) for p in s.split(":")]
        except ValueError:
            return None
        secs = 0.0
        for p in parts:
            secs = secs * 60 + p
        return secs
    try:
        return float(s)
    except ValueError:
        return None


def parse_json_block(text: str) -> Optional[dict]:
    """Extract the first JSON object from LLM output (tolerates code fences)."""
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None
