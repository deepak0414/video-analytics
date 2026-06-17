"""Deep-scan escalation (Tier 5b) — exhaustive sweep + CODE-side counting.

For counting-events/changes-over-time questions (architecture doc, "Deep-Scan
Escalation"): sample frames across the target video on a budget, ask the VLM a
micro-question per frame ("one phrase: what outfit is she wearing?"), then
count state changes deterministically over the observation timeline. The LLM
describes frames (what it's good at); Python does the arithmetic (what the LLM
is bad at). Observations are cached in the DB, so repeat questions are free.

Counting semantics:
- consecutive identical (normalized) observations merge into RUNS;
- `changes_high` = transitions between raw runs (every description change);
- `changes_low`  = transitions after merging ADJACENT similar runs (token-
  Jaccard >= 0.6) — absorbs the VLM describing the same state two ways.
The honest answer is the bounded count [low, high].
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple
from uuid import UUID

from va.contracts.evidence import Evidence, EvidenceItem

MODALITY_OBSERVATION = "observation"
MODALITY_DEEP_SCAN_COUNT = "deep_scan_count"

# v2: 2-5 words WITH a distinguishing style detail — color alone merges distinct
# items of the same color (measured: 6 different pink/white dresses collapsed).
# v3: hybrid sampling (intra-shot density added) — REGRESSED the montage case:
# 2s density inside 3-5s shots produced multiple differently-worded labels of
# the SAME dress (23 distinct vs the verified 18).
# v4: regime split — midpoint-only for shots <= 8s (montage: state can't change
# without a cut), interior samples every 4s ONLY in genuinely long takes.
# v5: cutoff raised 8s -> 15s. Audited v4 phantom: interior samples in a ~10s
# END-CARD shot caught the movie POSTER ("red satin dress" @153.1s — a dress on
# poster art, never worn). Edited-content shots (dialog, cards) run up to ~15s
# without state changes; fixed-camera takes run minutes — 15s splits the
# regimes with margin on both sides.
_PROMPT_VERSION = "v5"
_MICRO_PROMPT = (
    "Look at this single video frame. In 2-5 WORDS, name {target}, including its "
    "color AND one distinguishing detail (e.g. 'pink satin gown', 'yellow ruffled "
    "dress'). Use identical wording for the same item every time. "
    "If it is not visible in this frame, reply exactly: none"
)
DEFAULT_TARGET = "the main person's outfit"

# Observations meaning "subject not visible" — excluded from the timeline
# (an off-camera cut is not a state change).
_NONE = re.compile(r"^\s*(none|n/?a|other|not visible|no \w+)\s*\.?\s*$", re.I)

# Words that don't change the scan INTENT — ignored when building the cache key,
# so LLM-planner wording drift ("the girl's dress (color and style)" vs "dress
# the girl is wearing") maps to the same cached sweep.
_KEY_NOISE = {
    "the", "a", "an", "of", "in", "on", "is", "are", "its", "their", "and",
    "or", "main", "person", "persons", "current", "state", "wearing", "worn",
    "color", "colour", "style", "garment", "e", "g", "eg", "frame", "visible",
}


def canonical_key(scan_target: str) -> str:
    """Stable cache identity for a scan intent: sorted, de-pluralized content
    tokens. Punctuation splits tokens ("dress/outfit" -> two words) and trailing
    's' is stripped ("girl's"/"girls" -> "girl") so wording drift maps together."""
    raw = re.sub(r"[^a-z0-9]+", " ", scan_target.lower()).split()
    noise = {w.rstrip("s") for w in _KEY_NOISE}
    tokens = sorted({w.rstrip("s") for w in raw} - noise)
    return "-".join(t for t in tokens if t) or "default"


@dataclass
class Run:
    time_start: float
    time_end: float
    text: str


@dataclass
class DeepScanResult:
    video_id: UUID
    scan_target: str
    observations: List[Tuple[float, str]]
    runs: List[Run]
    changes_low: int
    changes_high: int
    distinct_states: int
    cached: bool
    # state -> number of SEPARATE appearances (contiguous runs). The statistic
    # for "how many visits/appearances": distinct counts kinds, transitions
    # count switches (≈2x visits), episodes count comings-and-goings.
    episodes: dict = field(default_factory=dict)
    evidence_items: List[EvidenceItem] = field(default_factory=list)


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def _tokens(s: str) -> set:
    return set(_normalize(s).split())


def _similar(a: str, b: str, threshold: float = 0.6) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold


def normalize_observations(
    observations: Sequence[Tuple[float, str]],
    scan_target: str,
    reasoner=None,
) -> Tuple[List[Tuple[float, str]], dict]:
    """Map raw frame labels to canonical state labels via ONE LLM call (semantics),
    so the counting (arithmetic) stays in code. Same-state synonyms merge; labels
    about a different subject (cut-aways) become OTHER; OTHER/NONE are dropped by
    analyze(). Without an LLM-capable reasoner: identity mapping (raw labels pass
    through) — the offline/stub path.
    """
    unique = sorted({text for _, text in observations})
    if not unique or reasoner is None:
        return list(observations), {}

    # duck-typed raw-text access: QwenReasoner._chat / ClaudeCliReasoner._call
    llm = getattr(reasoner, "_chat", None) or getattr(reasoner, "_call", None)
    if llm is None:
        return list(observations), {}

    from va.adapters.reasoner.prompts import NORMALIZE_PROMPT, parse_json_block

    # Compact raw timeline so the normalizer can SEE alternation patterns
    # (same moving subject relabeled per angle) instead of judging labels blind.
    raw_runs: List[Tuple[float, float, str]] = []
    for ts, text in observations:
        if raw_runs and _normalize(text) == _normalize(raw_runs[-1][2]):
            raw_runs[-1] = (raw_runs[-1][0], ts, raw_runs[-1][2])
        else:
            raw_runs.append((ts, ts, text))
    timeline = "\n".join(f"- {t0:.0f}-{t1:.0f}s: {text}" for t0, t1, text in raw_runs[:80])

    prompt = NORMALIZE_PROMPT.format(
        subject=scan_target,
        labels="\n".join(f"- {u}" for u in unique),
        timeline=timeline,
    )
    try:
        doc = parse_json_block(llm(prompt))
        mapping = doc.get("mapping", {}) if doc else {}
    except Exception:
        mapping = {}
    if not mapping:
        return list(observations), {}
    return [(ts, mapping.get(text, text)) for ts, text in observations], mapping


def analyze(observations: Sequence[Tuple[float, str]]) -> Tuple[List[Run], int, int, int]:
    """Observation timeline -> (runs, changes_low, changes_high, distinct_states).
    Pure code — deterministic for a given timeline. 'none' observations (subject
    off-camera) are dropped: a cut-away is not a state change."""
    observations = [(ts, t) for ts, t in observations if not _NONE.match(t)]
    runs: List[Run] = []
    for ts, text in observations:
        if runs and _normalize(text) == _normalize(runs[-1].text):
            runs[-1].time_end = ts
        else:
            runs.append(Run(time_start=ts, time_end=ts, text=text))
    changes_high = max(0, len(runs) - 1)

    merged: List[Run] = []
    for r in runs:
        if merged and _similar(r.text, merged[-1].text):
            merged[-1].time_end = r.time_end
        else:
            merged.append(Run(r.time_start, r.time_end, r.text))

    # Temporal debounce: a SINGLE-sample run sandwiched between two runs of the
    # same state is label flicker, not two real transitions (measured on a live
    # bird: "brown speckled"/"brown striped" alternated per sample, exploding 5
    # visits into 23). Iterate until stable.
    changed = True
    while changed:
        changed = False
        for i in range(1, len(merged) - 1):
            single = merged[i].time_start == merged[i].time_end
            if single and _normalize(merged[i - 1].text) == _normalize(merged[i + 1].text):
                merged[i - 1].time_end = merged[i + 1].time_end
                del merged[i:i + 2]
                changed = True
                break

    changes_low = max(0, len(merged) - 1)
    distinct = len({_normalize(r.text) for r in merged})
    # return the MERGED runs (adjacent same-state noise absorbed) — they are
    # what downstream displays and counts episodes from
    return merged, changes_low, changes_high, distinct


# Regime split (v4): a shot this short cannot contain a state change without a
# cut -> single midpoint sample (extra frames of the same state only add label
# noise — measured +5 phantom dresses). Longer takes (fixed cameras) get
# interior samples so in-shot events are observed.
_MIDPOINT_MAX_SHOT = 15.0  # shots <= this: midpoint only (covers edited content)
_LONG_SHOT_STRIDE = 4.0    # sampling stride inside longer takes (seconds)


def _sample_timestamps(
    video_id: UUID, local_path: str, workdir: str, max_frames: int, fps: float
) -> List[float]:
    """HYBRID sampling — correct for both content types:

    - edited/montage video: one frame per Role-1 segment midpoint, so no shot is
      skipped (a fixed stride missed 0.7s shots = missed dresses);
    - long-take/unedited video (fixed camera — nature cams, CCTV): midpoints
      alone collapse a one-shot video to ONE frame, so shots longer than
      _INTRA_SHOT_STRIDE also get samples every ~2s WITHIN the shot (events
      happen inside static scenes — the "reverse coalescing" trap).

    Falls back to a fixed stride when no segments exist. Budget-capped.
    """
    from va.media.frames import probe
    from va.pipeline.paths import Workspace
    from va.storage.structured.segments import SegmentStore

    seg_store = SegmentStore(Workspace(workdir).catalog_db)
    try:
        segments = seg_store.get_segments(video_id)
    finally:
        seg_store.close()

    if segments:
        stamps: List[float] = []
        for s in segments:
            duration = s.end_time - s.start_time
            if duration <= _MIDPOINT_MAX_SHOT:
                stamps.append(round((s.start_time + s.end_time) / 2.0, 2))
            else:
                t = s.start_time + _LONG_SHOT_STRIDE / 2.0
                while t < s.end_time:
                    stamps.append(round(t, 2))
                    t += _LONG_SHOT_STRIDE
        stamps = sorted(set(stamps))
        if len(stamps) > max_frames:  # budget: evenly subsample
            step = len(stamps) / max_frames
            stamps = [stamps[int(i * step)] for i in range(max_frames)]
        return stamps

    duration = probe(local_path).duration_seconds or 0.0
    stride = max(1.0 / fps, duration / max_frames if duration else 1.0)
    return [round(t * stride, 2) for t in range(int(duration / stride) + 1)]


def deep_scan_video(
    video_id: UUID,
    local_path: str,
    scan_target: str,
    workdir: str = ".va",
    max_frames: int = 120,
    fps: float = 1.0,
    reasoner=None,
) -> DeepScanResult:
    """Sweep one video (cached), analyze, and package evidence items."""
    from va.media.frames import frames_at
    from va.pipeline.paths import Workspace
    from va.registry import get_vlm_captioner
    from va.storage.structured.observations import ObservationStore

    prompt = _MICRO_PROMPT.format(target=scan_target)
    # Key on the canonical INTENT (not prompt wording) + template version, so
    # planner phrasing can't bust the cache but prompt upgrades invalidate it.
    prompt_key = hashlib.sha1(
        f"{_PROMPT_VERSION}|{canonical_key(scan_target)}|{max_frames}|{fps}".encode()
    ).hexdigest()[:16]

    store = ObservationStore(Workspace(workdir).catalog_db)
    try:
        # rows are only written after a COMPLETED sweep, so any rows = valid cache
        observations = store.load(video_id, prompt_key)
        cached = len(observations) > 0
        if not cached:
            timestamps = _sample_timestamps(video_id, local_path, workdir, max_frames, fps)
            captioner = get_vlm_captioner()
            observations = []
            for ts in timestamps:
                try:
                    [img] = frames_at(local_path, [ts])
                    observations.append((ts, captioner.caption([img], prompt=prompt).strip()))
                except Exception:
                    continue  # unreadable frame: skip, keep sweeping
            store.replace(video_id, prompt_key, observations)
    finally:
        store.close()

    # Semantics (label merging, off-subject filtering) via one LLM call;
    # arithmetic stays in code. The mapping is CACHED alongside the observations
    # so repeat asks are fully deterministic (no fresh LLM merge decisions).
    import json as _json

    # ":norm2" = normalization-prompt version (timeline context added) — bump
    # whenever NORMALIZE_PROMPT changes so stale mappings don't survive.
    map_key = f"{prompt_key}:norm2"
    store = ObservationStore(Workspace(workdir).catalog_db)
    try:
        cached_map = store.load(video_id, map_key)
        if cached_map:
            mapping = _json.loads(cached_map[0][1])
            canonical = [(ts, mapping.get(text, text)) for ts, text in observations]
        else:
            canonical, mapping = normalize_observations(observations, scan_target, reasoner)
            if mapping:
                store.replace(video_id, map_key, [(0.0, _json.dumps(mapping))])
    finally:
        store.close()
    runs, low, high, distinct = analyze(canonical)

    # Episodes: separate contiguous appearances per state — the statistic for
    # "how many visits/appearances" (distinct counts kinds; transitions count
    # switches ≈ 2x visits for appear/disappear subjects).
    from collections import Counter

    episodes = dict(Counter(_normalize(r.text) for r in runs))
    total_episodes = len(runs)
    ep_summary = ", ".join(f"'{s}': {n}" for s, n in
                           sorted(episodes.items(), key=lambda kv: -kv[1])[:8])

    items: List[EvidenceItem] = [
        EvidenceItem(
            modality=MODALITY_DEEP_SCAN_COUNT, video_id=video_id,
            time_start=observations[0][0] if observations else 0.0,
            time_end=observations[-1][0] if observations else 0.0,
            content=(
                f"CODE-COUNTED from an exhaustive scan of {len(observations)} frames of "
                f"'{scan_target}': {distinct} DISTINCT states; {low}-{high} timeline "
                f"transitions; {total_episodes} appearance EPISODES total "
                f"(per state: {ep_summary}). Pick the statistic matching the question: "
                f"kinds -> distinct; changes on edited footage -> distinct (transitions "
                f"overcount montage intercutting); visits/appearances -> episodes."
            ),
            score=1.0, source_role=11,
            attributes={
                "changes_low": low, "changes_high": high,
                "distinct_states": distinct, "frames_scanned": len(observations),
                "episodes": episodes, "total_episodes": total_episodes,
                "cached": cached, "normalized": bool(mapping),
                "label_mapping": dict(list(mapping.items())[:60]),  # auditable
            },
        )
    ]
    for r in runs[:40]:
        items.append(EvidenceItem(
            modality=MODALITY_OBSERVATION, video_id=video_id,
            time_start=r.time_start, time_end=r.time_end,
            content=r.text, score=0.9, source_role=11,
        ))

    return DeepScanResult(
        video_id=video_id, scan_target=scan_target, observations=observations,
        runs=runs, changes_low=low, changes_high=high, distinct_states=distinct,
        cached=cached, episodes=episodes, evidence_items=items,
    )


def run_deep_scan(
    evidence: Evidence, plan, workdir: str, reasoner=None
) -> Optional[DeepScanResult]:
    """Pick the target video (majority vote over evidence; else the only video
    in the catalog) and sweep it."""
    from collections import Counter

    from va.pipeline.paths import Workspace
    from va.storage.structured.catalog_sqlite import Catalog

    counts = Counter(i.video_id for i in evidence.items if i.video_id is not None)
    catalog = Catalog(Workspace(workdir).catalog_db)
    try:
        if counts:
            video = catalog.get(counts.most_common(1)[0][0])
        elif (evidence.attributes or {}).get("primary_video_id"):
            # The relevance gate (SR.5) may have emptied evidence.items; fall back
            # to the pre-gate dominant video the retriever stashed for us.
            video = catalog.get(UUID(evidence.attributes["primary_video_id"]))
        else:
            videos = catalog.list(limit=2)
            video = videos[0] if len(videos) == 1 else None
        if video is None or not video.local_path:
            return None
        target = (plan.params or {}).get("scan_target") or DEFAULT_TARGET
        return deep_scan_video(
            video.id, video.local_path, target, workdir=workdir, reasoner=reasoner
        )
    finally:
        catalog.close()
