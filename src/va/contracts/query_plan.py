"""QueryPlan + Answer contracts — runtime data between planner and executor.

Schema-evolution rules (these are runtime/LLM-produced payloads that will change
as the system improves):
- Every field has a default -> a payload missing fields (older producer, or a
  field we later remove) still parses.
- extra="allow" -> a payload with fields we don't know yet (newer producer)
  parses AND round-trips without dropping them.
- `params` is a free-form bag for new knobs that don't warrant a schema change.
- `schema_version` marks the rare intentionally-breaking revision.
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class QueryPlan(BaseModel):
    """Which tiers should run for a query (architecture doc, Query Pipeline)."""

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    query: str = ""

    # Tier flags (mirroring the architecture doc; all optional with defaults).
    needs_transcript_search: bool = False
    needs_caption_search: bool = False
    needs_ocr_search: bool = False     # Role 10: text ON screen (signs, titles)
    needs_object_query: bool = False
    needs_action_query: bool = False
    needs_vlm_reasoning: bool = False
    # SR.6: re-check visual candidates by LOOKING (VLM), for queries an embedding
    # mis-handles — a specific attribute (colour), a negation, or a composition.
    needs_visual_verification: bool = False
    # Tier 5b: exhaustive frame sweep + code-side counting (architecture doc,
    # "Deep-Scan Escalation"). For counting-events/changes-over-time queries.
    needs_deep_scan: bool = False

    # Optional refinements the planner may emit; safe to omit.
    search_terms: Optional[str] = None     # rephrased terms for text searches
    params: dict[str, Any] = Field(default_factory=dict)


class Answer(BaseModel):
    """Role 11 output: a grounded answer with cited moments."""

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    text: str = ""
    citations: List[Tuple[UUID, float]] = Field(default_factory=list)  # (video_id, timestamp)
    attributes: dict[str, Any] = Field(default_factory=dict)
