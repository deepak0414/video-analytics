#!/usr/bin/env python3
"""Validate the NVR burned-in-clock gate against the real .va-24h footage.

Answers one question honestly: on genuinely-delivered footage, does the clock gate
(`sources/ocr_clock.py` + `sources/verify.py`) REJECT good clips? The gate exists to
catch wrong-week ring fragments; it must not false-reject correct footage.

TWO modes, because they trade fidelity for cost:

  --proxy (default, seconds): use the persisted Role-10 OCR rows (`ocr_results`) as a
    stand-in for the head the gate OCRs. This is a PROXY, and an imperfect one — its
    caveats are stated so no reader over-trusts it:
      * Role-10 sampled at ~1 fps over the WHOLE clip, so the "head" here is the
        earliest N rows spanning many seconds — reaching far PAST the shipped reader's
        ~1.5 s window (`CLOCK_HEAD_SECONDS`, 8 sparse frames via head_clock_frames) into
        the good body — so the proxy UNDER-counts a multi-second wrong-week head and its
        false-reject picture is OPTIMISTIC.
      * `ocr_results` has no confidence column; every row is assumed above the floor.
    What the proxy DOES establish soundly is the PARSE-level robustness (how many rows
    parse, and how many land > tolerance off), which is independent of head sampling.

  --real (minutes; needs the [ocr] extra): run the SHIPPED `OcrClockReader` over each
    clip's true head frames via `head_clock_frames` (~1.5 s span, 8 sparse samples from
    frame 0) — exactly what a live pull does. This is the definitive number; it loads
    RapidOCR and OCRs those samples per clip.

Usage:
    .venv/bin/python scripts/validate_clock_gate_va24h.py [--workdir .va-24h]
                     [--proxy | --real] [--head-frames 8] [--tz America/Los_Angeles]

Reports, per mode: the regex parse count and > tolerance "off" rows under the OLD
(`\\d{1,2}`) vs SHIPPED (`\\d{2}`) parse, and the end-to-end accept/trim/reject tally.
In `--real` mode each reject is listed with its emitted HEAD readings as
`(t_s, skew_days)` pairs and whether its BODY is aligned, so a true-positive (a clean
multi-second wrong-week head over a good body) can be told from a hypothetical OCR
misread from the script's own output — no ad-hoc re-instrumentation.
"""
from __future__ import annotations

import argparse
import collections
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# The SHIPPED parse/consensus + pure verifier — validate the real code, not a copy.
from va.sources.ocr_clock import clock_readings_from_texts
from va.sources.verify import (ExpectedProfile, ObservedSignals, RequestedWindow,
                               verify_delivery)
from va.sources.nvr import CLOCK_OCR_TOL_S

# The pre-fix permissive regex, kept here ONLY to quantify what the \d{2} fix removed.
_OLD_RE = re.compile(
    r"(?P<mo>\d{1,2})\s*[-/.]\s*(?P<d>\d{1,2})\s*[-/.]\s*(?P<y>\d{4})"
    r"[^0-9]{0,4}"
    r"(?P<h>\d{1,2})\s*[:.]\s*(?P<mi>\d{2})\s*[:.]\s*(?P<s>\d{2})"
    r"\s*(?P<ap>[AaPp])\s*[Mm]")


def _old_parse_epoch(text, tz):
    m = _OLD_RE.search(text or "")
    if not m:
        return None
    mo, d, y = int(m["mo"]), int(m["d"]), int(m["y"])
    h, mi, s = int(m["h"]), int(m["mi"]), int(m["s"])
    if not (1 <= h <= 12):
        return None
    h = h % 12 + 12 if m["ap"].lower() == "p" else h % 12
    try:
        return datetime(y, mo, d, h, mi, s).replace(tzinfo=tz).timestamp()
    except ValueError:
        return None


def _parse_robustness(rows_by_vid, se, tz):
    """Old vs shipped PARSE: total parsed and rows landing > tolerance off. Sampling-
    independent, so it is the sound part of the proxy."""
    from va.sources.ocr_clock import parse_lorex_clock, _to_epoch

    def tally(parse):
        parsed = off = 0
        for vid, rows in rows_by_vid.items():
            for t, text in rows:
                ep = parse(text)
                if ep is None:
                    continue
                parsed += 1
                if abs(ep - (se[vid] + t)) > CLOCK_OCR_TOL_S:
                    off += 1
        return parsed, off

    old = tally(lambda text: _old_parse_epoch(text, tz))

    def shipped(text):
        dt = parse_lorex_clock(text)
        return None if dt is None else _to_epoch(dt, tz)

    new = tally(shipped)
    print(f"  parse robustness  OLD \\d{{1,2}}: parsed={old[0]} off={old[1]}"
          f"   SHIPPED \\d{{2}}: parsed={new[0]} off={new[1]}")


def _gate(readings, start_epoch):
    return verify_delivery(RequestedWindow("nvr-ch", start_epoch, 30.0),
                           ObservedSignals(clock=readings),
                           ExpectedProfile(clock_tol_s=CLOCK_OCR_TOL_S))


def run_proxy(con, se, tz, head_n):
    rows_by_vid = collections.defaultdict(list)
    for vid, t, text in con.execute(
            "SELECT video_id,timestamp,text FROM ocr_results ORDER BY timestamp"):
        if vid in se:
            rows_by_vid[vid].append((t, text))
    _parse_robustness(rows_by_vid, se, tz)
    verdicts, rejects = collections.Counter(), []
    for vid, rows in rows_by_vid.items():
        head = rows[:head_n]                                  # PROXY head (see module doc)
        readings = clock_readings_from_texts(
            [(t, text, 0.95) for (t, text) in head], tz)
        v = _gate(readings, se[vid])
        verdicts[v.action] += 1
        if v.action == "reject":
            rejects.append((vid[:8], [text for _, text in head][:3]))
    return verdicts, rejects


def _body_status(con, vid, se_v, tz):
    """Classify the clip's BODY (t >= 1 s Role-10 rows) as 'aligned' (>= 1 row within
    tolerance → a long wrong-week HEAD over a good body, recoverable on a live pull via
    the exact-window fallback), 'off' (rows parse but all land wrong-week → whole-clip
    wrong-week, a verified-bad reject), or 'unreadable' (no body row parses → the reject
    is UNVERIFIABLE from the stored rows, not a proven wrong-week). Splitting these three
    keeps the 'genuine wrong-week' claim honest: an unverifiable reject is not evidence.
    Uses the stored rows so the classification is cheap and reproducible."""
    from va.sources.ocr_clock import parse_lorex_clock, _to_epoch
    parsed_any = False
    for t, text in con.execute(
            "SELECT timestamp,text FROM ocr_results WHERE video_id=? AND timestamp>=1.0 "
            "ORDER BY timestamp", (vid,)):
        dt = parse_lorex_clock(text)
        if dt is None:
            continue
        parsed_any = True
        if abs(_to_epoch(dt, tz) - (se_v + t)) <= CLOCK_OCR_TOL_S:
            return "aligned"
    return "off" if parsed_any else "unreadable"


def run_real(con, se, tz, head_n, workdir):
    import os

    from va.pipeline.paths import Workspace
    from va.sources.ocr_clock import default_timestamp_reader

    os.environ.pop("VA_NVR_CLOCK_GATE", None)                 # force-enable if [ocr] present
    reader = default_timestamp_reader()
    if reader is None:
        sys.exit("--real needs the [ocr] extra (RapidOCR) importable; none found.")
    ws = Workspace(workdir)                                    # maps source_key -> clip dir
    verdicts, rejects = collections.Counter(), []
    reject_class = collections.Counter()                      # head-off/body-aligned vs whole-clip
    n = 0
    for vid in se:
        (sk,) = con.execute("SELECT source_key FROM videos WHERE id=?", (vid,)).fetchone()
        clip = ws.video_dir(sk) / "media.mp4"                 # globs the <key16>-* dir
        if not clip.exists():
            continue
        readings = reader.read_head_clock(str(clip), head_n)
        v = _gate(tuple(readings), se[vid])
        verdicts[v.action] += 1
        n += 1
        if v.action == "reject":
            # Classify the reject by BODY status: a long wrong-week head over a good
            # body (recoverable via the exact-window fallback on a live pull), a
            # whole-clip wrong-week (body also off — verified bad), or unverifiable
            # (no body row parses). Record the emitted HEAD readings as (t_s, skew_days)
            # so a reader can AUDIT the split from this output alone.
            kind = {"aligned": "body-aligned(long-head)",
                    "off": "whole-clip-wrong-week",
                    "unreadable": "body-unreadable(unverifiable)"}[
                        _body_status(con, vid, se[vid], tz)]
            reject_class[kind] += 1
            head = [(round(r.t, 2), round((r.observed_epoch - (se[vid] + r.t)) / 86400.0, 2))
                    for r in readings]
            rejects.append((vid[:8], kind, head, str(clip)))
        if n % 25 == 0:
            print(f"    ... {n} clips  (rejects so far: {dict(reject_class)})", file=sys.stderr)
    print(f"  reject breakdown: {dict(reject_class)}")
    return verdicts, rejects


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".va-24h")
    ap.add_argument("--tz", default="America/Los_Angeles")
    ap.add_argument("--head-frames", type=int, default=8)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--proxy", action="store_true", help="Role-10 OCR rows (default)")
    mode.add_argument("--real", action="store_true", help="shipped reader on clip files")
    args = ap.parse_args()

    tz = ZoneInfo(args.tz)
    con = sqlite3.connect(f"{args.workdir}/catalog.db")
    se = {v: e for v, e in con.execute(
        "SELECT id,start_epoch FROM videos WHERE start_epoch IS NOT NULL")}
    print(f"videos with start_epoch: {len(se)}   tz={args.tz}   head_frames={args.head_frames}")

    if args.real:
        print("mode: REAL (shipped OcrClockReader over clip head frames)")
        verdicts, rejects = run_real(con, se, tz, args.head_frames, args.workdir)
    else:
        print("mode: PROXY (Role-10 OCR rows as head — OPTIMISTIC, see module doc)")
        verdicts, rejects = run_proxy(con, se, tz, args.head_frames)
    con.close()

    print(f"  clock-gate verdicts: {dict(verdicts)}")
    print(f"  rejects (fail-closed): {len(rejects)}. A 'body-aligned' reject is a clip "
          f"with a LONG wrong-week head (measured 2-3 s+) over a good body: on a LIVE "
          f"pull that reject triggers the exact-window fallback (no pre-pad seek), which "
          f"re-pulls it clean; here (.va-24h is off-ring) it stays correctly excluded. A "
          f"'whole-clip-wrong-week' reject has no aligned body at all.")
    for r in rejects:
        print(f"    {r}")


if __name__ == "__main__":
    main()
