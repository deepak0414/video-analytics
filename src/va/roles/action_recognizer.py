"""Role 7 — Action/Event Recognizer (the contract).

Labels what HAPPENS over time (eating, driving, falling) — properties of frame
sequences that no single frame can show. Open-vocabulary like Role 5: callers
pass the action phrases to look for. Runs per time span (normally the Role-1
segments — the shot is the natural unit of an action). Backends (motion stub,
X-CLIP, cloud) are interchangeable; each owns its own frame sampling.
"""
from __future__ import annotations

from typing import List, Protocol, Sequence, Tuple, runtime_checkable

from va.contracts.action import ActionEvent

# Abstention foil. A contrastive recognizer (X-CLIP) softmaxes over the phrases
# it is given and MUST pick one, so on footage that shows no listed action it
# forces a wrong-but-confident label (measured: a pet-snake clip scored
# "feeding animals" 0.91). Including this phrase gives the softmax somewhere to
# put probability when nothing fits; backends treat it as "no event", never as a
# stored label. The recognizer injects it even if a custom vocab omits it.
NO_ACTION = "no particular action"

# Generic activities scored at ingest time (architecture: "always-on for common
# actions, on-demand for specific ones"). HARDCODED CONTENT, deliberately kept
# generic — domain vocabularies belong in roles.yaml `actions:` (same override
# pattern as object_detector `classes:`). NO_ACTION rides along as the abstention
# floor (see above); it is a mechanism, not a domain action.
DEFAULT_INGEST_ACTIONS = [
    "walking", "running", "driving a car", "riding a bicycle",
    "eating", "drinking", "talking", "dancing",
    "jumping", "swimming", "cooking", "playing a sport",
    "feeding animals", "sitting still",
    NO_ACTION,
]


@runtime_checkable
class ActionRecognizer(Protocol):
    def recognize(
        self,
        media_path: str,
        spans: Sequence[Tuple[float, float]],
        actions: Sequence[str],
    ) -> List[List[ActionEvent]]:
        """Per input span, the recognized actions among `actions` with
        start/end set to the span (video_id/segment_id left unset — the
        pipeline fills them). An empty inner list = nothing confident."""
        ...
