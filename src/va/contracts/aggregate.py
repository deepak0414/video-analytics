"""Aggregation contracts — typed results for the deterministic query tier.

The typed-query tier (typed-query-tier-plan.md) answers counting/aggregation
questions ("how many cars on Aug 11 before noon") with CODE, not an LLM: the
planner fills validated parameters, SQL computes the number, and these models
carry the answer *together with how it was derived* (the anti-hallucination
spine — a count never ships without its method and caveats).

Schema-evolution rules match query_plan.py / evidence.py: defaults on every
field (except the deliberately-required `TimeWindow` core, see below),
extra="allow" so unknown fields parse AND round-trip, an `attributes` bag for
payload that doesn't warrant a schema change, and a `schema_version` stamp.

Two deliberate exceptions to "defaults everywhere", both load-bearing:

- `TimeWindow.start/.end` have no default — an aggregation window must be
  explicit (no implicit "all time"; the whole-corpus count already exists as
  `pipeline.objects.count_objects`).
- `TimeWindow.tz` has no default and rejects blank/unknown zones — a count with
  no timezone is ambiguous (measured on the same real window: 111 tracks local
  vs 147 UTC). Better no answer than a silently-wrong one.

Epoch discipline: `TimeWindow.epoch_bounds()` converts wall-clock to UTC epoch
seconds IN PYTHON, so SQL always compares number-to-number. Never build epoch
bounds with SQLite `strftime('%s', ...)` — it returns TEXT, and SQLite orders
every number below any text, so `numeric_expr >= strftime(...)` is silently
always-false (a false 0, the exact bug this tier exists to prevent).
"""
from __future__ import annotations

from datetime import datetime, timezone as _dt_timezone
from typing import Any, List, Literal, Optional
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from va.contracts.evidence import EvidenceItem

_UTC = _dt_timezone.utc

# How track instances are deduplicated into counted entities (plan §5.2).
# "raw": one track = one entity (today). "instance": cross-window/camera ReID —
# accepted since day one, but until Role 12 lands it FALLS BACK to raw plus a
# caveat, never silently pretends to dedup.
DedupMode = Literal["raw", "instance"]


class TimeWindow(BaseModel):
    """An explicit wall-clock window with a mandatory timezone.

    `start`/`end` may be naive (interpreted in `tz`) or timezone-aware (their
    own offset is respected; `tz` still governs presentation).
    """

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    start: datetime
    end: datetime
    tz: str
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tz")
    @classmethod
    def _tz_required_and_known(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("TimeWindow.tz is required — a count with no "
                             "timezone is ambiguous (e.g. 111 local vs 147 UTC "
                             "on the same window)")
        try:
            ZoneInfo(v.strip())
        except (ZoneInfoNotFoundError, ValueError, KeyError) as e:
            raise ValueError(f"TimeWindow.tz {v!r} is not a known IANA "
                             f"timezone: {e}")
        return v.strip()

    @model_validator(mode="after")
    def _ordered(self) -> "TimeWindow":
        # Diagnose DST spring-forward gaps FIRST: a naive wall time that does
        # not exist in tz (e.g. 02:30 on the US spring-forward day) resolves
        # via fold rules to an epoch that can make a forward-ordered window
        # look reversed — the caller deserves the real diagnosis, not
        # "end is before start".
        for name, dt in (("start", self.start), ("end", self.end)):
            if dt.tzinfo is None and self._is_nonexistent(dt):
                raise ValueError(
                    f"TimeWindow.{name} {dt.isoformat()} does not exist in "
                    f"{self.tz} (DST spring-forward gap — clocks skip that "
                    f"wall time)")
        s, e = self.epoch_bounds()
        if e < s:
            raise ValueError(f"TimeWindow end ({self.end}) is before start "
                             f"({self.start}) once resolved in tz={self.tz}")
        return self

    def _is_nonexistent(self, naive: datetime) -> bool:
        """True when a naive wall time falls in this tz's spring-forward gap.

        PEP-495 round-trip check: attaching the zone and normalizing through
        UTC lands a nonexistent wall time on a DIFFERENT wall time; real
        (including ambiguous fall-back) times round-trip exactly.
        """
        zone = ZoneInfo(self.tz)
        aware = naive.replace(tzinfo=zone)
        roundtrip = aware.astimezone(_UTC).astimezone(zone)
        return roundtrip.replace(tzinfo=None) != naive

    def _to_epoch(self, dt: datetime) -> float:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(self.tz))
        return dt.timestamp()

    def epoch_bounds(self) -> tuple[float, float]:
        """(start, end) as NUMERIC UTC epoch seconds, computed in Python.

        These bind into SQL as numbers, comparing correctly against the REAL
        `videos.start_epoch` / track-time columns. Never replace this with
        SQLite `strftime('%s', ...)` — that yields TEXT and the comparison
        becomes silently always-false (see module docstring).
        """
        return (self._to_epoch(self.start), self._to_epoch(self.end))


class ResolutionProvenance(BaseModel):
    """WHAT normalization/dedup actually ran for a count (plan §5).

    The two axes are independent seams: category (what kind of thing) and
    identity (same physical thing?). Until the Role-12 real bodies land the
    sources read "plural-strip" / "per-window tracks" — honest about being
    stubs — and flip to "taxonomy-registry" / "cross-window ReID" later with
    no shape change.
    """

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    categories_matched: List[str] = Field(default_factory=list)
    category_source: str = ""      # "plural-strip" (stub) | "taxonomy-registry" (Role 12)
    dedup_mode: str = ""           # "raw" | "instance"
    dedup_source: str = ""         # "per-window tracks" | "cross-window ReID"
    attributes: dict[str, Any] = Field(default_factory=dict)


class CountResult(BaseModel):
    """A windowed, per-camera object count plus how it was derived.

    `caveats` must always disclose what the count did NOT do (raw upper bound,
    no cross-window ReID, parked objects included, "crossed" != "present") —
    the number never travels without its method.
    """

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    total: int = 0
    per_camera: dict[str, int] = Field(default_factory=dict)   # {"nvr-ch2": 55, ...}
    window: Optional[TimeWindow] = None                        # echoed back, tz included
    resolution: ResolutionProvenance = Field(default_factory=ResolutionProvenance)
    caveats: List[str] = Field(default_factory=list)
    evidence: List[EvidenceItem] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)


class EventRow(BaseModel):
    """One aggregation-tier event: a single track placed on the wall clock.

    Times are UTC epoch seconds (`videos.start_epoch` + the track's relative
    seconds); presentation in the window's tz is the caller's job.
    """

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    video_id: Optional[UUID] = None
    track_id: Optional[UUID] = None
    category: str = ""
    camera: Optional[str] = None                 # e.g. "nvr-ch2"; None = uncameraed
    first_seen_epoch: Optional[float] = None     # UTC epoch seconds
    last_seen_epoch: Optional[float] = None
    frames: int = 0
    attributes: dict[str, Any] = Field(default_factory=dict)


class Bucket(BaseModel):
    """One histogram bucket: entity count in [bucket_start, bucket_start + width)."""

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    bucket_start_epoch: float = 0.0              # UTC epoch seconds
    count: int = 0
    attributes: dict[str, Any] = Field(default_factory=dict)
