"""Gated golden harness for per-modality QUERY fixtures (tests/golden_queries/*.yaml).

Complements `test_golden_ask.py` (which runs the `ask_questions:` deep-scan blocks).
This one runs the retrieval blocks against a pre-ingested real-model workdir:

  - `queries:`       one entry per (modality, expect) — visual / caption / transcript /
                     on_screen_text / object / action / object_count. match vs no_match
                     semantics per the dir README.
  - `semantic_text:` Retrieval Layer (SR.1/SR.2): a query sharing NO words with its
                     target line must still rank the right line first (bge-m3).
  - `diarization:`   distinct-speaker count (Role 9) read from the transcripts table,
                     asserted within a human-verified bounded range.

Every case PRINTS its measured value (score / count / hit) before asserting, so a run
with `-s` is also the calibration table the README asks for.

Skipped by default (needs GPU models + an ingested workdir). Run for real with:

    RUN_GOLDEN=1 VA_CONFIG_DIR=run-claude/config GOLDEN_WORKDIR=.va-shots \
        .venv/bin/pytest tests/test_golden_queries.py -m golden -q -s

A case is skipped (not failed) if its video isn't ingested in the workdir.
"""
import os
import sqlite3
from pathlib import Path

import pytest
import yaml

GOLDEN_DIR = Path(__file__).parent / "golden_queries"

pytestmark = pytest.mark.golden

if not os.environ.get("RUN_GOLDEN"):
    pytest.skip("golden query harness disabled (set RUN_GOLDEN=1)", allow_module_level=True)


def _workdir() -> str:
    return os.environ.get("GOLDEN_WORKDIR", ".va-shots")


def _cases(block: str):
    out = []
    for path in sorted(GOLDEN_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        for q in doc.get(block, []):
            # A query may carry `xfail: "<reason>"` to document a KNOWN model
            # limitation (SigLIP color-negation, YOLO vocab gaps, …). strict=True
            # so an unexpected pass (a better model) is reported as xpass -> a
            # red alert to revisit the fixture, not a silent green.
            marks = [pytest.mark.xfail(reason=q["xfail"], strict=True)] if q.get("xfail") else []
            out.append(pytest.param(doc, q, id=q.get("id", q.get("query", "?")), marks=marks))
    return out


def _resolve(doc):
    """The catalog Video for this fixture, or skip if not ingested in the workdir."""
    from va.pipeline.paths import Workspace
    from va.storage.structured.catalog_sqlite import Catalog

    catalog = Catalog(Workspace(_workdir()).catalog_db)
    try:
        video = catalog.get_by_source_key(doc["source_key"])
    finally:
        catalog.close()
    if video is None or video.ingest_status.value != "done":
        pytest.skip(f"{doc['video_id']} not ingested in {_workdir()}")
    return video


def _overlaps(start, end, rng) -> bool:
    """Does [start,end] intersect the fixture's [lo,hi] time_range? (None -> any)."""
    if not rng:
        return True
    return end >= rng[0] and start <= rng[1]


# Per text-modality: the search fn + how to read a hit's (start, end, video_id).
def _text_hits(modality, query, workdir):
    if modality == "caption":
        from va.pipeline.caption import search_captions
        hits = search_captions(query, workdir=workdir, k=20)
        return [(h, h.start_time, h.end_time, h.video_id) for h in hits]
    if modality == "transcript":
        from va.pipeline.transcript import search_transcripts
        hits = search_transcripts(query, workdir=workdir, k=20)
        return [(h, h.start_time, h.end_time, h.video_id) for h in hits]
    if modality == "on_screen_text":
        from va.pipeline.ocr import search_ocr
        hits = search_ocr(query, workdir=workdir, k=20)
        return [(h, h.time_start, h.time_end, h.video_id) for h in hits]
    if modality == "action":
        from va.pipeline.actions import search_actions
        hits = search_actions(query, workdir=workdir, k=20)
        return [(h, h.start_time, h.end_time, h.video_id) for h in hits]
    raise AssertionError(f"unknown text modality {modality!r}")


# --- queries: block ----------------------------------------------------------

@pytest.mark.parametrize("doc,q", _cases("queries"))
def test_golden_query(doc, q):
    video = _resolve(doc)
    vid, wd = video.id, _workdir()
    modality = q.get("modality", "visual")
    expect, rng = q["expect"], q.get("time_range")
    tag = f"[{q['id']}] {modality:14s} expect={expect:8s}"

    if modality == "visual":
        from va.pipeline.query import query as visual_query
        from va.pipeline.verify import verify_visual_hits
        min_score = float(q.get("min_score", doc.get("default_min_score", 0.05)))
        # over-fetch so the target video's own best frame is present even when
        # other videos out-score it globally (one shared index over all shards).
        hits = [h for h in visual_query(q["query"], workdir=wd, k=256) if h.video_id == vid]
        # SR.6: VLM-verify ONLY queries flagged `verify` — those hitting SigLIP's
        # known weaknesses (attribute/negation/composition). Blanket verification
        # erodes recall on weak true-positives (a distant-grandstands query), so in
        # production the Role-11 planner sets this flag per query type; here the
        # fixture declares it. stop=1 -> a true match confirms on its strongest
        # frame (1 call); a true negative checks every above-floor candidate.
        if q.get("verify"):
            hits = verify_visual_hits(hits, q["query"], workdir=wd, floor=min_score,
                                      max_verify=80, stop_after_accepts=1)
        top = max(hits, key=lambda h: h.score, default=None)
        shown = "none" if top is None else f"{top.score:.3f}@{top.timestamp:.0f}s"
        if expect == "match":
            # "locatable in the window": the strongest hit INSIDE time_range must
            # clear min_score (not the global top — a multi-instance concept like a
            # snake can peak at a different valid moment outside the window).
            scope = [h for h in hits if _overlaps(h.timestamp, h.timestamp, rng)]
            best = max(scope, key=lambda h: h.score, default=None)
            ok = best is not None and best.score >= min_score
            detail = "none" if best is None else f"{best.score:.3f}@{best.timestamp:.0f}s"
            if not ok and q.get("verify"):
                # SR.6 recall-recovery: SigLIP under-scored a frame the VLM confirms.
                from va.pipeline.verify import verify_scene_presence
                n = verify_scene_presence(q["query"], vid, workdir=wd, window=rng, samples=8)
                ok = n >= 1
                detail += f" + scene-presence {n}/8"
            print(f"{tag} min_score={min_score:.3f}  in_range_best={detail} (global {shown})")
            assert ok, \
                f"{q['id']}: best in {rng or 'any'} = {detail} < min_score {min_score} (q={q['query']!r})"
        else:
            print(f"{tag} min_score={min_score:.3f}  top={shown}")
            assert top is None or top.score < min_score, \
                f"{q['id']}: top {shown} >= min_score {min_score} — false hit (q={q['query']!r})"
        return

    if modality in ("caption", "transcript", "on_screen_text", "action"):
        rows = [r for r in _text_hits(modality, q["query"], wd) if r[3] == vid]
        in_rng = [r for r in rows if _overlaps(r[1], r[2], rng)]
        print(f"{tag} hits={len(rows)} in_range={len(in_rng)}  "
              f"top={(rows[0][0].__dict__ if rows else None)!r:.120}")
        if expect == "match":
            assert in_rng, f"{q['id']}: no {modality} hit in {rng} (q={q['query']!r})"
        else:
            assert not rows, f"{q['id']}: unexpected {modality} hit (q={q['query']!r}): {rows[0][0]}"
        return

    if modality == "object":
        from va.pipeline.objects import query_objects
        rows = [s for s in query_objects(q["query"], workdir=wd) if s.video_id == vid]
        via, present = "yolo", bool(rows)
        if not present and q.get("verify"):
            # SR.6: YOLO's fixed vocab found nothing and the query is flagged for
            # verification — ask the VLM (no-op stub -> 0).
            from va.pipeline.verify import verify_object_presence
            klass = q["query"].strip().split()[-1]
            n = verify_object_presence(klass, vid, workdir=wd, samples=8)
            present, via = (n >= 1), f"vlm:{n}/8"
        print(f"{tag} present={present} via={via} yolo={[s.object_class for s in rows]}")
        if expect == "match":
            assert present, f"{q['id']}: '{q['query']}' not found by YOLO or VLM"
        else:
            assert not present, f"{q['id']}: '{q['query']}' wrongly reported present ({via})"
        return

    if modality == "object_count":
        from va.pipeline.objects import count_objects
        rows = [c for c in count_objects(q["query"], workdir=wd) if c.video_id == vid]
        n = rows[0].distinct if rows else 0
        lo, hi = q.get("count_min"), q.get("count_max")
        print(f"{tag} distinct={n} bounds=[{lo},{hi}]")
        if expect == "match":
            assert rows, f"{q['id']}: no distinct count for {q['query']!r}"
            assert lo <= n <= hi, f"{q['id']}: distinct {n} outside [{lo},{hi}]"
        else:
            assert not rows, f"{q['id']}: unexpected count {n} for {q['query']!r}"
        return

    raise AssertionError(f"{q['id']}: unhandled modality {modality!r}")


# --- semantic_text: block (Retrieval Layer SR.1/SR.2) ------------------------

@pytest.mark.parametrize("doc,q", _cases("semantic_text"))
def test_golden_semantic_text(doc, q):
    video = _resolve(doc)
    from va.pipeline.text_search import search_text

    modality = q.get("modality")
    mods = [modality] if modality else None
    hits = [h for h in search_text(q["query"], workdir=_workdir(), k=20, modalities=mods)
            if h.video_id == video.id]
    want = q["expect_text"].lower()
    top = hits[0].text if hits else None
    print(f"[{q['id']}] semantic '{q['query']}' -> top={top!r} (want ~{want!r})")
    assert hits, f"{q['id']}: no semantic hit for {q['query']!r}"
    # the RIGHT line ranks first despite sharing no words with the query
    assert want in hits[0].text.lower(), \
        f"{q['id']}: top semantic hit {top!r} does not contain {want!r}"


# --- diarization: block (Role 9 distinct-speaker count) ----------------------

@pytest.mark.parametrize("doc,q", _cases("diarization"))
def test_golden_diarization(doc, q):
    video = _resolve(doc)
    if q.get("check") != "distinct_speakers":
        pytest.skip(f"unsupported diarization check {q.get('check')!r}")

    con = sqlite3.connect(str(__import__("va.pipeline.paths", fromlist=["Workspace"])
                          .Workspace(_workdir()).catalog_db))
    try:
        (n,) = con.execute(
            "SELECT COUNT(DISTINCT speaker) FROM transcripts "
            "WHERE video_id = ? AND speaker IS NOT NULL",
            (str(video.id),),
        ).fetchone()
    finally:
        con.close()
    lo, hi = q["expected_min"], q["expected_max"]
    print(f"[{q['id']}] distinct_speakers={n} bounds=[{lo},{hi}] "
          f"(human truth={q.get('ground_truth')})")
    assert lo <= n <= hi, (
        f"{q['id']}: distinct speakers {n} outside [{lo},{hi}] "
        f"(human truth {q.get('ground_truth')}, provenance {q.get('provenance')})"
    )
