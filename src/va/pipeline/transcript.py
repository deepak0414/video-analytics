"""Transcript search — "when did they say X" over the transcripts table.

A second query modality alongside visual search (Role 2). Unifying the two under
the progressive-escalation planner is a later orchestration concern; for now this
is a distinct search path.
"""
from __future__ import annotations

from typing import List

from va.pipeline.paths import Workspace
from va.storage.structured.transcripts import TranscriptHit, TranscriptStore


def search_transcripts(
    text: str, workdir: str = ".va", k: int = 10, speaker: str | None = None
) -> List[TranscriptHit]:
    store = TranscriptStore(Workspace(workdir).catalog_db)
    try:
        return store.search(text, k=k, speaker=speaker)
    finally:
        store.close()
