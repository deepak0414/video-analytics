"""resolve_category seam (typed-query tier, TQ1.b).

The seam is a pure STRUCTURAL stub: plural-strip word expansion, identical to
what `pipeline.objects._classes` has always done, plus a provenance source
string. The parity table is the done-when: same inputs, same outputs.
"""
from __future__ import annotations

import pytest

from va.pipeline.aggregate import CATEGORY_SOURCE_PLURAL_STRIP, resolve_category
from va.pipeline.objects import _classes

# Inputs spanning the observed shapes: single word, plural, multiword query
# text, mixed case, punctuation, possessive, digits, s-only word, empty.
# Expected outputs are PINNED BY HAND — `_classes` now delegates to
# `resolve_category`, so an assertion of mere equality between the two would be
# tautological; these literals are what actually freeze the behavior.
PARITY_TABLE = [
    ("car", ["car"]),
    ("cars", ["cars", "car"]),
    ("birds", ["birds", "bird"]),
    ("car person", ["car", "person"]),
    ("Cars and Trucks", ["cars", "car", "and", "trucks", "truck"]),
    ("red sports car!", ["red", "sports", "sport", "car"]),
    ("person's dog", ["person's", "person'", "dog"]),
    ("channel2 cars", ["channel2", "cars", "car"]),
    ("s", ["s"]),
    # rstrip('s') strips ALL trailing s -> "glass" becomes "gla" (known stub quirk)
    ("glass", ["glass", "gla"]),
    ("", []),
    # no synonym expansion: stays vehicle(s) — Role-12 territory
    ("vehicles", ["vehicles", "vehicle"]),
]


@pytest.mark.parametrize("text,expected", PARITY_TABLE)
def test_pinned_outputs_and_parity_with_objects_classes(text, expected):
    categories, source = resolve_category(text)
    assert categories == expected           # the pinned behavior
    assert categories == _classes(text)     # and the delegation stays intact
    assert source == CATEGORY_SOURCE_PLURAL_STRIP


def test_plural_strip_includes_both_forms_in_order():
    assert resolve_category("cars")[0] == ["cars", "car"]


def test_no_synonym_content():
    """The stub must NOT expand hypernyms — 'vehicle' stays itself (flagged
    item TQ1.b2 / the Role-12 taxonomy is the only sanctioned place for that)."""
    categories, _ = resolve_category("vehicles")
    assert categories == ["vehicles", "vehicle"]
    assert "car" not in categories and "truck" not in categories


def test_provenance_source_is_the_stub_string():
    assert resolve_category("dog")[1] == "plural-strip"


def test_dedups_repeated_words():
    assert resolve_category("car car cars")[0] == ["car", "cars"]
