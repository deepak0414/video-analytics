"""WS4.a — MotionSource protocol: sidecar stub, clustering, LNR log parser.

Done-conditions from architecture-evolution-loop.md (offline half): stub-adapter
tests return synthetic motion windows. The vendor adapter's device I/O is stubbed
here (`_get`); the live LNR608 exercise is the human-gated WS4.a2 item.
"""
import json

import pytest

from va.adapters.motion_source.lnr_eventlog_inproc import (
    LnrEventLogMotionSource,
    _detail_field,
    _parse_items,
)
from va.adapters.motion_source.sidecar_inproc import SidecarMotionSource
from va.contracts.motion import MotionEvent
from va.roles.motion_source import cluster_events


def _ev(cam, start, end):
    return MotionEvent(camera_ref=cam, start_epoch=start, end_epoch=end)


# --- sidecar stub -------------------------------------------------------------

def test_sidecar_filters_range_and_camera(tmp_path):
    f = tmp_path / "motion.json"
    f.write_text(json.dumps({"events": [
        {"camera_ref": "1", "start_epoch": 100.0, "end_epoch": 130.0},
        {"camera_ref": "2", "start_epoch": 200.0, "end_epoch": 230.0},
        {"camera_ref": "1", "start_epoch": 900.0, "end_epoch": 930.0},
    ]}))
    src = SidecarMotionSource(f)
    got = src.events(0, 500)
    assert [(e.camera_ref, e.start_epoch) for e in got] == [("1", 100.0), ("2", 200.0)]
    assert [e.start_epoch for e in src.events(0, 1000, camera_ref="1")] == [100.0, 900.0]
    # overlap counts: an event straddling the range edge is included
    assert src.events(120, 150)[0].start_epoch == 100.0


def test_sidecar_missing_file_is_a_quiet_day(tmp_path):
    assert SidecarMotionSource(tmp_path / "absent.json").events(0, 100) == []
    assert SidecarMotionSource(None).events(0, 100) == []


# --- clustering ---------------------------------------------------------------

def test_cluster_merges_within_gap_per_camera():
    events = [
        _ev("1", 0, 10), _ev("1", 25, 40),      # gap 15 <= 30 -> merge
        _ev("1", 300, 310),                     # far -> separate
        _ev("2", 20, 30),                       # other camera never merges in
    ]
    merged = cluster_events(events, gap_s=30.0)
    assert [(e.camera_ref, e.start_epoch, e.end_epoch) for e in merged] == [
        ("1", 0, 40), ("2", 20, 30), ("1", 300, 310),
    ]


def test_cluster_gap_zero_keeps_touching_only():
    merged = cluster_events([_ev("1", 0, 10), _ev("1", 10, 20), _ev("1", 21, 30)],
                            gap_s=0.0)
    assert [(e.start_epoch, e.end_epoch) for e in merged] == [(0, 20), (21, 30)]


def test_cluster_is_order_insensitive_and_keeps_longer_end():
    merged = cluster_events([_ev("1", 20, 60), _ev("1", 0, 50)], gap_s=5.0)
    assert [(e.start_epoch, e.end_epoch) for e in merged] == [(0, 60)]


# --- LNR log parsing (device I/O stubbed; live probe = WS4.a2) ---------------

FLAT_PAGE = """token=123
items[0].Time=2026-07-21 10:31:29
items[0].Type=Motion Detect
items[0].Detail=Channel No.: 2 Start Time: 2026-07-21 10:31:29 End Time: 2026-07-21 10:31:59
items[1].Time=2026-07-21 10:32:00
items[1].Type=Login
items[1].Detail=User: admin
items[2].Time=2026-07-21 10:40:00
items[2].Type=Motion Detect
items[2].Detail=Channel No.: 5 Start Time: 2026-07-21 10:40:00 End Time: 2026-07-21 10:40:20
"""

DOTTED_PAGE = """items[0].Time=2026-07-21 10:31:29
items[0].Type=Motion Detect
items[0].Detail.Channel=2
items[0].Detail.StartTime=2026-07-21 10:31:29
items[0].Detail.EndTime=2026-07-21 10:31:59
"""


def test_parse_items_both_shapes():
    flat = _parse_items(FLAT_PAGE)
    assert len(flat) == 3 and flat[1]["Type"] == "Login"
    assert _detail_field(flat[0], "Channel").startswith("Channel") is False
    assert "2" in _detail_field(flat[0], "Channel")
    assert _detail_field(flat[0], "Start Time") == "2026-07-21 10:31:29"
    assert _detail_field(flat[0], "End Time") == "2026-07-21 10:31:59"

    dotted = _parse_items(DOTTED_PAGE)
    assert _detail_field(dotted[0], "Channel") == "2"
    assert _detail_field(dotted[0], "Start Time") == "2026-07-21 10:31:29"


def _stubbed_source(monkeypatch, pages):
    monkeypatch.setenv("VA_NVR_USER", "u")
    monkeypatch.setenv("VA_NVR_PASS", "p")
    src = LnrEventLogMotionSource({"host": "http://nvr.test", "tz": "UTC"})
    calls = []

    def fake_get(path_qs):
        calls.append(path_qs)
        if "startFind" in path_qs:
            return "token=777"
        if "doFind" in path_qs:
            return pages.pop(0) if pages else ""
        return "OK"
    monkeypatch.setattr(src, "_get", fake_get)
    return src, calls


def _utc(y, mo, d, h=0, mi=0):
    import datetime as dt
    return dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc).timestamp()


def test_vendor_events_filters_types_and_maps_epochs(monkeypatch):
    src, calls = _stubbed_source(monkeypatch, [FLAT_PAGE])
    got = src.events(_utc(2026, 7, 21), _utc(2026, 7, 22))
    assert [(e.camera_ref, e.kind) for e in got] == [("2", "motion"), ("5", "motion")]
    # UTC tz: "2026-07-21 10:31:29" -> that exact UTC epoch; end - start = 30s
    assert got[0].end_epoch - got[0].start_epoch == 30.0
    assert got[0].start_epoch == _utc(2026, 7, 21, 10, 31) + 29
    assert any("stopFind" in c for c in calls)  # token released even on success


def test_vendor_applies_its_own_overlap_filter(monkeypatch):
    # Device range semantics are never trusted: events outside the requested
    # window are dropped client-side (the Protocol's overlap contract).
    src, _ = _stubbed_source(monkeypatch, [FLAT_PAGE])
    assert src.events(_utc(2026, 7, 21, 11, 0), _utc(2026, 7, 22)) == []


def test_vendor_camera_filter_and_error_guidance(monkeypatch):
    src, _ = _stubbed_source(monkeypatch, [FLAT_PAGE])
    only5 = src.events(0, 2_000_000_000, camera_ref="5")
    assert [(e.camera_ref) for e in only5] == ["5"]

    monkeypatch.delenv("VA_NVR_USER", raising=False)
    monkeypatch.delenv("VA_NVR_PASS", raising=False)
    with pytest.raises(ValueError, match="VA_NVR_USER"):
        LnrEventLogMotionSource({"host": "http://nvr.test"})
    monkeypatch.setenv("VA_NVR_USER", "u")
    monkeypatch.setenv("VA_NVR_PASS", "p")
    monkeypatch.delenv("VA_NVR_HOST", raising=False)
    with pytest.raises(ValueError, match="host"):
        LnrEventLogMotionSource({})


# Verbatim shape captured from the live LNR608 (WS4.a2, 2026-08-03): Detail is
# MULTI-LINE, and an episode is TWO entries — a Start marker and an End marker.
REAL_PAGE = """found=6
items[0].Detail=Event Type:Motion Detect
Channel:1
Start Time:2026-08-03 12:00:05

items[0].Time=2026-08-03 12:00:05
items[0].Type=Motion Detect
items[0].User=cloud
items[1].Detail=Event Type:Motion Detect
Channel:2
Start Time:2026-08-03 12:00:07

items[1].Time=2026-08-03 12:00:07
items[1].Type=Motion Detect
items[1].User=cloud
items[2].Detail=Event Type:Motion Detect
Channel:1
End Time:2026-08-03 12:00:20

items[2].Time=2026-08-03 12:00:20
items[2].Type=Motion Detect
items[2].User=cloud
items[3].Detail=Event Type:Motion Detect
Channel:2
End Time:2026-08-03 12:00:21

items[3].Time=2026-08-03 12:00:21
items[3].Type=Motion Detect
items[3].User=cloud
items[4].Detail=Event Type:Motion Detect
Channel:1
End Time:2026-08-03 12:05:00

items[4].Time=2026-08-03 12:05:00
items[4].Type=Motion Detect
items[4].User=cloud
items[5].Detail=Event Type:Motion Detect
Channel:1
Start Time:2026-08-03 12:09:00

items[5].Time=2026-08-03 12:09:00
items[5].Type=Motion Detect
items[5].User=cloud
"""


def test_vendor_pairs_live_start_end_markers(monkeypatch):
    src, _ = _stubbed_source(monkeypatch, [REAL_PAGE])
    got = src.events(_utc(2026, 8, 3, 11, 0), _utc(2026, 8, 3, 13, 0))
    windows = [(e.camera_ref, e.end_epoch - e.start_epoch) for e in got]
    # cam1 05->20 (15s), cam2 07->21 (14s), cam1 unmatched End at 12:05 (instant),
    # cam1 unmatched Start at 12:09 (open episode -> start-anchored instant).
    assert windows == [("1", 15.0), ("2", 14.0), ("1", 0.0), ("1", 0.0)]
    assert got[0].start_epoch == _utc(2026, 8, 3, 12, 0) + 5
    assert got[-1].attributes.get("open") is True


def test_vendor_paginates_until_empty_page(monkeypatch):
    # A device capping pages below the requested count must not drop the tail:
    # the loop stops only on an EMPTY page.
    page2 = """items[0].Time=2026-07-21 11:00:00
items[0].Type=Motion Detect
items[0].Detail=Channel No.: 1 Start Time: 2026-07-21 11:00:00 End Time: 2026-07-21 11:00:10
"""
    src, calls = _stubbed_source(monkeypatch, [FLAT_PAGE, page2])
    got = src.events(0, 2_000_000_000)
    assert [e.camera_ref for e in got] == ["2", "5", "1"]
    assert sum("doFind" in c for c in calls) == 3  # two pages + the empty terminator


def test_vendor_runaway_guards_stop_on_repeated_page(monkeypatch):
    # A cursor that never advances (same page forever) must stop after the first
    # repeat with no duplicated events — not hammer the NVR unboundedly.
    src, calls = _stubbed_source(monkeypatch, [FLAT_PAGE, FLAT_PAGE, FLAT_PAGE])
    got = src.events(_utc(2026, 7, 21), _utc(2026, 7, 22))
    assert [e.camera_ref for e in got] == ["2", "5"]  # one page's worth, once
    assert sum("doFind" in c for c in calls) == 2     # page + the detected repeat


def test_vendor_page_cap_stops_pathological_pagination(monkeypatch):
    # >500 DISTINCT pages (a firmware that never exhausts) must hit the hard cap,
    # not hammer the NVR indefinitely.
    def page(i):
        return (f"items[0].Time=2026-07-21 10:{i % 60:02d}:{i // 60:02d}\n"
                f"items[0].Type=Motion Detect\n"
                f"items[0].Detail=Channel No.: 1 Start Time: 2026-07-21 "
                f"10:{i % 60:02d}:{i // 60:02d} End Time: 2026-07-21 "
                f"10:{i % 60:02d}:{i // 60:02d}\n")
    src, calls = _stubbed_source(monkeypatch, [page(i) for i in range(520)])
    got = src.events(_utc(2026, 7, 21), _utc(2026, 7, 22))
    n_dofind = sum("doFind" in c for c in calls)
    assert n_dofind == 501          # the cap fired on page 501
    assert len(got) == 500          # processed pages only, no unbounded growth


def test_vendor_startfind_times_use_percent20_not_plus(monkeypatch):
    src, calls = _stubbed_source(monkeypatch, [""])
    src.events(_utc(2026, 7, 21), _utc(2026, 7, 21, 1))
    start_call = next(c for c in calls if "startFind" in c)
    assert "%20" in start_call and "+" not in start_call


def test_vendor_dofind_error_mid_pagination_raises(monkeypatch):
    # A token expiring mid-pagination (HTTP-200 error body) must raise — an
    # "empty page" reading would silently under-report the day's motion.
    src, _ = _stubbed_source(monkeypatch, [FLAT_PAGE, "Error\ncode=287"])
    with pytest.raises(RuntimeError, match="doFind returned an error"):
        src.events(_utc(2026, 7, 21), _utc(2026, 7, 22))


def test_vendor_double_start_emits_displaced_episode(monkeypatch):
    page = """items[0].Detail=Event Type:Motion Detect
Channel:1
Start Time:2026-08-03 12:00:05

items[0].Time=2026-08-03 12:00:05
items[0].Type=Motion Detect
items[1].Detail=Event Type:Motion Detect
Channel:1
Start Time:2026-08-03 12:10:00

items[1].Time=2026-08-03 12:10:00
items[1].Type=Motion Detect
items[2].Detail=Event Type:Motion Detect
Channel:1
End Time:2026-08-03 12:10:30

items[2].Time=2026-08-03 12:10:30
items[2].Type=Motion Detect
"""
    src, _ = _stubbed_source(monkeypatch, [page])
    got = src.events(_utc(2026, 8, 3, 11), _utc(2026, 8, 3, 13))
    windows = [(e.start_epoch - _utc(2026, 8, 3, 12), e.end_epoch - e.start_epoch,
                e.attributes.get("open")) for e in got]
    # displaced first Start emitted as an open instant; second Start pairs with End
    assert windows == [(5.0, 0.0, True), (600.0, 30.0, None)]


def test_vendor_unparseable_end_keeps_the_open_start(monkeypatch):
    page = """items[0].Detail=Event Type:Motion Detect
Channel:1
Start Time:2026-08-03 12:00:05

items[0].Time=2026-08-03 12:00:05
items[0].Type=Motion Detect
items[1].Detail=Event Type:Motion Detect
Channel:1
End Time:garbled

items[1].Time=2026-08-03 12:00:40
items[1].Type=Motion Detect
"""
    src, _ = _stubbed_source(monkeypatch, [page])
    got = src.events(_utc(2026, 8, 3, 11), _utc(2026, 8, 3, 13))
    # the bad End is skipped; the open Start survives to the range-end flush
    assert [(e.end_epoch - e.start_epoch, e.attributes.get("open")) for e in got] \
        == [(0.0, True)]


def test_vendor_error_body_raises_not_zero_motion(monkeypatch):
    monkeypatch.setenv("VA_NVR_USER", "u")
    monkeypatch.setenv("VA_NVR_PASS", "p")
    src = LnrEventLogMotionSource({"host": "http://nvr.test", "tz": "UTC"})
    monkeypatch.setattr(src, "_get", lambda q: "Error\ncode=287")
    with pytest.raises(RuntimeError, match="error"):
        src.events(0, 100)


def test_registry_lnr_branch_plumbs_host_and_tz(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path

    import yaml

    from va.registry import get_motion_source

    repo_config = Path(__file__).resolve().parents[1] / "config"
    cdir = tmp_path / "config"
    shutil.copytree(repo_config, cdir)
    doc = yaml.safe_load((cdir / "roles.yaml").read_text())
    doc["roles"]["motion_source"].update(
        {"model": "lnr-eventlog", "host": "http://nvr.test", "tz": "UTC"})
    (cdir / "roles.yaml").write_text(yaml.safe_dump(doc))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))
    monkeypatch.setenv("VA_NVR_USER", "u")
    monkeypatch.setenv("VA_NVR_PASS", "p")

    src = get_motion_source()
    assert isinstance(src, LnrEventLogMotionSource)
    assert src.host == "http://nvr.test" and str(src.tz) == "UTC"


def test_registry_returns_sidecar_by_default(tmp_path, monkeypatch):
    from va.registry import get_motion_source

    src = get_motion_source()
    assert isinstance(src, SidecarMotionSource)


def test_cli_motion_probe_prints_windows(tmp_path, monkeypatch, capsys):
    import shutil
    from pathlib import Path

    import yaml

    from va.cli import main

    repo_config = Path(__file__).resolve().parents[1] / "config"
    cdir = tmp_path / "config"
    shutil.copytree(repo_config, cdir)
    events = tmp_path / "motion.json"
    # times chosen inside the probed local-day range below
    import datetime as dt
    base = dt.datetime(2026, 7, 21, 12, 0).astimezone().timestamp()
    events.write_text(json.dumps({"events": [
        {"camera_ref": "1", "start_epoch": base, "end_epoch": base + 30},
    ]}))
    doc = yaml.safe_load((cdir / "roles.yaml").read_text())
    doc["roles"]["motion_source"]["events_file"] = str(events)
    (cdir / "roles.yaml").write_text(yaml.safe_dump(doc))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    rc = main(["motion-probe", "2026-07-21", "2026-07-22"])
    out = capsys.readouterr().out
    assert rc == 0 and "cam 1" in out and "1 window(s)" in out
