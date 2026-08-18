"""Aggregation contracts (typed-query tier, TQ1.a).

The load-bearing assertions: a blank/missing/unknown tz is REJECTED (a count
with no timezone is ambiguous), epoch bounds are computed in Python as numbers
against hand-derived ground truth, and the evolution idiom holds (unknown extra
fields round-trip).
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest
from pydantic import ValidationError

from va.contracts.aggregate import (
    Bucket, CountResult, EventRow, ResolutionProvenance, TimeWindow,
)
from va.contracts.evidence import EvidenceItem

# Hand-derived ground truth (independent of zoneinfo):
#   2026-01-01T00:00Z = 1767225600  (2020-01-01 = 1577836800 + 6*365d + 2 leap
#   days [2020, 2024] = 2192 d * 86400)
#   Jan 1 -> Aug 11 = 31+28+31+30+31+30+31+10 = 222 days
#   2026-08-11T00:00Z = 1767225600 + 222*86400 = 1786406400
#   Aug 11 2026 is PDT (UTC-7), so 00:00 local = 07:00Z = 1786431600
#   and 12:00 local = 19:00Z = 1786474800.
AUG11_LOCAL_MIDNIGHT_EPOCH = 1786431600.0
AUG11_LOCAL_NOON_EPOCH = 1786474800.0


def _window(**over) -> TimeWindow:
    kw = dict(start=datetime(2026, 8, 11, 0, 0), end=datetime(2026, 8, 11, 12, 0),
              tz="America/Los_Angeles")
    kw.update(over)
    return TimeWindow(**kw)


# --- TimeWindow: tz is mandatory ---------------------------------------------

def test_missing_tz_rejected():
    with pytest.raises(ValidationError):
        TimeWindow(start=datetime(2026, 8, 11), end=datetime(2026, 8, 12))


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_blank_tz_rejected(bad):
    with pytest.raises(ValidationError):
        _window(tz=bad)


def test_unknown_tz_rejected():
    with pytest.raises(ValidationError):
        _window(tz="America/Not_A_City")


def test_tz_is_stripped():
    assert _window(tz="  America/Los_Angeles ").tz == "America/Los_Angeles"


# --- TimeWindow: epoch bounds are Python-computed numbers ---------------------

def test_epoch_bounds_naive_local_ground_truth():
    """Naive wall-clock in tz -> the hand-derived UTC epochs (DST-aware: PDT=-7)."""
    s, e = _window().epoch_bounds()
    assert s == AUG11_LOCAL_MIDNIGHT_EPOCH
    assert e == AUG11_LOCAL_NOON_EPOCH
    assert isinstance(s, float) and isinstance(e, float)  # numbers, never TEXT


def test_epoch_bounds_aware_datetimes_keep_their_offset():
    """An aware datetime's own offset wins; tz only governs presentation."""
    w = _window(start=datetime(2026, 8, 11, 7, 0, tzinfo=timezone.utc),
                end=datetime(2026, 8, 11, 19, 0, tzinfo=timezone.utc))
    assert w.epoch_bounds() == (AUG11_LOCAL_MIDNIGHT_EPOCH, AUG11_LOCAL_NOON_EPOCH)


def test_epoch_bounds_winter_offset_differs():
    """Jan 11 is PST (UTC-8): local midnight = 08:00Z. Pins DST-awareness.

    2026-01-11T00:00Z = 1767225600 + 10*86400 = 1768089600; +8h = 1768118400."""
    w = TimeWindow(start=datetime(2026, 1, 11, 0, 0), end=datetime(2026, 1, 11, 1, 0),
                   tz="America/Los_Angeles")
    assert w.epoch_bounds()[0] == 1768118400.0


def test_end_before_start_rejected():
    with pytest.raises(ValidationError):
        _window(start=datetime(2026, 8, 12), end=datetime(2026, 8, 11))


def test_dst_gap_start_gets_a_gap_diagnosis_not_a_reversed_window_error():
    """2026-03-08 02:30 America/Los_Angeles does not exist (spring forward).

    Under fold rules it resolves to a LATER epoch than a real 03:00 end, so
    without the gap check the error would be a baffling 'end is before start'.
    The validator must name the real cause."""
    with pytest.raises(ValidationError) as ei:
        TimeWindow(start=datetime(2026, 3, 8, 2, 30), end=datetime(2026, 3, 8, 3, 0),
                   tz="America/Los_Angeles")
    msg = str(ei.value)
    assert "does not exist" in msg and "spring-forward" in msg
    assert "before start" not in msg


def test_dst_gap_end_also_diagnosed():
    with pytest.raises(ValidationError) as ei:
        TimeWindow(start=datetime(2026, 3, 8, 1, 0), end=datetime(2026, 3, 8, 2, 30),
                   tz="America/Los_Angeles")
    assert "does not exist" in str(ei.value)


def test_ambiguous_fallback_time_is_accepted():
    """2026-11-01 01:30 America/Los_Angeles happens twice (fall back) — an
    ambiguous time is real and must validate (fold=0 = first occurrence)."""
    w = TimeWindow(start=datetime(2026, 11, 1, 1, 30), end=datetime(2026, 11, 1, 2, 0),
                   tz="America/Los_Angeles")
    assert w.epoch_bounds()[0] < w.epoch_bounds()[1]


def test_end_equal_start_allowed():
    w = _window(end=datetime(2026, 8, 11, 0, 0))
    s, e = w.epoch_bounds()
    assert s == e


# --- evolution idiom: unknown extras round-trip -------------------------------

def test_round_trip_preserves_unknown_extra_field():
    payload = {"start": "2026-08-11T00:00:00", "end": "2026-08-11T12:00:00",
               "tz": "America/Los_Angeles", "future_knob": {"a": 1}}
    w = TimeWindow.model_validate(payload)
    assert w.model_dump()["future_knob"] == {"a": 1}

    cr = CountResult.model_validate({"total": 3, "novel_field": "kept"})
    assert cr.model_dump()["novel_field"] == "kept"


def test_defaults_everywhere_on_result_models():
    """Every result model validates with zero args (evolution rule)."""
    assert CountResult().total == 0
    assert CountResult().window is None
    assert ResolutionProvenance().categories_matched == []
    assert EventRow().frames == 0
    assert Bucket().count == 0


def test_count_result_composes_window_provenance_and_evidence():
    cr = CountResult(
        total=77, per_camera={"nvr-ch2": 55, "nvr-ch1": 22}, window=_window(),
        resolution=ResolutionProvenance(
            categories_matched=["car"], category_source="plural-strip",
            dedup_mode="raw", dedup_source="per-window tracks"),
        caveats=["raw per-window tracks; no cross-window/camera dedup"],
        evidence=[EvidenceItem(modality="object_count", content="ch2: 55")],
    )
    rt = CountResult.model_validate(cr.model_dump())
    assert rt.total == 77
    assert rt.per_camera["nvr-ch1"] == 22
    assert rt.window.tz == "America/Los_Angeles"
    assert rt.resolution.category_source == "plural-strip"
    assert rt.evidence[0].modality == "object_count"
