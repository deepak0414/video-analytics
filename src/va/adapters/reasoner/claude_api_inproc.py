"""Claude API Role-11 backend — PLACEHOLDER.

The production cloud path per the architecture doc (best reasoning quality,
pay-per-query). Deliberately not implemented yet: whether/how to fund an
ANTHROPIC_API_KEY is an open decision (a Claude subscription does NOT include
API access — see the discussion in the project log).

When the key decision lands, this becomes a thin adapter: anthropic SDK,
messages.create with the shared prompts (vision-capable model so keyframes go
in as image blocks), same JSON-out parsing as the other backends. Until then,
use `claude-code` (subscription CLI) or `qwen` (local GPU).
"""
from __future__ import annotations

from typing import Any

_MESSAGE = (
    "The `claude-api` reasoner backend is a placeholder pending the "
    "ANTHROPIC_API_KEY decision. Use reasoner model `claude-code` (subscription "
    "CLI) or `qwen` (local GPU) in config/roles.yaml meanwhile."
)


class ClaudeApiReasoner:
    def __init__(self, load: dict[str, Any] | None = None):
        raise NotImplementedError(_MESSAGE)
