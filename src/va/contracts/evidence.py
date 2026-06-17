"""Evidence contracts — the bundle Role 11 reasons over.

Same schema-evolution rules as query_plan.py: defaults on every field,
extra="allow" (unknown fields parse and round-trip), an `attributes` bag per
item for modality-specific payload, and a schema_version stamp.

An EvidenceItem is one piece of retrieved context. The typed core (modality,
video_id, time span, content, score) is what any reasoner can rely on; anything
modality-specific (speaker, segment_index, future bbox/track_id) goes in
`attributes` so new roles never require a schema change.
"""
from __future__ import annotations

from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Well-known modality strings (open vocabulary — new roles add new strings).
MODALITY_VISUAL = "visual"        # Role 2 frame hit
MODALITY_CAPTION = "caption"      # Role 4 segment caption
MODALITY_TRANSCRIPT = "transcript"  # Role 8 speech line
MODALITY_OBJECT = "object"        # Role 5 detection summary
MODALITY_ON_SCREEN_TEXT = "on_screen_text"  # Role 10 OCR line
MODALITY_ACTION = "action"        # Role 7 recognized action
MODALITY_OBJECT_COUNT = "object_count"  # Role 6 distinct-instance count
MODALITY_CO_OCCURRENCE = "co_occurrence"  # Roles 5/6 temporal join (classes together)


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    modality: str = ""
    video_id: Optional[UUID] = None
    time_start: float = 0.0
    time_end: float = 0.0
    content: str = ""              # text rendering for the reasoner
    score: float = 0.0
    source_role: Optional[int] = None  # which pipeline role produced it
    attributes: dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    """Everything assembled for one query, ready for a reasoner."""

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    query: str = ""
    items: List[EvidenceItem] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)  # e.g. tiers skipped/unavailable
    attributes: dict[str, Any] = Field(default_factory=dict)


# --- converters from today's hit types --------------------------------------

def from_search_hit(h) -> EvidenceItem:
    """va.contracts.embedding.SearchHit -> EvidenceItem."""
    return EvidenceItem(
        modality=MODALITY_VISUAL, video_id=h.video_id,
        time_start=h.timestamp, time_end=h.timestamp,
        content=f"visual match at {h.timestamp:.1f}s in {h.source_uri}",
        score=h.score, source_role=2,
        attributes={"source_uri": h.source_uri},
    )


def from_caption_hit(h) -> EvidenceItem:
    """va.storage.structured.segments.CaptionHit -> EvidenceItem."""
    return EvidenceItem(
        modality=MODALITY_CAPTION, video_id=h.video_id,
        time_start=h.start_time, time_end=h.end_time,
        content=h.caption, score=h.score, source_role=4,
        attributes={"segment_index": h.segment_index},
    )


def from_transcript_hit(h) -> EvidenceItem:
    """va.storage.structured.transcripts.TranscriptHit -> EvidenceItem."""
    return EvidenceItem(
        modality=MODALITY_TRANSCRIPT, video_id=h.video_id,
        time_start=h.start_time, time_end=h.end_time,
        content=h.text, score=h.score, source_role=8,
        attributes={"speaker": h.speaker},
    )


def from_action_hit(h) -> EvidenceItem:
    """va.storage.structured.actions.ActionHit -> EvidenceItem.

    The wording carries the recognizer's limitation: per-segment zero-shot
    labels are 'most plausible of the requested vocabulary', not certainties."""
    return EvidenceItem(
        modality=MODALITY_ACTION, video_id=h.video_id,
        time_start=h.start_time, time_end=h.end_time,
        content=(f"action '{h.action_class}' recognized from {h.start_time:.1f}s "
                 f"to {h.end_time:.1f}s (model confidence {h.confidence:.2f}; "
                 f"best match among the configured action vocabulary)"),
        score=h.score, source_role=7,
        attributes={"action_class": h.action_class, "confidence": h.confidence},
    )


def from_ocr_hit(h) -> EvidenceItem:
    """va.storage.structured.ocr.OcrHit -> EvidenceItem."""
    seen = (f"on screen at {h.time_start:.1f}s" if h.sightings == 1 else
            f"on screen {h.sightings}x between {h.time_start:.1f}s and {h.time_end:.1f}s")
    return EvidenceItem(
        modality=MODALITY_ON_SCREEN_TEXT, video_id=h.video_id,
        time_start=h.time_start, time_end=h.time_end,
        content=f'text "{h.text}" {seen}',
        score=h.score, source_role=10,
        attributes={"text": h.text, "sightings": h.sightings},
    )


def from_co_occurrence(c) -> EvidenceItem:
    """va.storage.structured.detections.CoOccurrence -> EvidenceItem."""
    classes = " + ".join(c.classes)
    return EvidenceItem(
        modality=MODALITY_CO_OCCURRENCE, video_id=c.video_id,
        time_start=c.time_start, time_end=c.time_end,
        content=(f"{classes} appear TOGETHER from {c.time_start:.1f}s to "
                 f"{c.time_end:.1f}s ({c.frames} frames)"),
        score=min(1.0, 0.5 + 0.1 * c.frames), source_role=6,
        attributes={"classes": list(c.classes), "frames": c.frames},
    )


def from_distinct_count(c) -> EvidenceItem:
    """va.storage.structured.tracks.DistinctCount -> EvidenceItem.

    The wording carries the tracker's limitation explicitly: tracks are NOT
    re-identified across absences, so for subjects that leave and return this
    number overcounts individuals (observed: ~10 'distinct birds' for ~2-4 real
    ones across 5 visits). Evidence must state its own reliability."""
    return EvidenceItem(
        modality=MODALITY_OBJECT_COUNT, video_id=c.video_id,
        time_start=c.first_seen, time_end=c.last_seen,
        content=(f"{c.distinct} '{c.object_class}' track(s) between "
                 f"{c.first_seen:.1f}s and {c.last_seen:.1f}s — CAUTION: tracks are "
                 f"not re-identified across separate appearances; if the subject "
                 f"leaves and returns this OVERCOUNTS individuals. Prefer deep-scan "
                 f"episodes for visits and distinct states for kinds, if present."),
        score=1.0, source_role=6,
        attributes={"distinct": c.distinct, "object_class": c.object_class},
    )


def from_text_hit(h) -> EvidenceItem:
    """va.pipeline.text_search.TextHit -> EvidenceItem.

    The semantic (SR.2) counterpart to the per-modality lexical converters above:
    one row from the unified text index, whose `modality` already matches the
    well-known strings. Carries the bi-encoder cosine as the native score; the
    retriever (SR.4) reranks and fuses it from here."""
    return EvidenceItem(
        modality=h.modality, video_id=h.video_id,
        time_start=h.time_start, time_end=h.time_end,
        content=h.text, score=h.score, source_role=h.source_role,
        attributes={"semantic": True},
    )


def from_object_summary(s) -> EvidenceItem:
    """va.storage.structured.detections.ObjectSummary -> EvidenceItem."""
    return EvidenceItem(
        modality=MODALITY_OBJECT, video_id=s.video_id,
        time_start=s.first_seen, time_end=s.last_seen,
        content=(f"'{s.object_class}' detected in {s.frames} frames "
                 f"between {s.first_seen:.1f}s and {s.last_seen:.1f}s "
                 f"(frame appearances, not distinct objects)"),
        score=s.max_confidence, source_role=5,
        attributes={"frames": s.frames, "object_class": s.object_class},
    )
