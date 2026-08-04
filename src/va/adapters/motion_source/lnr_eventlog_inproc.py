"""LNR608 vendor event-log MotionSource (WS-4; proven access: nvr-access-notes.md §5b).

Queries the NVR's log by time range (the mechanism that WORKS on this 2015
firmware — live push `attach` is dead) via the stateful Dahua CGI flow:

    log.cgi?action=startFind&condition.StartTime=...&condition.EndTime=...  -> token
    log.cgi?action=doFind&token=T&count=100                                  (repeat)
    log.cgi?action=stopFind&token=T

Filtered to `Type=Motion Detect` entries, whose Detail carries the camera
channel and the episode's start/end. Costs bytes of text, never video — the
"don't download footage to find motion" primitive.

Config/credentials: `host` may come from the role spec or VA_NVR_HOST;
credentials come ONLY from the environment (VA_NVR_USER / VA_NVR_PASS — the
admin account; the CGI API is denied to least-priv users). Never put them in a
config file. `tz` (IANA name, default: system local) converts the NVR's local
log timestamps to UTC epochs.

⚠ Channel semantics (notes §5b + §5c): the log's `Channel` is the NVR's
1-indexed DISPLAY camera number. `camera_ref` reports that display number as a
string; the display->API-channel mapping needed by loadfile is NOT 1:1 on this
unit and is applied at the pull step (WS4.c), not here.
"""
from __future__ import annotations

import base64
import logging
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from va.contracts.motion import MotionEvent

logger = logging.getLogger(__name__)

_TIME_FMT = "%Y-%m-%d %H:%M:%S"


def _parse_items(text: str) -> list[dict[str, str]]:
    """Dahua CGI key=value lines -> one dict per items[i] index.

    LIVE-VALIDATED against the LNR608 (WS4.a2, 2026-08-03): this firmware's
    `Detail` value is MULTI-LINE ("Event Type:Motion Detect\\nChannel:1\\n
    End Time:..."), so continuation lines (not matching `items[i].key=` and not
    a bare `key=value` header like `found=12`) are appended to the last seen
    item key. Dotted subkeys and flat single-line Details (other firmwares in
    the family) still parse.
    """
    items: dict[int, dict[str, str]] = {}
    cur: Optional[tuple[int, str]] = None
    for line in text.splitlines():
        m = re.match(r"items\[(\d+)\]\.(.+?)=(.*)$", line.strip())
        if m:
            idx, key, val = int(m.group(1)), m.group(2), m.group(3)
            items.setdefault(idx, {})[key] = val
            cur = (idx, key)
            continue
        if re.match(r"\w[\w.]*=", line.strip()):
            cur = None          # a top-level field (found=12, token=...) ends the value
            continue
        if line.strip() and cur is not None:
            idx, key = cur      # continuation of a multi-line value
            items[idx][key] += "\n" + line.strip()
    return [items[i] for i in sorted(items)]


def _detail_field(item: dict[str, str], name: str) -> Optional[str]:
    """A Detail field by name, from either the dotted or the flat shape."""
    for key, val in item.items():
        if key.lower() == f"detail.{name.lower()}".replace(" ", ""):
            return val.strip()
        if key.lower().startswith("detail.") and key.lower().endswith(name.lower()):
            return val.strip()
    # One rule for both shapes, applied per Detail LINE: the live LNR608 emits
    # one field per line ("Channel:1"); other firmwares pack several into one
    # line ("Channel No.: 2 Start Time: ..."). Label may carry decoration; the
    # value runs until the next Capitalized-label colon or line end (times' own
    # colons follow digits, so they never match the label lookahead).
    for line in item.get("Detail", "").splitlines():
        m = re.search(rf"{name}[\w .]*?:\s*([^,;|]+?)(?=\s+[A-Z][\w .]*:|$)", line)
        if m:
            return m.group(1).strip()
    return None


class LnrEventLogMotionSource:
    def __init__(self, load: dict | None = None):
        load = load or {}
        self.host = (load.get("host") or os.environ.get("VA_NVR_HOST") or "").rstrip("/")
        self.user = os.environ.get("VA_NVR_USER", "")
        self.password = os.environ.get("VA_NVR_PASS", "")
        tz_name = load.get("tz") or os.environ.get("VA_NVR_TZ")
        # None = "system local RULES" (DST-aware per timestamp). Capturing
        # datetime.now().astimezone().tzinfo here would freeze TODAY's UTC
        # offset and shift every window an hour in the opposite DST phase.
        self.tz: Optional[ZoneInfo] = ZoneInfo(tz_name) if tz_name else None
        if not self.host:
            raise ValueError(
                "LNR event-log motion source needs the NVR host: set `host:` in the "
                "motion_source role spec or VA_NVR_HOST (e.g. http://10.0.0.64)")
        if not (self.user and self.password):
            raise ValueError(
                "LNR event-log motion source needs credentials via VA_NVR_USER / "
                "VA_NVR_PASS (the admin account — the CGI API is denied to "
                "least-priv users). Never put them in a config file.")

    # --- device I/O (isolated so tests can stub it) -----------------------
    def _get(self, path_qs: str) -> str:
        url = f"{self.host}/cgi-bin/{path_qs}"
        req = urllib.request.Request(url)
        token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 — LAN device
            return resp.read().decode("utf-8", errors="replace")

    def _local(self, epoch: float) -> str:
        if self.tz is not None:
            return datetime.fromtimestamp(epoch, self.tz).strftime(_TIME_FMT)
        return datetime.fromtimestamp(epoch).astimezone().strftime(_TIME_FMT)

    def _epoch(self, local_str: str) -> Optional[float]:
        try:
            naive = datetime.strptime(local_str, _TIME_FMT)
        except ValueError:
            return None
        if self.tz is not None:
            return naive.replace(tzinfo=self.tz).timestamp()
        return naive.astimezone().timestamp()  # system local, DST-aware per date

    def events(
        self,
        start_epoch: float,
        end_epoch: float,
        camera_ref: Optional[str] = None,
    ) -> List[MotionEvent]:
        # quote_via=quote: spaces must encode as %20 (the proven-working form,
        # notes §5b) — this 2015-era embedded CGI may not decode '+' as space.
        q = urllib.parse.urlencode({
            "action": "startFind",
            "condition.StartTime": self._local(start_epoch),
            "condition.EndTime": self._local(end_epoch),
        }, quote_via=urllib.parse.quote)
        started = self._get(f"log.cgi?{q}")
        # Prefer an explicit token= field; an HTTP-200 error body ("Error\ncode=287")
        # must raise, not have its digits mistaken for a token and read as 0 motion.
        m = re.search(r"token=(\d+)", started)
        if m is None and re.search(r"\berror\b", started, re.IGNORECASE):
            raise RuntimeError(f"log.cgi startFind returned an error: {started[:200]!r}")
        if m is None:
            m = re.fullmatch(r"\s*(?:result=)?(\d+)\s*", started)
        if m is None:
            raise RuntimeError(f"log.cgi startFind returned no token: {started[:200]!r}")
        token = m.group(1)
        out: List[MotionEvent] = []
        # Start markers awaiting their End marker, per channel (live-validated
        # episode pairing — see the loop below).
        open_by_chan: dict[str, tuple[Optional[float], dict]] = {}
        pages = 0
        prev_page: Optional[str] = None
        try:
            while True:
                page = self._get(f"log.cgi?action=doFind&token={token}&count=100")
                items = _parse_items(page)
                if not items and re.search(r"\berror\b", page, re.IGNORECASE):
                    # Same principle as startFind: an HTTP-200 error body (e.g. the
                    # token expired mid-pagination, Dahua code=287) must RAISE —
                    # treating it as an empty page would silently under-report the
                    # motion log and WS4.b would never pull that footage.
                    raise RuntimeError(f"log.cgi doFind returned an error: {page[:200]!r}")
                # Runaway guards (cursor advance is unverified on this firmware):
                # a repeating page or an absurd page count must stop the loop, not
                # hammer the NVR and duplicate events unboundedly. Fingerprint the
                # WHOLE page — duplicate log rows straddling a page boundary make
                # first-item fingerprints falsely trip and drop the day's tail.
                pages += 1
                if items and page == prev_page:
                    logger.warning("log.cgi doFind repeated a page — cursor not advancing; stopping")
                    break
                if pages > 500:  # 50k entries — far beyond any real day's log
                    logger.warning("log.cgi doFind exceeded 500 pages — stopping")
                    break
                prev_page = page
                for item in items:
                    if "motion" not in item.get("Type", "").lower():
                        continue  # the log also holds login/logout/etc entries
                    chan = _detail_field(item, "Channel") or ""
                    chan = re.sub(r"\D", "", chan)  # "Channel No.: 2" -> "2"
                    if camera_ref is not None and chan != camera_ref:
                        continue
                    start_s = _detail_field(item, "Start Time")
                    end_s = _detail_field(item, "End Time")
                    attrs = {"type": item.get("Type", ""),
                             "log_time": item.get("Time", "")}
                    # LIVE-VALIDATED episode semantics (WS4.a2): this firmware logs
                    # an episode as TWO entries — a Start Time marker and a later
                    # End Time marker — so pair them per channel. Entries carrying
                    # both in one Detail (other firmwares) emit a window directly.
                    if start_s and end_s:
                        self._emit(out, chan, start_s, end_s, attrs,
                                   start_epoch, end_epoch)
                    elif start_s:
                        displaced = open_by_chan.get(chan)
                        if displaced is not None and displaced[0] is not None:
                            # Start,Start on one channel (End marker lost to a log
                            # wrap / NVR reboot): emit the displaced episode as a
                            # start-anchored instant, like the range-end flush —
                            # never silently drop it.
                            self._emit(out, chan, None, None,
                                       {**displaced[1], "open": True},
                                       start_epoch, end_epoch,
                                       start=displaced[0], end=displaced[0])
                        open_by_chan[chan] = (self._epoch(start_s), attrs)
                    elif end_s:
                        e = self._epoch(end_s)
                        if e is None:
                            # Parse BEFORE popping: a bad End Time must not discard
                            # the paired open Start (the range-end flush emits it).
                            logger.warning("unparseable End Time %r — skipped", end_s)
                            continue
                        opened = open_by_chan.pop(chan, None)
                        b = opened[0] if opened and opened[0] is not None else e
                        self._emit(out, chan, None, None, attrs,
                                   start_epoch, end_epoch, start=b, end=e)
                    else:
                        # neither marker: an instant entry at the log time
                        self._emit(out, chan, item.get("Time", ""), None, attrs,
                                   start_epoch, end_epoch)
                if not items:
                    # Stop only on an EMPTY page: assuming the device always fills
                    # count=100 per page would silently drop the day's tail if this
                    # firmware caps responses lower. doFind advances its cursor per
                    # token, so re-calling until empty is safe.
                    break
        finally:
            try:
                self._get(f"log.cgi?action=stopFind&token={token}")
            except Exception:  # noqa: BLE001 — best-effort cleanup of the find token
                logger.warning("log.cgi stopFind failed for token %s", token)
        # Episodes still open at range end (their End marker lies beyond the
        # queried window): emit as start-anchored instants; the consumer may pad.
        for chan, (b, attrs) in open_by_chan.items():
            if b is not None:
                self._emit(out, chan, None, None, {**attrs, "open": True},
                           start_epoch, end_epoch, start=b, end=b)
        out.sort(key=lambda e: e.start_epoch)
        return out

    def _emit(self, out: List[MotionEvent], chan: str,
              start_s: Optional[str], end_s: Optional[str], attrs: dict,
              range_start: float, range_end: float,
              start: Optional[float] = None, end: Optional[float] = None) -> None:
        if start is None:
            start = self._epoch(start_s or "")
        if start is None:
            logger.warning("unparseable motion entry time %r — skipped", start_s)
            return
        if end is None:
            end = self._epoch(end_s) if end_s else start
            end = end if end is not None else start
        # Client-side overlap filter (the Protocol's contract, like the sidecar):
        # never trust the device's range semantics — quirky inclusive filtering
        # would make WS4.b/c pull footage for windows nobody asked for.
        if end < range_start or start > range_end:
            return
        out.append(MotionEvent(camera_ref=chan, start_epoch=start,
                               end_epoch=end, kind="motion", attributes=attrs))
