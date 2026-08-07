"""Catch-up watcher (WS6.b, plan §3.5) — the A-LSSRVF orchestrator.

Per camera: read the durable watermark (`cameras.last_processed_epoch`), ask
the configured MotionSource what moved in `[watermark, now - settle]`, cluster
the events into episodes, pull+ingest each episode as an `nvr://` window, and
advance the watermark as each episode lands. Everything is idempotent — the
nvr source_key dedups a re-pulled window, and the watermark only moves
forward — so a crash mid-cycle simply resumes on the next run, and a restart
after an outage backfills exactly the gap.

Bounds (structure/budget knobs, not content):
- `lookback_s` caps how far a NEVER-watched camera reaches back (a NULL
  watermark must not slurp the NVR's whole ring buffer on first run).
- `settle_s` keeps the query horizon behind `now` — the newest episodes may
  still be OPEN in the recorder's log.
- `max_windows` caps pulls per cycle, so one giant backlog cannot starve the
  other cameras; the watermark then rests at the last ingested episode and
  the next cycle continues from there.
- Episodes longer than the nvr source's window cap are split into
  back-to-back windows (each its own idempotent chunk).
- `gap_s` clusters raw motion events into PULL episodes — independent of the
  scene_detector spec's same-named knob, which segments WITHIN a chunk at
  ingest time. `open_instant_max_age_s` bounds how long an open (lost-End)
  instant may defer the watermark before the recovery pull kicks in.

SLA note (§8.2): the LNR608 keeps ~6 days of footage — an outage longer than
the ring buffer is unrecoverable no matter the watermark; the watcher pulls
what the recorder still holds and moves on.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from va.registry import get_motion_source
from va.roles.motion_source import cluster_events
from va.sources.nvr import MAX_WINDOW_S

logger = logging.getLogger(__name__)


@dataclass
class CameraReport:
    camera_id: str
    windows_ingested: int = 0
    windows_failed: int = 0
    watermark_before: Optional[float] = None
    watermark_after: Optional[float] = None
    truncated: bool = False   # hit max_windows; more remains for the next cycle


@dataclass
class CatchUpReport:
    cameras: List[CameraReport] = field(default_factory=list)

    @property
    def windows_ingested(self) -> int:
        return sum(c.windows_ingested for c in self.cameras)


def _iso_utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00")


def _window_uri(source_ref: str, start: float, end: float) -> str:
    """Format a pull window, FLOORing start and CEILing end to whole seconds:
    the nvr URI carries second precision, and truncating both ends of a
    sub-second event (a fractional-epoch MotionSource) would yield start==end
    — rejected by the parser on every retry, wedging the camera (round-5
    review). Widening by <1 s is harmless; the source_key stays stable."""
    import math

    lo = math.floor(start)
    hi = max(math.ceil(end), lo + 1)
    # Independent floor/ceil can widen a SPLIT window to cap+1 seconds, which
    # parse_nvr_uri hard-rejects — the identical URI then fails on every
    # retry, wedging the watermark (rebase-review major). Clamping is lossless
    # for split windows (the next window's floored start is exactly
    # lo+MAX_WINDOW_S) and loses <1 s on an unsplit tail — within the stated
    # widening tolerance.
    hi = min(hi, lo + MAX_WINDOW_S)
    return f"nvr://{source_ref}/{_iso_utc(lo)}/{_iso_utc(hi)}"


def _episode_windows(start: float, end: float) -> List[tuple[float, float]]:
    """Split an episode into back-to-back windows within the nvr pull cap."""
    out = []
    t = start
    while t < end:
        out.append((t, min(end, t + MAX_WINDOW_S)))
        t += MAX_WINDOW_S
    return out


def catch_up(
    workdir: str,
    camera_ids: Optional[List[str]] = None,
    lookback_s: float = 3600.0,
    settle_s: float = 120.0,
    max_windows: int = 50,
    gap_s: float = 30.0,
    open_instant_max_age_s: float = 600.0,
    now_epoch: Optional[float] = None,
    cfg=None,
) -> CatchUpReport:
    """One catch-up pass over the registered cameras. Returns a report."""
    from va.configuration import load_config
    from va.pipeline.ingest import ingest
    from va.pipeline.paths import Workspace
    from va.storage.structured.cameras import CameraStore

    cfg = cfg or load_config()
    now = time.time() if now_epoch is None else now_epoch
    horizon = now - settle_s
    report = CatchUpReport()

    store = CameraStore(Workspace(workdir).catalog_db)
    try:
        cameras = [c for c in store.list() if c.source_ref]
        if camera_ids is not None:
            known = {c.id for c in cameras}
            for cid in camera_ids:
                if cid not in known:
                    logger.warning(
                        "catch-up: requested camera %r is not registered (or "
                        "has no source_ref) — it will NOT be watched", cid)
            cameras = [c for c in cameras if c.id in camera_ids]
    finally:
        store.close()
    if not cameras:
        logger.warning(
            "catch-up: no cameras with a source_ref registered in %s — ingest "
            "one nvr:// window (or add a camera row) to register one", workdir)
        return report

    source = get_motion_source(cfg)
    # Per-camera budget SPLIT: a shared pool in fixed camera order would let
    # one deeply backlogged camera exhaust every pass while the others' oldest
    # footage ages out of the ~6-day ring (round-4 review). Each camera gets
    # an equal share (at least 1); unspent shares are not redistributed — the
    # next pass comes soon enough.
    per_camera_budget = max(1, max_windows // max(1, len(cameras)))

    for cam in cameras:
        budget = per_camera_budget
        crep = CameraReport(camera_id=cam.id,
                            watermark_before=cam.last_processed_epoch)
        report.cameras.append(crep)
        watermark = cam.last_processed_epoch
        if watermark is None:
            watermark = now - lookback_s
        if horizon <= watermark:
            crep.watermark_after = cam.last_processed_epoch
            continue

        try:
            events = source.events(watermark, horizon,
                                   camera_ref=cam.source_ref)
        except Exception:  # noqa: BLE001 — one camera's flaky source must not
            logger.warning("catch-up: motion query failed for camera %s — "
                           "will retry next cycle", cam.id, exc_info=True)
            crep.watermark_after = cam.last_processed_epoch
            continue
        # An episode STRADDLING the watermark was already covered by the cycle
        # that set the watermark to its end — only genuinely new starts count.
        events = [e for e in events if e.start_epoch >= watermark]

        # Deferral is decided on RAW events BEFORE clustering: cluster_events
        # keeps only the FIRST event's attributes, so an `open` instant
        # merging onto a preceding closed event (gap <= gap_s is the
        # chatty-log NORM) would lose its open/zero-length signature, read as
        # settled, and let the watermark advance past the still-running
        # episode — losing it forever (round-3 critical).
        deferred_start: Optional[float] = None
        pullable = []
        for e in events:
            open_instant = (bool(e.attributes.get("open"))
                            or e.end_epoch <= e.start_epoch)
            if (open_instant
                    and horizon - e.start_epoch > open_instant_max_age_s):
                # A LOST-END artifact (NVR reboot/log wrap eats the End
                # marker): it re-emits on every query and would wedge this
                # camera's watermark forever (round-2 review). Recover what we
                # can — one padded window from its start — and move on, loudly.
                logger.warning(
                    "catch-up: open motion instant at %.0f on camera %s is "
                    ">%.0fs old (lost End marker?) — pulling one %.0fs window "
                    "and advancing past it", e.start_epoch, cam.id,
                    open_instant_max_age_s, float(MAX_WINDOW_S))
                pullable.append(e.model_copy(update={
                    "end_epoch": min(e.start_epoch + MAX_WINDOW_S, horizon),
                    "attributes": {**e.attributes, "open": False},
                }))
                continue
            if open_instant or e.end_epoch >= horizon:
                deferred_start = (e.start_epoch if deferred_start is None
                                  else min(deferred_start, e.start_epoch))
                continue
            pullable.append(e)
        settled = cluster_events(pullable, gap_s=gap_s)

        crep.watermark_after = cam.last_processed_epoch
        aborted = False
        for ep in settled:
            ok = True
            for w_start, w_end in _episode_windows(ep.start_epoch,
                                                   ep.end_epoch):
                if budget <= 0:
                    # The cap binds INSIDE an episode too — one giant
                    # clustered episode must not ignore it (round-1 major).
                    # Watermark stays put; the re-pulls next pass dedup.
                    crep.truncated = True
                    ok = False
                    break
                uri = _window_uri(cam.source_ref, w_start, w_end)
                try:
                    res = ingest(uri, workdir=workdir)
                except Exception:  # noqa: BLE001 — leave the watermark at the
                    # last COMPLETE episode; this one retries next cycle.
                    logger.warning("catch-up: ingest failed for %s — watermark "
                                   "held, will retry next cycle", uri,
                                   exc_info=True)
                    crep.windows_failed += 1
                    ok = False
                    break
                if not res.deduped:
                    # A deduped replay (truncated episode's early windows on
                    # the next pass) is free — charging it would pin a giant
                    # episode at its first `max_windows` chunks forever.
                    crep.windows_ingested += 1
                    budget -= 1
            if not ok:
                aborted = True
                break
            # NEVER advance past a deferred episode's start — a settled episode
            # ordered after it would otherwise push the watermark beyond it and
            # the deferred one fails the start-filter forever (round-2 major;
            # the re-pull of this settled episode next pass dedups for free).
            advance_to = (ep.end_epoch if deferred_start is None
                          else min(ep.end_epoch, deferred_start))
            if advance_to > (crep.watermark_after or watermark):
                _advance(workdir, cam.id, advance_to)
                crep.watermark_after = advance_to
        if not aborted and not crep.truncated:
            # Quiet remainder advances to the horizon — but never past a
            # deferred (unsettled) episode's start.
            target = horizon if deferred_start is None \
                else min(horizon, deferred_start)
            if target > (cam.last_processed_epoch or watermark):
                _advance(workdir, cam.id, target)
                crep.watermark_after = target
    return report


def _advance(workdir: str, camera_id: str, epoch: float) -> None:
    from va.pipeline.paths import Workspace
    from va.storage.structured.cameras import CameraStore

    store = CameraStore(Workspace(workdir).catalog_db)
    try:
        store.set_watermark(camera_id, epoch)
    finally:
        store.close()


def run_watch(
    workdir: str,
    interval_s: float = 60.0,
    stop_after: Optional[int] = None,
    **kwargs,
) -> None:
    """The long-running loop: one catch_up pass every `interval_s`.
    `stop_after` bounds the number of passes (tests/one-shot); None = forever."""
    passes = 0
    while True:
        try:
            report = catch_up(workdir, **kwargs)
            if report.windows_ingested:
                logger.info("catch-up: ingested %d window(s) across %d "
                            "camera(s)", report.windows_ingested,
                            len(report.cameras))
        except Exception:  # noqa: BLE001 — a transient failure (locked DB,
            # flaky device) must not kill the unattended daemon (round-1
            # review); the durable watermark makes the next pass safe.
            logger.warning("catch-up pass failed — retrying next interval",
                           exc_info=True)
        passes += 1
        if stop_after is not None and passes >= stop_after:
            return
        time.sleep(interval_s)
