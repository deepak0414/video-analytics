"""Gated golden harness for ASK-level questions (deep-scan counting).

Asserts the CODE-COUNTED deep_scan_count attributes against human-verified
bounds from tests/golden_queries/*.yaml `ask_questions` — the narrator's prose
is not asserted (only the deterministic numbers are stable by design).

Skipped by default (needs GPU models + the claude CLI + ingested workdirs).
Run for real with:

    RUN_GOLDEN=1 VA_CONFIG_DIR=run-claude/config GOLDEN_WORKDIR=.va-shots \
        .venv/bin/pytest -m golden -q

A question is skipped (not failed) if its video isn't in the workdir's catalog.
"""
import os
from pathlib import Path

import pytest
import yaml

GOLDEN_DIR = Path(__file__).parent / "golden_queries"

pytestmark = pytest.mark.golden

if not os.environ.get("RUN_GOLDEN"):
    pytest.skip("golden ask harness disabled (set RUN_GOLDEN=1)", allow_module_level=True)


def _ask_cases():
    cases = []
    for path in sorted(GOLDEN_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        for q in doc.get("ask_questions", []):
            cases.append(pytest.param(doc, q, id=q["id"]))
    return cases


@pytest.mark.parametrize("doc,case", _ask_cases())
def test_golden_ask(doc, case):
    from va.pipeline.ask import ask
    from va.pipeline.paths import Workspace
    from va.storage.structured.catalog_sqlite import Catalog

    workdir = os.environ.get("GOLDEN_WORKDIR", ".va-shots")
    catalog = Catalog(Workspace(workdir).catalog_db)
    try:
        video = catalog.get_by_source_key(doc["source_key"])
    finally:
        catalog.close()
    if video is None or video.ingest_status.value != "done":
        pytest.skip(f"{doc['video_id']} not ingested in {workdir}")

    res = ask(case["question"], workdir=workdir, k=15)
    ds = [i for i in res.evidence.items if i.modality == "deep_scan_count"]
    assert ds, f"deep scan did not run; notes={res.evidence.notes}"

    stat = case["statistic"]
    value = ds[0].attributes.get(stat)
    assert value is not None, f"statistic {stat!r} missing from {ds[0].attributes}"
    assert case["expected_min"] <= value <= case["expected_max"], (
        f"{case['id']}: {stat}={value}, expected "
        f"[{case['expected_min']}, {case['expected_max']}] "
        f"(provenance: {case.get('provenance')})"
    )
