"""Typed aggregation ops over Roles 5/6 rows (typed-query-tier-plan.md).

This module holds the deterministic aggregation tier: a small set of general,
composable operations (count/list/histogram) plus the two RESOLVE-SEAMS that
Role 12 will fill later:

- `resolve_category()` — the classification/taxonomy axis ("what kind of
  thing"). Ships as a pure STRUCTURAL stub: the same plural-strip logic the
  object query path has always used, promoted to a named seam that also
  returns a provenance source string. NO domain content (no synonym tables —
  "vehicle" does not expand to {car, truck}); that expansion is a flagged,
  human-gated decision (plan §5.1, loop item TQ1.b2) or the Role-12 taxonomy
  registry.
- `resolve_identities()` — the instance-dedup/ReID axis ("same physical
  thing?"), to land as a stub in a later item (loop TQ1.d).

The seam contract that makes Role 12 a stub-swap: signatures stay fixed; only
the bodies and the provenance strings change.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from va.contracts.aggregate import (
    Bucket, CountResult, EventRow, ResolutionProvenance, TimeWindow,
)
from va.contracts.evidence import (
    MODALITY_AGGREGATE_COUNT, MODALITY_OBJECT_COUNT, EvidenceItem,
)
from va.pipeline.paths import Workspace
from va.storage.structured.catalog_sqlite import Catalog
from va.storage.structured.tracks import PlacedTrack, TrackStore

# Provenance value for the stub body — flips to "taxonomy-registry" when the
# Role-12 category registry replaces the plural-strip logic (plan §5.1).
CATEGORY_SOURCE_PLURAL_STRIP = "plural-strip"


def resolve_category(category: str) -> Tuple[List[str], str]:
    """Expand a category name into candidate detector-class names.

    Returns `(categories, source)` where `source` names the resolution that
    actually ran, for `ResolutionProvenance.category_source`.

    Stub semantics (pure STRUCTURE, deliberately content-free): split into
    lowercase word tokens and include each word's plural-stripped form, so
    "cars" matches the detector class "car" (observed: plural query words
    silently produced ZERO object evidence). This is exactly the logic
    `pipeline.objects._classes` has always applied — promoted to a named seam,
    not changed. It cannot map synonyms or hypernyms ("vehicle" stays
    "vehicle" and matches nothing under a car/person/truck/dog vocabulary);
    that requires the Role-12 taxonomy registry.
    """
    words = re.findall(r"[a-z0-9']+", category.lower())
    out: List[str] = []
    for w in words:
        for candidate in (w, w.rstrip("s")):
            if candidate and candidate not in out:
                out.append(candidate)
    return out, CATEGORY_SOURCE_PLURAL_STRIP


def select_tracks(
    categories: Sequence[str], window: TimeWindow, workdir: str = ".va",
    cameras: Optional[Sequence[str]] = None,
) -> List[PlacedTrack]:
    """Tracks of the given classes whose absolute start falls inside `window`.

    The wall-clock -> UTC conversion happens ONCE, in Python, via
    `TimeWindow.epoch_bounds()` (tz-mandatory, DST-aware); the SQL then
    compares number-to-number. Placement follows the repo's single translation
    rule (`pipeline/timeline.py::absolute_time`): absolute = `videos.
    start_epoch` + the stored video-relative seconds — inlined in SQL because
    membership here is per-track, not the range->chunk mapping
    `wallclock_to_chunks` provides. Videos without `start_epoch` (A-EV) are
    skipped by construction; the window is half-open [start, end) so adjacent
    windows partition tracks without double counting.
    """
    t0, t1 = window.epoch_bounds()
    store = TrackStore(Workspace(workdir).catalog_db)
    try:
        return store.select_placed(categories, t0, t1, cameras=cameras)
    finally:
        store.close()


# --- the identity resolve-seam (plan §5.2) -----------------------------------

DEDUP_MODE_RAW = "raw"
DEDUP_MODE_INSTANCE = "instance"
# Provenance value for the stub body — flips to "cross-window ReID" when the
# Role-12 appearance-clustering body replaces the pass-through.
DEDUP_SOURCE_PER_WINDOW_TRACKS = "per-window tracks"

CAVEAT_NO_REID = (
    "dedup='instance' was requested, but cross-window/camera re-identification "
    "(Role 12) is not yet available — fell back to raw per-window tracks: the "
    "same physical object re-appearing in another window or camera is counted "
    "again"
)


@dataclass(frozen=True)
class Entity:
    """One counted physical entity.

    Stub semantics: exactly one track per entity. When the Role-12 ReID body
    lands, `tracks` holds every track clustered into the entity — the shape
    already carries that future without a change.
    """
    category: str                    # the track's object_class
    camera: Optional[str]
    first_seen_epoch: float          # UTC epoch seconds
    last_seen_epoch: float
    tracks: Tuple[PlacedTrack, ...]


@dataclass(frozen=True)
class IdentityResolution:
    """resolve_identities output: entities + the provenance of HOW they were
    deduplicated (feeds `ResolutionProvenance.dedup_mode`/`dedup_source`) and
    any honesty caveats. `dedup_mode` records what actually RAN — an
    "instance" request that fell back reports "raw" plus the caveat, never a
    dedup that didn't happen."""
    entities: List[Entity]
    dedup_mode: str
    dedup_source: str
    caveats: List[str]


def resolve_identities(
    tracks: Sequence[PlacedTrack], mode: str = DEDUP_MODE_RAW,
    min_frames: int = 2,
) -> IdentityResolution:
    """Collapse track instances into counted entities (plan §5.2 seam, stub).

    `mode="raw"`: one track = one entity, after the `min_frames` flicker
    filter (frame_count >= min_frames — the same NAMED heuristic
    `TrackStore.distinct_counts` has always applied; single-frame tracks are
    usually detector flicker). `mode="instance"` is ACCEPTED but falls back to
    raw with a caveat until the Role-12 ReID body lands (the substrate —
    `object_tracks.appearance_ref` -> `appearance.npz` — already exists).

    The seam contract that makes Role 12 a stub-swap: this signature is
    stable; only the body, `dedup_mode`, and `dedup_source` change.
    """
    if mode not in (DEDUP_MODE_RAW, DEDUP_MODE_INSTANCE):
        raise ValueError(f"unknown dedup mode {mode!r} — expected "
                         f"'{DEDUP_MODE_RAW}' or '{DEDUP_MODE_INSTANCE}'")
    caveats: List[str] = []
    if mode == DEDUP_MODE_INSTANCE:
        caveats.append(CAVEAT_NO_REID)
    entities = [
        Entity(category=p.track.object_class, camera=p.camera,
               first_seen_epoch=p.first_seen_epoch,
               last_seen_epoch=p.last_seen_epoch, tracks=(p,))
        for p in tracks if p.track.frame_count >= min_frames
    ]
    return IdentityResolution(entities=entities, dedup_mode=DEDUP_MODE_RAW,
                              dedup_source=DEDUP_SOURCE_PER_WINDOW_TRACKS,
                              caveats=caveats)


# --- the windowed count op (plan §3, §5 pseudocode) ---------------------------

# per_camera key for entities on a video with no camera link (shouldn't occur
# on NVR footage, where every chunk carries `nvr-ch<n>`; a STRUCTURAL label,
# not domain content).
NO_CAMERA_KEY = "(no camera)"

# Standing honesty caveats — every count carries them (plan §11: a number never
# travels without its method).
CAVEAT_RAW_UPPER_BOUND = (
    "raw per-window tracks: no cross-window/camera re-identification, so the "
    "same physical object re-appearing in another window or camera is counted "
    "again — treat the total as an UPPER BOUND on distinct objects"
)
CAVEAT_PARKED = (
    "includes stationary/parked objects: a counted track means 'tracked in "
    "frame', not 'crossed' or 'passed through' — distinguishing those needs "
    "motion analysis this tier does not do"
)
CAVEAT_START_MEMBERSHIP = (
    "window membership is by track START: an object already being tracked "
    "when the window opened is not counted in this window"
)
# A workdir with NO wall-clock-anchored videos cannot window-count anything —
# its 0 means "nothing is anchorable", never "zero objects" (batch review:
# the confident-false-zero class on a pure A-EV workdir).
CAVEAT_NOT_WINDOWABLE = (
    "NOT APPLICABLE to this workdir: no done video carries a wall-clock "
    "anchor (start_epoch is NULL everywhere — standalone/edited-video "
    "ingests such as YouTube or local files), so a windowed count can see "
    "NONE of the stored tracks. This total means 'nothing is anchored to "
    "the clock', NOT 'zero objects' — use the whole-corpus `va count` "
    "instead"
)


def _unplaced_exclusion_caveat(n: int, category: str) -> str:
    return (f"{n} matched '{category}' track(s) sit on videos with no "
            f"wall-clock anchor (start_epoch NULL) and are EXCLUDED from "
            f"this windowed count — windowed totals cover only "
            f"wall-clock-anchored (e.g. NVR) footage")


def _mixed_workdir_caveat(workdir: str) -> Optional[str]:
    """The CLAUDE.md mixed-workdir hazard, surfaced instead of silenced: if the
    catalog's DONE videos span more than one footage domain, say so."""
    cat = Catalog(Workspace(workdir).catalog_db)
    try:
        domains = cat.footage_domains()
    finally:
        cat.close()
    if len(domains) <= 1:
        return None
    return (f"mixed-footage workdir ({len(domains)} footage domains present): "
            f"this count spans footage domains whose rows are not comparable; "
            f"keep long-static-scene (A-LSSRVF) chunks in their own workdir")


def _entity_evidence(e: Entity, zone: ZoneInfo) -> EvidenceItem:
    """One manifest row per counted entity — the backing track, wall-clock
    placed, so follow-ups ('those 5 cars') can resolve against real refs."""
    track = e.tracks[0].track
    start_local = datetime.fromtimestamp(e.first_seen_epoch, zone).isoformat()
    where = e.camera if e.camera is not None else NO_CAMERA_KEY
    return EvidenceItem(
        modality=MODALITY_OBJECT_COUNT, video_id=track.video_id,
        time_start=track.first_seen, time_end=track.last_seen,
        content=(f"'{e.category}' track on {where} from {start_local} "
                 f"({track.frame_count} frames)"),
        score=1.0, source_role=6,
        attributes={
            "object_class": e.category, "camera": e.camera,
            "track_id": str(track.id),
            "first_seen_epoch": e.first_seen_epoch,
            "last_seen_epoch": e.last_seen_epoch,
            "frame_count": track.frame_count,
        },
    )


def count_objects(
    category: str, window: TimeWindow, workdir: str = ".va",
    cameras: Optional[Sequence[str]] = None, dedup: str = DEDUP_MODE_RAW,
    min_frames: int = 2,
) -> CountResult:
    """Windowed, per-camera, tz-correct object count with provenance + caveats.

    The plan-§5 composition, exactly: `resolve_category` (what kind) ->
    `select_tracks` (windowed SQL) -> `resolve_identities` (same instance?) ->
    a `CountResult` that echoes the window, disclose what resolution actually
    ran, and carries one `EvidenceItem` per counted entity as the manifest.

    `min_frames=2` is the NAMED flicker-filter heuristic shared with
    `TrackStore.distinct_counts` (single-frame tracks are usually detector
    flicker) — structure, not content, and overridable per call.

    NB this is the WINDOWED counterpart of `pipeline.objects.count_objects`
    (whole-corpus, per-class); same name by design — the plan's op vocabulary —
    different module.
    """
    ident, categories, cat_source = _entities_for(
        category, window, workdir, cameras, dedup, min_frames)

    per_camera: dict[str, int] = {}
    for e in ident.entities:
        key = e.camera if e.camera is not None else NO_CAMERA_KEY
        per_camera[key] = per_camera.get(key, 0) + 1

    # Anchoring disclosure (plan §4/§11): what this windowed count could NOT
    # see. A workdir with zero anchored videos gets the not-applicable caveat
    # FIRST; matched tracks on un-anchored videos are named and counted.
    store = TrackStore(Workspace(workdir).catalog_db)
    try:
        anchoring = store.window_anchoring(categories, min_frames=min_frames)
    finally:
        store.close()

    caveats = []
    if anchoring.placed_videos == 0:
        caveats.append(CAVEAT_NOT_WINDOWABLE)
    if anchoring.unplaced_tracks > 0:
        caveats.append(_unplaced_exclusion_caveat(anchoring.unplaced_tracks,
                                                  category))
    caveats += [CAVEAT_RAW_UPPER_BOUND, CAVEAT_PARKED, CAVEAT_START_MEMBERSHIP]
    caveats.extend(ident.caveats)
    mixed = _mixed_workdir_caveat(workdir)
    if mixed is not None:
        caveats.append(mixed)

    zone = ZoneInfo(window.tz)
    return CountResult(
        total=len(ident.entities),
        per_camera=per_camera,
        window=window,
        resolution=ResolutionProvenance(
            categories_matched=categories, category_source=cat_source,
            dedup_mode=ident.dedup_mode, dedup_source=ident.dedup_source),
        caveats=caveats,
        evidence=[_entity_evidence(e, zone) for e in ident.entities],
        attributes={"window_anchoring": {
            "placed_videos": anchoring.placed_videos,
            "unplaced_matching_tracks": anchoring.unplaced_tracks,
        }},
    )


# --- the rows behind a count + the trend view (plan §3) -----------------------

def _entities_for(
    category: str, window: TimeWindow, workdir: str,
    cameras: Optional[Sequence[str]], dedup: str, min_frames: int,
):
    """The ONE selection path every op shares (plan §6: one placement rule —
    never a second membership computation that could disagree with a count).
    Returns (IdentityResolution, categories, category_source)."""
    categories, source = resolve_category(category)
    placed = select_tracks(categories, window, workdir=workdir, cameras=cameras)
    return (resolve_identities(placed, mode=dedup, min_frames=min_frames),
            categories, source)


def list_events(
    category: str, window: TimeWindow, workdir: str = ".va",
    cameras: Optional[Sequence[str]] = None, limit: int = 100,
    dedup: str = DEDUP_MODE_RAW, min_frames: int = 2,
) -> List[EventRow]:
    """One row per counted entity — the rows BEHIND `count_objects` for the
    same arguments (same selection path, so the two can never disagree),
    ordered by absolute start, capped at `limit`."""
    ident, _categories, _source = _entities_for(
        category, window, workdir, cameras, dedup, min_frames)
    rows: List[EventRow] = []
    for e in ident.entities[: max(0, limit)]:
        track = e.tracks[0].track
        rows.append(EventRow(
            video_id=track.video_id, track_id=track.id, category=e.category,
            camera=e.camera, first_seen_epoch=e.first_seen_epoch,
            last_seen_epoch=e.last_seen_epoch, frames=track.frame_count,
        ))
    return rows


# Default bucket width — a NAMED heuristic (structure, not content): hourly is
# the natural grain for a day narrative. Overridable per call.
DEFAULT_HISTOGRAM_BUCKET = "1h"
# Guard against a degenerate bucket/window pair allocating unbounded output
# (e.g. bucket="1s" over a month). Structural budget, flagged at review.
MAX_HISTOGRAM_BUCKETS = 10_000

_BUCKET_RE = re.compile(r"^(\d+)\s*([smhd])$")
_BUCKET_UNIT_S = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _bucket_seconds(bucket: str) -> float:
    m = _BUCKET_RE.match(bucket.strip().lower())
    if not m or int(m.group(1)) == 0:
        raise ValueError(f"invalid histogram bucket {bucket!r} — expected "
                         f"'<positive int><s|m|h|d>', e.g. '1h', '30m'")
    return float(int(m.group(1)) * _BUCKET_UNIT_S[m.group(2)])


def timeline_histogram(
    category: str, window: TimeWindow, workdir: str = ".va",
    bucket: str = DEFAULT_HISTOGRAM_BUCKET,
    cameras: Optional[Sequence[str]] = None,
    dedup: str = DEDUP_MODE_RAW, min_frames: int = 2,
) -> List[Bucket]:
    """Entity counts per fixed-width bucket across the window ("when were X
    seen") — every bucket in the window is emitted, zeros included, so the
    result charts directly.

    Buckets are FIXED SPANS of absolute seconds aligned to the window start
    (the last one may extend past the window's end; membership still respects
    the window). A '1d' bucket is 24 clock-hours, NOT a calendar day — across
    a DST transition the two differ by an hour. Membership is the entity's
    absolute START (the same single placement rule as `count_objects` — the
    bucket counts sum exactly to that count for the same arguments).
    """
    width = _bucket_seconds(bucket)
    t0, t1 = window.epoch_bounds()
    # True float ceiling — the integer idiom ((span + width - 1) // width)
    # under-allocates on fractional-second spans (e.g. a 10.5 s window at 10 s
    # buckets needs 2, the idiom gives 1) and a valid in-window entity would
    # then index past the list.
    n_buckets = math.ceil((t1 - t0) / width) if t1 > t0 else 0
    if n_buckets > MAX_HISTOGRAM_BUCKETS:
        raise ValueError(
            f"bucket {bucket!r} over this window would produce {n_buckets} "
            f"buckets (cap {MAX_HISTOGRAM_BUCKETS}) — widen the bucket or "
            f"narrow the window")
    counts = [0] * n_buckets
    ident, _categories, _source = _entities_for(
        category, window, workdir, cameras, dedup, min_frames)
    for e in ident.entities:
        counts[int((e.first_seen_epoch - t0) // width)] += 1
    return [
        Bucket(bucket_start_epoch=t0 + i * width, count=counts[i],
               attributes={"width_seconds": width})
        for i in range(n_buckets)
    ]


# --- the planner tool registry + dispatch (plan §7) ---------------------------
#
# Each op is a JSON-Schema-described tool the Role-11 planner can select and
# fill (the standard tool-use pattern). The planner NEVER authors SQL — it only
# names an op and its arguments; code validates and executes. New op = new
# registry entry, no planner code change. The planner prompt renders itself
# from this registry (adapters/reasoner/prompts.py), so schema and prompt
# cannot drift.

_WINDOW_PARAMS = {
    "category": {"type": "string",
                 "description": "object category to count, e.g. 'car'"},
    "start": {"type": "string",
              "description": "window start, ISO 8601 wall-clock, e.g. 2026-08-11T00:00"},
    "end": {"type": "string", "description": "window end (exclusive), same format"},
    "tz": {"type": "string",
           "description": "IANA timezone the window is expressed in, e.g. "
                          "America/Los_Angeles — REQUIRED (a count with no "
                          "timezone is ambiguous)"},
    "cameras": {"type": "array", "items": {"type": "string"},
                "description": "optional camera ids, e.g. ['nvr-ch2']; omit for all"},
    "dedup": {"type": "string", "enum": ["raw", "instance"],
              "description": "optional; 'instance' falls back to raw with a "
                             "caveat until ReID exists"},
    "min_frames": {"type": "integer",
                   "description": "optional flicker filter (default 2)"},
}
_WINDOW_REQUIRED = ["category", "start", "end", "tz"]

AGGREGATION_TOOLS: dict[str, dict] = {
    "count_objects": {
        "description": "How many distinct object tracks in an explicit "
                       "wall-clock window, split per camera.",
        "parameters": {"type": "object", "properties": dict(_WINDOW_PARAMS),
                       "required": list(_WINDOW_REQUIRED)},
    },
    "list_events": {
        "description": "The individual tracks behind such a count, wall-clock "
                       "placed (one row per track).",
        "parameters": {"type": "object",
                       "properties": {**_WINDOW_PARAMS,
                                      "limit": {"type": "integer",
                                                "description": "max rows (default 100)"}},
                       "required": list(_WINDOW_REQUIRED)},
    },
    "timeline_histogram": {
        "description": "Track counts per time bucket across the window "
                       "(when were they seen).",
        "parameters": {"type": "object",
                       "properties": {**_WINDOW_PARAMS,
                                      "bucket": {"type": "string",
                                                 "description": "bucket width "
                                                 "<int><s|m|h|d> (default 1h)"}},
                       "required": list(_WINDOW_REQUIRED)},
    },
}


def _degrade(reason: str) -> Tuple[List[EvidenceItem], List[str]]:
    """The no-hallucination fallback: no items, one honest note. A missing or
    invalid argument must never become a guessed total (plan §7)."""
    return [], [f"aggregation requested but not run: {reason} — no count "
                f"computed (a typed count only ships with valid, explicit "
                f"arguments)"]


def dispatch_aggregation(
    plan_params: dict, workdir: str = ".va",
) -> Tuple[List[EvidenceItem], List[str]]:
    """Execute the aggregation op a planner selected (params['aggregation']).

    Validates the arguments against the registry contract; every failure path
    degrades to a note (never a fabricated number). On success returns ONE
    summary EvidenceItem whose content is the verbatim CODE-COUNTED line the
    rendered answer must lead with (R11.a display discipline), with the full
    `CountResult` (or rows/buckets) in `attributes`.
    """
    args = (plan_params or {}).get("aggregation")
    if not isinstance(args, dict) or not args:
        return _degrade("params['aggregation'] is missing or not an object")
    op = args.get("op", "count_objects")
    if op not in AGGREGATION_TOOLS:
        return _degrade(f"unknown op {op!r} (available: "
                        f"{', '.join(sorted(AGGREGATION_TOOLS))})")
    missing = [p for p in _WINDOW_REQUIRED if not args.get(p)]
    if missing:
        return _degrade(f"missing required argument(s): {', '.join(missing)}")

    from datetime import datetime as _dt

    try:
        start = _dt.fromisoformat(str(args["start"]))
        end = _dt.fromisoformat(str(args["end"]))
    except ValueError as e:
        return _degrade(f"unparseable start/end ({e})")
    try:
        window = TimeWindow(start=start, end=end, tz=str(args["tz"]))
    except Exception as e:  # pydantic.ValidationError — bad/blank/unknown tz etc.
        return _degrade(f"invalid window ({e})")

    cameras = args.get("cameras", None)
    if cameras is not None:
        if not (isinstance(cameras, list)
                and all(isinstance(c, str) for c in cameras)):
            return _degrade(f"cameras must be a list of camera ids, got "
                            f"{cameras!r}")
        if len(cameras) == 0:
            # NOT 'all cameras' — an empty selection usually means a failed
            # camera-name resolution upstream, and treating it as unfiltered
            # would present the full total as the filtered answer (the same
            # falsy-guard hazard select_placed refuses).
            return _degrade("cameras=[] — an empty camera selection matches "
                            "nothing; omit 'cameras' entirely to mean all "
                            "cameras")
    dedup = args.get("dedup", DEDUP_MODE_RAW)
    if dedup not in (DEDUP_MODE_RAW, DEDUP_MODE_INSTANCE):
        return _degrade(f"unknown dedup mode {dedup!r}")
    try:
        min_frames = int(args.get("min_frames", 2))
    except (TypeError, ValueError):
        return _degrade(f"min_frames must be an integer, got "
                        f"{args.get('min_frames')!r}")
    try:
        limit = int(args.get("limit", 100))
    except (TypeError, ValueError):   # JSON null / list / prose from a planner
        return _degrade(f"limit must be an integer, got {args.get('limit')!r}")

    category = str(args["category"])
    zone = ZoneInfo(window.tz)
    t0, t1 = window.epoch_bounds()
    span = (f"{datetime.fromtimestamp(t0, zone).isoformat()} to "
            f"{datetime.fromtimestamp(t1, zone).isoformat()} [{window.tz}]")

    try:
        # The canonical count runs for EVERY op: its total is the untruncated
        # number the content must lead with, and its caveats (standing +
        # instance-fallback + mixed-workdir) travel as notes with every op —
        # a limit-capped row list or a bucket chart is still the same count.
        r = count_objects(category, window, workdir=workdir, cameras=cameras,
                          dedup=dedup, min_frames=min_frames)
        anchoring = r.attributes.get("window_anchoring", {})
        if anchoring.get("placed_videos") == 0:
            # A workdir where NOTHING is wall-clock-anchored cannot answer a
            # windowed question at all — shipping "CODE-COUNTED: 0" here is
            # the confident-false-zero the tier exists to prevent. Degrade to
            # the honest note instead of an item.
            n = anchoring.get("unplaced_matching_tracks", 0)
            return _degrade(
                "this workdir has no wall-clock-anchored videos (start_epoch "
                "is NULL on every done video — standalone/edited-video "
                "ingests), so a windowed count is not applicable"
                + (f"; {n} matched '{category}' track(s) exist but cannot be "
                   f"placed on the clock" if n else "")
                + " — use the whole-corpus object count (needs_object_query / "
                  "va count) for this footage")
        notes = [f"aggregation caveat: {c}" for c in r.caveats]
        if op == "count_objects":
            cams = ", ".join(f"{c} {n}" for c, n in sorted(
                r.per_camera.items(), key=lambda kv: (-kv[1], kv[0])))
            content = (f"CODE-COUNTED: {r.total} '{category}' track(s) from "
                       f"{span}"
                       + (f" — per camera: {cams}" if cams else "")
                       + " — raw upper bound: no cross-window/camera "
                         "re-identification, parked objects included, window "
                         "membership by track start")
            attributes = {"op": op, "total": r.total,
                          "per_camera": dict(r.per_camera),
                          "count_result": r.model_dump(mode="json")}
        elif op == "list_events":
            rows = list_events(category, window, workdir=workdir,
                               cameras=cameras, limit=limit, dedup=dedup,
                               min_frames=min_frames)
            shown = (f"all {len(rows)}" if len(rows) == r.total
                     else f"first {len(rows)} of {r.total}")
            content = (f"CODE-COUNTED: {r.total} '{category}' event(s) from "
                       f"{span} ({shown} row(s) in attributes; raw upper "
                       f"bound)")
            attributes = {"op": op, "total": r.total,
                          "rows_returned": len(rows),
                          "rows": [e.model_dump(mode="json") for e in rows]}
        else:  # timeline_histogram
            buckets = timeline_histogram(
                category, window, workdir=workdir,
                bucket=str(args.get("bucket", DEFAULT_HISTOGRAM_BUCKET)),
                cameras=cameras, dedup=dedup, min_frames=min_frames)
            content = (f"CODE-COUNTED: {r.total} '{category}' track(s) from "
                       f"{span} across {len(buckets)} bucket(s) (buckets in "
                       f"attributes; raw upper bound)")
            attributes = {"op": op, "total": r.total,
                          "buckets": [b.model_dump(mode="json")
                                      for b in buckets]}
        excluded = anchoring.get("unplaced_matching_tracks", 0)
        if excluded:
            # The exclusion must reach the CODE-COUNTED line itself, not only
            # the notes — the lead line is what the rendered answer shows.
            content += (f" — NB {excluded} matched track(s) on un-anchored "
                        f"(no start_epoch) videos are excluded")
        item = EvidenceItem(modality=MODALITY_AGGREGATE_COUNT, content=content,
                            score=1.0, source_role=6, attributes=attributes)
        return [item], notes
    except ValueError as e:   # e.g. bad bucket grammar / bucket-explosion guard
        return _degrade(str(e))
