"""Claude Role-11 backend via the locally authenticated `claude` CLI.

Uses headless mode (`claude -p ... --output-format json`), which runs on the
machine's existing Claude subscription login — no API key. Keyframes are passed
as file paths the CLI reads with its Read tool (Claude Code renders images
natively).

PoC/dev convenience, not a production pattern: it shares the subscription's
rate limits and spawns a process per call. The production path is the
`claude-api` backend (pending the ANTHROPIC_API_KEY decision).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Sequence
from uuid import UUID

from va.adapters.reasoner.prompts import (
    PLANNER_PROMPT,
    REASONER_PROMPT,
    coerce_timestamp,
    parse_json_block,
    render_evidence,
    render_keyframe_note,
)
from va.adapters.reasoner.rule_inproc import RuleReasoner
from va.contracts.evidence import Evidence
from va.contracts.query_plan import Answer, QueryPlan
from va.roles.reasoner import Keyframe


class ClaudeCliReasoner:
    def __init__(self, load: dict[str, Any] | None = None):
        load = load or {}
        self.binary = load.get("claude_binary", "claude")
        self.timeout = int(load.get("timeout_seconds", 120))
        if shutil.which(self.binary) is None:
            raise RuntimeError(
                f"`{self.binary}` CLI not found on PATH — the claude-code backend "
                "needs a logged-in Claude Code install. Use model `qwen` otherwise."
            )
        self._fallback = RuleReasoner()

    def _call(self, prompt: str, allow_read: bool = False) -> str:
        cmd = [self.binary, "-p", prompt, "--output-format", "json"]
        if allow_read:
            cmd += ["--allowedTools", "Read"]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=self.timeout
        )
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI failed: {proc.stderr[:300]}")
        # --output-format json wraps the reply: {"result": "<text>", ...}
        try:
            return json.loads(proc.stdout).get("result", proc.stdout)
        except json.JSONDecodeError:
            return proc.stdout

    def plan(self, query: str) -> QueryPlan:
        doc = parse_json_block(self._call(PLANNER_PROMPT.format(query=query)))
        if doc is None:
            return self._fallback.plan(query)
        doc.setdefault("query", query)
        plan = QueryPlan.model_validate(doc)
        plan.query = query
        return plan

    def reason(
        self, query: str, evidence: Evidence, keyframes: Sequence[Keyframe] = ()
    ) -> Answer:
        with_files = [kf for kf in keyframes if kf.path]
        note = render_keyframe_note(with_files, with_paths=True)
        if with_files:
            note += "\nRead each keyframe file above with the Read tool before answering."
        prompt = REASONER_PROMPT.format(
            query=query, evidence=render_evidence(evidence), keyframe_note=note,
        )
        raw = self._call(prompt, allow_read=bool(with_files))
        doc = parse_json_block(raw)
        if doc is None:
            ans = self._fallback.reason(query, evidence, keyframes)
            ans.attributes["backend"] = "claude-code(fallback-rule)"
            ans.attributes["raw"] = raw[:500]
            return ans
        items = [i for i in doc.get("items", []) if isinstance(i, dict)]
        citations: list[tuple[UUID, float]] = []
        for i in items:
            ts = coerce_timestamp(i.get("timestamp"))
            if ts is not None:
                i["timestamp"] = ts
            try:
                if ts is not None:
                    citations.append((UUID(str(i["video_id"])), ts))
            except (KeyError, ValueError, TypeError):
                continue
        return Answer(
            text=str(doc.get("summary", "")), citations=citations,
            attributes={"items": items, "backend": "claude-code"},
        )
