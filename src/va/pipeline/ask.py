"""The ask pipeline — Role 11 end to end.

    question -> plan (Role 11) -> retrieve evidence (SR.4: gather all
                                  modalities, rerank, fuse, rank)
             -> extract keyframes at candidate moments
             -> reason (Role 11, sees evidence + keyframes)
             -> Answer rendered with hyperlinked timestamps

Hyperlinks: YouTube sources become `watch?v=<id>&t=<s>s` deep links; local
files render as `path @ m:ss`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from uuid import UUID

from va.contracts.evidence import Evidence
from va.contracts.query_plan import Answer, QueryPlan
from va.contracts.video import SourceType, Video
from va.pipeline.retrieval import retrieve
from va.pipeline.paths import Workspace
from va.pipeline.trace_links import trace_ingest_links
from va.registry import get_reasoner
from va.runtime.trace import trace, traced_run
from va.roles.reasoner import Keyframe
from va.storage.structured.catalog_sqlite import Catalog

# Modalities whose moments are worth LOOKING at, in priority order.
_KEYFRAME_PRIORITY = ["co_occurrence", "visual", "caption", "object"]


@dataclass
class AskResult:
    question: str
    plan: QueryPlan
    evidence: Evidence
    answer: Answer
    rendered: str


def _ts(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


# Self-reported insufficiency in a sparse answer (phrases observed from claude
# and the rule fallback; "unknown" included — hedged sparse answers deserve the
# escalation since it runs at most once and only when no deep scan happened).
_INSUFFICIENT = re.compile(
    r"insufficien|not enough|cannot (be )?(determin|count|tell|confirm|verif)"
    r"|can't (determine|tell|count)|unable to|no (relevant )?evidence"
    r"|unclear from|unknown",
    re.I,
)


def _answer_insufficient(answer: Answer) -> bool:
    if _INSUFFICIENT.search(answer.text or ""):
        return True
    items = answer.attributes.get("items") or []
    return not items and not answer.citations   # uncited AND empty = a shrug


def _deep_scan_into(evidence: Evidence, plan: QueryPlan, workdir: str, reasoner) -> None:
    """Run the Tier-5b sweep and fold its evidence in (degrades to a note)."""
    from va.pipeline.deep_scan import run_deep_scan

    try:
        ds = run_deep_scan(evidence, plan, workdir, reasoner=reasoner)
        if ds is not None:
            evidence.items.extend(ds.evidence_items)
            evidence.notes.append(
                f"deep-scan: {len(ds.observations)} frames of '{ds.scan_target}' "
                f"({'cached' if ds.cached else 'fresh sweep'}); "
                f"code-counted changes: {ds.changes_low}-{ds.changes_high}"
            )
        else:
            evidence.notes.append("deep-scan requested but no target video resolvable")
    except Exception as e:  # noqa: BLE001 — degrade, don't fail the ask
        evidence.notes.append(f"deep-scan failed: {e}")


def _link(video: Optional[Video], seconds: float) -> str:
    """Human-clickable reference to a moment in a video."""
    stamp = _ts(seconds)
    if video is None:
        return f"[{stamp}]"
    if video.source_type is SourceType.youtube:
        url = f"https://www.youtube.com/watch?v={video.source_key}&t={int(seconds)}s"
        return f"[{stamp}]({url})"
    return f"[{stamp} @ {video.source_uri}]"


def _collect_keyframes(
    evidence: Evidence, workdir: str, max_keyframes: int
) -> List[Keyframe]:
    """Pull frames at the strongest evidence moments so the reasoner can see
    pixels (attributes like 'shirt color' exist only in frames)."""
    from va.media.frames import frames_at

    catalog = Catalog(Workspace(workdir).catalog_db)
    picked: list[tuple[UUID, float]] = []
    seen: set[tuple[str, int]] = set()
    try:
        for modality in _KEYFRAME_PRIORITY:
            for item in sorted(
                (i for i in evidence.items if i.modality == modality),
                key=lambda i: -i.score,
            ):
                if item.video_id is None or len(picked) >= max_keyframes:
                    continue
                # for windows, look at the middle; for instants, the instant
                ts = (item.time_start + item.time_end) / 2.0
                key = (str(item.video_id), int(ts))
                if key in seen:
                    continue
                seen.add(key)
                picked.append((item.video_id, ts))

        ws = Workspace(workdir)
        out: List[Keyframe] = []
        for vid, ts in picked:
            video = catalog.get(vid)
            if video is None or not video.local_path or not Path(video.local_path).exists():
                continue
            try:
                [img] = frames_at(video.local_path, [ts])
            except Exception:
                continue
            # layout v2: keyframes live with their video's artifacts
            kf_dir = ws.video_dir(video.source_key, video.title, create=True) / "keyframes"
            kf_dir.mkdir(parents=True, exist_ok=True)
            path = kf_dir / f"{int(ts)}.png"
            img.save(path)
            out.append(Keyframe(video_id=vid, timestamp=ts, image=img, path=str(path)))
        return out
    finally:
        catalog.close()


def render_answer(answer: Answer, workdir: str) -> str:
    """Answer -> human-readable text with hyperlinked timestamps."""
    catalog = Catalog(Workspace(workdir).catalog_db)
    try:
        videos: dict[str, Optional[Video]] = {}

        def video_for(vid_str: str) -> Optional[Video]:
            if vid_str not in videos:
                try:
                    videos[vid_str] = catalog.get(UUID(vid_str))
                except ValueError:
                    videos[vid_str] = None
            return videos[vid_str]

        from va.adapters.reasoner.prompts import coerce_timestamp

        lines: List[str] = []
        items = answer.attributes.get("items") or []
        for item in items:
            statement = str(item.get("statement", "")).strip()
            if not statement:
                continue
            vid = item.get("video_id")
            ts = coerce_timestamp(item.get("timestamp"))
            if vid is not None and ts is not None:
                lines.append(f"- {statement} {_link(video_for(str(vid)), ts)}")
            else:
                lines.append(f"- {statement}")
        if answer.text:
            lines.append(answer.text if not lines else f"\n{answer.text}")
        if not lines:
            lines.append("(no answer produced)")
        return "\n".join(lines)
    finally:
        catalog.close()


def ask(
    question: str, workdir: str = ".va", k: int = 5, max_keyframes: int = 4
) -> AskResult:
    with traced_run("ask", workdir):
        trace("ask", "question", question)
        reasoner = get_reasoner()

        plan = reasoner.plan(question)              # Role 11, call #1
        if not plan.query:
            plan.query = question
        trace("planner", "plan", _plan_summary(plan),
              tiers=_active_tiers(plan), search_terms=plan.search_terms,
              params=plan.params or {})

        # Deterministic escalation floor: deep-scan is the difference between a
        # counted answer and a guess, so its trigger must not depend on planner
        # quality (observed: qwen-7B planner missed it on a counting question the
        # rule heuristics catch). OR the rule trigger into any LLM plan.
        from va.adapters.reasoner.rule_inproc import RuleReasoner

        rule_plan = RuleReasoner().plan(question)
        if rule_plan.needs_deep_scan and not plan.needs_deep_scan:
            plan.needs_deep_scan = True
            plan.needs_vlm_reasoning = True
            if not (plan.params or {}).get("scan_target"):
                plan.params["scan_target"] = rule_plan.params.get("scan_target")
            trace("planner", "rule_floor", "rule heuristic forced deep_scan", level="warn",
                  scan_target=(plan.params or {}).get("scan_target"))

        evidence = retrieve(plan, workdir=workdir, k=k)          # SR.4: fused, ranked
        trace_ingest_links(workdir, {it.video_id for it in evidence.items})

        if plan.needs_deep_scan:                                 # Tier 5b
            _deep_scan_into(evidence, plan, workdir, reasoner)
            trace("deep_scan", "ran",
                  next((n for n in evidence.notes if "deep-scan" in n), "deep scan"))

        keyframes = _collect_keyframes(evidence, workdir, max_keyframes)
        trace("reasoner", "keyframes", f"{len(keyframes)} keyframes",
              moments=[round(kf.timestamp, 1) for kf in keyframes])

        _trace_reasoner_input(evidence, keyframes)
        answer = reasoner.reason(question, evidence, keyframes)  # Role 11, call #2
        trace("reasoner", "output", "answer produced",
              reasoner_output=answer.text, citations=len(answer.citations))

        # SELF-ESCALATION (architecture: progressive escalation; trigger #3): if no
        # deep scan ran and the sparse answer self-reports insufficiency (or comes
        # back uncited and empty), escalate ONCE and re-reason over the dense
        # evidence. A missed trigger becomes a slower right answer, not a wrong one.
        if not plan.needs_deep_scan and _answer_insufficient(answer):
            trace("reasoner", "self_escalation",
                  "sparse answer insufficient -> deep scan", level="warn")
            plan.needs_deep_scan = True
            if not (plan.params or {}).get("scan_target"):
                from va.adapters.reasoner.rule_inproc import RuleReasoner

                plan.params["scan_target"] = RuleReasoner().plan(question).params.get("scan_target")
            evidence.notes.append("self-escalation: sparse answer insufficient -> deep scan")
            _deep_scan_into(evidence, plan, workdir, reasoner)
            keyframes = _collect_keyframes(evidence, workdir, max_keyframes)
            _trace_reasoner_input(evidence, keyframes)
            answer = reasoner.reason(question, evidence, keyframes)
            trace("reasoner", "output", "answer (post-escalation)",
                  reasoner_output=answer.text, citations=len(answer.citations))

        rendered = render_answer(answer, workdir)

        # Deterministic facts are displayed deterministically: when a deep scan ran,
        # lead with its code-counted numbers — a weak narrator (observed: qwen-7B
        # said "10" while its own evidence said 19 distinct) can't hide them.
        ds = [i for i in evidence.items if i.modality == "deep_scan_count"]
        if ds and "CODE-COUNTED" not in rendered:
            rendered = f"[{ds[0].content}]\n\n{rendered}"
        trace("ask", "answer", (rendered or "").splitlines()[0][:120] if rendered else "")
        return AskResult(
            question=question, plan=plan, evidence=evidence,
            answer=answer, rendered=rendered,
        )


def _active_tiers(plan: QueryPlan) -> list[str]:
    """The tier flags the planner turned on (for the trace)."""
    return [f for f in QueryPlan.model_fields
            if f.startswith("needs_") and getattr(plan, f, False)]


def _plan_summary(plan: QueryPlan) -> str:
    tiers = [t.replace("needs_", "") for t in _active_tiers(plan)]
    return f"tiers: {', '.join(tiers) or 'visual only'}"


def _trace_reasoner_input(evidence: Evidence, keyframes: List[Keyframe]) -> None:
    """Record the VERBATIM text + keyframe list handed to the reasoner — the same
    render the reasoner adapters use, so the trace shows exactly what it saw."""
    from va.adapters.reasoner.prompts import render_evidence

    trace("reasoner", "input", f"{len(evidence.items)} evidence items + "
          f"{len(keyframes)} keyframes -> reasoner",
          reasoner_input=render_evidence(evidence),
          keyframes=[kf.path for kf in keyframes])
