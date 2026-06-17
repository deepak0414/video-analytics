"""Action search — "when does the eating happen" over action_events.

A fifth query modality: what HAPPENS over time (Role 7), alongside what's
visible (Role 2), described (Role 4), said (Role 8), and on screen (Role 10).
"""
from __future__ import annotations

from typing import List

from va.pipeline.paths import Workspace
from va.storage.structured.actions import ActionHit, ActionStore


def search_actions(text: str, workdir: str = ".va", k: int = 10) -> List[ActionHit]:
    store = ActionStore(Workspace(workdir).catalog_db)
    try:
        return store.search(text, k=k)
    finally:
        store.close()
