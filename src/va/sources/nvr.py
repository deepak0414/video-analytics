"""NVR recorded-window source (WS4.c) — pull a wall-clock window from the recorder.

URI form:  nvr://<channel>/<start>/<end>
           nvr://1/2026-08-01T12:00:00/2026-08-01T12:00:30

`<channel>` is the NVR's 1-indexed DISPLAY channel number (the same ref
MotionEvents carry). Times are ISO-8601; naive times are interpreted in the
NVR's clock timezone (`VA_NVR_TZ`, else the system-local rules, DST-aware —
the same convention as the lnr-eventlog MotionSource).

The pull is one loadfile session for a PADDED window [start - PAD_PRE,
end + PAD_POST], then a PTS cut to the exact requested window, then a
DELIVERY-VERIFICATION gate. The pull is deterministic (the same window pulled
twice measured byte-identical, and a full-resolution PTS cut of a 20 s target
measured exactly 20.0 s), but determinism is not correctness: a forensic census
of the `.va-24h` backfill (`va-24h-data-integrity-investigation.md`) proved the
pre-pad does NOT reliably absorb the §5d seek lead-in. The recorder's time-seek
intermittently prepends a fragment of STALE RING-BUFFER content — ~1 ring cycle
(~7 days) old and belonging to whichever camera occupied that disk region — and
it lands at t=0 of the CUT, not only of the padded fetch. Across the census 71
of 238 ingested clips (30%) carried a cross-camera head and 4 more were a wholly
foreign low-rate sub-stream; every one passed the duration check. The earlier
"7/7 windows clean behind the pad" validation was blind: it hashed
`ffmpeg -vf fps=4` samples, whose first output frame is not the file's first
frame (measured 28-39 dHash from the true t=0), so a 1-5-frame head was never
in what it inspected.

So window identity is VERIFIED, not trusted. After the cut, `_verify_and_trim`
extracts delivery signals and runs the source-agnostic pure verifier
(`sources/verify.py`): (1) the TRUE first frames (decoded by index, not by an
fps filter) are perceptual-hashed against the clip's own body — a cross-camera
lead-in reads far and is TRIMMED; (2) the delivered resolution/fps must match
the channel's configured main-stream profile (`VA_NVR_MAIN_STREAM`) — a
sub-stream substitution is REJECTED; (3) where a burned-in-clock reader is
injected, a frame whose painted wall-clock is off from (start_epoch + t) is
wrong-time footage and is trimmed. A head that fails a check that RAN and cannot
be cleanly trimmed, or a delivery that fails whole-stream identity, RAISES — the
pull fails closed rather than ingest it (the retry/exact-window-fallback
machinery below runs first; only when it is exhausted does the pull raise).

COVERAGE — what this gate does and does NOT catch (be honest, it is
defence-in-depth, not a cure):
  - Cross-camera heads (census: 68/92 head lead-ins at dHash >= 24): TRIMMED by
    the self-referential head check.
  - Sub-stream substitution (census: 4 clips at 352x240): REJECTED — but ONLY
    when VA_NVR_MAIN_STREAM is configured; unset, that check is inactive.
  - Same-camera WRONG-WEEK footage (census: ~25 clips "right camera, wrong week",
    plus 21/92 heads that were the same view a different day at dHash <= 18, i.e.
    BELOW the identity band by construction): NOT caught by the OCR-free defences
    — the head reads close to the body because it IS the same camera. Only the
    burned-in-clock gate catches this class, and NO default TimestampReader ships
    yet (the gate is a tested, injectable seam). Per the census this is mandatory
    item 1 before a clean re-pull; until a reader is wired, a same-camera
    wrong-week head can still ingest. Do not read this gate as "contamination
    solved".

The gate replaces the removed consensus-dHash `ReferenceLibrary` (dropped in
PR #40 — it was lighting-dependent and false-refused correct dusk footage). The
new head check is SELF-referential (head vs the same clip's body) and so needs
no persisted per-channel library and no lighting-matched reference; a
`<workdir>/nvr_refs/` directory is vestigial and can be deleted.

A clean no-seek whole-file read (RPC_Loadfile / loadfile by fileName), which
would avoid the seek entirely, is blocked on this 2017 firmware (every form
returns "Invalid Request"/400) — hence pull-by-time + client-side cut.

Malformed pulls are caught by a deterministic sanity check instead: the cut
must decode and its duration must land within DURATION_TOL_S of the requested
window; a failure re-fetches and re-cuts up to MAX_TRIES. At the ~6-day ring
edge (an outage backfill — the watcher's "pull what remains" case) the pre-pad
can predate the oldest surviving footage even though [start, end] survives, so
a second phase then re-pulls the EXACT window with no pad (aligned by
construction, purity 1.000 measured); only if BOTH phases fail does the pull
raise — fail closed, never a silently short clip. A persistently unpullable
window still holds its camera's `va watch` watermark (narrowed, not removed, by
the fallback). See `_pull_window`.

Connection settings come from the environment ONLY (mirroring the lnr
adapter): `VA_NVR_HOST` (e.g. "http://10.0.0.64"), `VA_NVR_USER`,
`VA_NVR_PASS`. Credentials never live in config files. Device transfers shell
out to `curl` (`--anyauth` negotiates; these CGI endpoints are Basic-ONLY per
nvr-access-notes.md — do NOT harden to `--digest`, it silently 401s — the
recipe's proven transport); ffmpeg is the imageio-ffmpeg bundled binary.

Dedup identity: `source_key = nvr:ch<channel>:<start_epoch>-<end_epoch>` — a
re-pull of the same window on the same channel is the same video.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from va.contracts.video import Camera, ResolvedVideo, SourceType
from va.sources.verify import (
    DeliveryRejected,
    DeliveryVerdict,
    ExpectedProfile,
    HeadFrameSignal,
    ObservedSignals,
    RequestedWindow,
    dhash,
    hamming,
    verify_delivery,
)

logger = logging.getLogger(__name__)

_URI_RE = re.compile(
    r"^nvr://(?P<chan>\d+)/(?P<start>[^/]+)/(?P<end>[^/]+)$"
)

# Structure/budget knobs for the pull (not content):
MAX_WINDOW_S = 120    # refuse absurd windows; motion episodes are ~30-70 s
MAX_TRIES = 4         # attempts (both fetch and whole pull+cut) before failing
# The pads are structural budgets, justified by measurement, not guesses: the
# §5d seek lead-in measured ~1-2 s (and single-request purity was 1.000 over
# 300+ prior pulls), so 10 s of pre-pad is a wide margin over the worst case
# observed. The pad is DISCARDED by the PTS cut, so even a pre-pad that
# crosses into foreign buffered footage cannot contaminate the delivered
# window — over-padding costs only a few seconds of transfer.
PAD_PRE = 10          # s fetched before `start`; absorbs the seek lead-in
PAD_POST = 2          # s fetched after `end`; keeps the cut's right edge off
                      # the stream tail (loadfile t=0 aligns only to ~1 s)
DURATION_TOL_S = 2.0  # the cut must land within this of the requested window

# Delivery-verification budgets (structure, not device content — the expected
# main-stream resolution/fps is CONTENT and comes from VA_NVR_MAIN_STREAM, never
# baked in). Calibrated against the census bands in
# `va-24h-data-integrity-investigation.md`:
HEAD_FRAMES = 8       # true first frames inspected; the census head was 1-5 frames
IDENTITY_MAX_DHASH = 20   # head-vs-body dHash: same-camera <=18, cross-camera >=24
CLOCK_TOL_S = 5.0     # burned-in clock skew that still counts as aligned
MIN_KEPT_S = 1.0      # a head trim must leave at least this much footage


def _tz():
    name = os.environ.get("VA_NVR_TZ")
    if name:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    return None  # system-local rules, DST-aware per date


def parse_nvr_uri(uri: str) -> Tuple[int, datetime, datetime]:
    """-> (channel, start, end) as tz-AWARE datetimes. Raises ValueError on junk."""
    m = _URI_RE.match(uri.strip())
    if not m:
        raise ValueError(
            f"bad NVR uri {uri!r} — expected nvr://<channel>/<start>/<end> "
            "with ISO-8601 times, e.g. nvr://1/2026-08-01T12:00:00/2026-08-01T12:00:30"
        )
    chan = int(m.group("chan"))
    times = []
    for s in (m.group("start"), m.group("end")):
        try:
            dt = datetime.fromisoformat(s)
        except ValueError as e:
            raise ValueError(f"bad NVR uri time {s!r}: {e}") from e
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz()).astimezone(timezone.utc) if _tz() \
                else dt.astimezone()  # naive -> local rules
        times.append(dt.astimezone(timezone.utc))
    start, end = times
    if not end > start:
        raise ValueError(f"bad NVR uri window: end {end} not after start {start}")
    if (end - start).total_seconds() > MAX_WINDOW_S:
        raise ValueError(
            f"NVR window {(end - start).total_seconds():.0f}s exceeds "
            f"{MAX_WINDOW_S}s — pull motion episodes, not raw hours"
        )
    return chan, start, end


def _parse_main_stream(spec: Optional[str]) -> Optional[frozenset]:
    """Parse VA_NVR_MAIN_STREAM ("2688x1520@20", or several comma-separated) into
    a frozenset of expected main-stream PROFILES, each a (w, h, fps_or_None)
    tuple, for stream-identity verification. Each entry's resolution and fps stay
    PAIRED — a multi-entry install ("2688x1520@20,1920x1080@15") must not let a
    2688x1520@15 wrong feed cross-match. Unset/blank -> None = the whole-stream
    check is inactive. Device CONTENT lives here in the environment, never as a
    baked-in constant."""
    if not spec or not spec.strip():
        return None
    profiles = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        res, _, fps = token.partition("@")
        w, _, h = res.strip().lower().partition("x")
        try:
            wh = (int(w), int(h))
        except ValueError as e:
            raise ValueError(
                f"bad VA_NVR_MAIN_STREAM resolution {res!r} in {token!r} — "
                "expected WxH[@fps], e.g. 2688x1520@20"
            ) from e
        f = None
        if fps.strip():
            try:
                f = int(round(float(fps)))
            except ValueError as e:
                raise ValueError(
                    f"bad VA_NVR_MAIN_STREAM fps {fps!r} in {token!r}"
                ) from e
        profiles.add((wh[0], wh[1], f))
    if not profiles:
        # Non-blank but no usable entries (e.g. a stray ","): raise, don't return
        # None — a silent None would DEACTIVATE the gate on a config typo (fail
        # open). Unset/blank is the only inactive case, handled above.
        raise ValueError(
            f"VA_NVR_MAIN_STREAM={spec!r} parsed to no profiles — expected "
            "WxH[@fps] entries, e.g. 2688x1520@20"
        )
    return frozenset(profiles)


class NvrRecordedSource:
    def __init__(self, verifier=verify_delivery, timestamp_reader=None):
        """`verifier` and `timestamp_reader` are the injectable delivery-
        verification seam (sources/verify.py). The default verifier is the pure
        `verify_delivery`; `timestamp_reader` is the burned-in-clock extractor —
        None (the default) leaves the OCR-dependent clock gate inactive while the
        OCR-free head-identity and stream-identity defences still run."""
        self._verifier = verifier
        self._timestamp_reader = timestamp_reader

    def resolve(self, uri: str) -> ResolvedVideo:
        chan, start, end = parse_nvr_uri(uri)
        s, e = int(start.timestamp()), int(end.timestamp())
        # Canonical URI is fully-qualified UTC: a NAIVE input time depends on
        # VA_NVR_TZ / the system zone at resolve time, so storing it verbatim
        # would make the stored row's identity environment-dependent — a later
        # `va reingest` in a different shell would resolve a different
        # source_key and miss the row (round-5 review finding). The canonical
        # form re-resolves identically in any environment.
        canonical = (f"nvr://{chan}/{start:%Y-%m-%dT%H:%M:%S}+00:00/"
                     f"{end:%Y-%m-%dT%H:%M:%S}+00:00")
        return ResolvedVideo(
            source_type=SourceType.nvr_recorded,
            source_uri=canonical,
            source_key=f"nvr:ch{chan}:{s}-{e}",
            start_epoch=float(s),
            camera=Camera(
                # Stable internal id per channel; the display name is the
                # user-renamable part (WS3.c decision) — get_or_create never
                # clobbers an existing row's name.
                id=f"nvr-ch{chan}",
                name=f"NVR channel {chan}",
                source_ref=str(chan),
            ),
        )

    def fetch(self, resolved: ResolvedVideo, cache_dir) -> ResolvedVideo:
        from va.media.frames import probe

        chan, start, end = parse_nvr_uri(resolved.source_uri)
        cache = Path(cache_dir)
        cache.mkdir(parents=True, exist_ok=True)
        out = cache / f"{resolved.source_key.replace(':', '_')}.mp4"
        if not out.exists():
            self._pull_window(chan, start, end, out)   # pulls AND verifies
        else:
            # An existing cache file may PREDATE the delivery-verification gate:
            # pulled by older code, or preserved across a `va reingest` (manage.py
            # moves the kept clip back to this exact path). Verify it once before
            # trusting it, so an upgrade cannot re-ingest an unverified clip — a
            # trim replaces it in place.
            try:
                verified = self._verify_and_trim(out, chan, start, end)
                if Path(verified) != out:
                    os.replace(verified, out)
            except DeliveryRejected as exc:
                # A verified-bad cache clip must NOT dead-end the window: left in
                # place it fails identically on every retry while clean footage
                # may still be on the ring. Set it aside and re-pull (which
                # re-verifies, and fails closed if the window is genuinely gone).
                aside = out.with_suffix(".rejected.mp4")
                os.replace(out, aside)
                logger.warning(
                    "nvr ch%d: cached clip failed delivery verification (%s) — "
                    "set aside as %s and re-pulling", chan, exc, aside.name)
                self._pull_window(chan, start, end, out)
        meta = probe(str(out))
        if meta.title is None:
            meta.title = (f"nvr ch{chan} "
                          f"{start.astimezone():%Y-%m-%d %H:%M:%S}")
        return resolved.model_copy(update={"local_path": str(out), "metadata": meta})

    # ---- device layer (everything below talks to the NVR; tests stub it) ----

    @staticmethod
    def _conn() -> Tuple[str, str, str]:
        host = os.environ.get("VA_NVR_HOST")
        user = os.environ.get("VA_NVR_USER")
        password = os.environ.get("VA_NVR_PASS")
        if not (host and user and password):
            raise RuntimeError(
                "NVR pull needs VA_NVR_HOST + VA_NVR_USER + VA_NVR_PASS in the "
                "environment (credentials never live in config files — map them "
                "from your secrets store into the environment)"
            )
        return host.rstrip("/"), user, password

    @staticmethod
    def _ffmpeg() -> str:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()

    @staticmethod
    def _curl_argv(url: str, out: str, max_time: int) -> List[str]:
        """Credentials travel via `--config -` on STDIN — never on the command
        line, where any local user could read them out of the process list
        during a pull (round-2 review finding)."""
        return ["curl", "-g", "--anyauth", "--config", "-",
                "--max-time", str(max_time), "-s", "-o", out, url]

    @staticmethod
    def _curl_config(user: str, password: str) -> str:
        """The stdin config line. curl's config parser interprets `\\` and `"`
        inside a quoted value, so both must be escaped or a credential
        containing them silently mangles and every transfer fails auth
        (round-3 review finding)."""
        raw = f"{user}:{password}"
        if "\r" in raw or "\n" in raw:
            # A newline would end the quoted value and turn the remainder into
            # a stray config directive — reject loudly instead of 401-ing with
            # a misleading error later.
            raise RuntimeError(
                "VA_NVR_USER/VA_NVR_PASS must not contain newline characters"
            )
        quoted = raw.replace("\\", "\\\\").replace('"', '\\"')
        return f'user = "{quoted}"\n'

    def _curl(self, url: str, out: str, max_time: int = 60) -> int:
        """Returns curl's exit status. Callers that download a payload MUST
        check it: a transfer killed by --max-time (exit 28) leaves a partial
        file that can pass a size gate and ingest as a silently SHORT clip
        (round-1 review minor 2). Best-effort callers (stopLoad) may ignore
        it."""
        _, user, password = self._conn()
        return subprocess.run(
            self._curl_argv(url, out, max_time),
            input=self._curl_config(user, password),
            text=True,
            check=False,
        ).returncode

    def _stop_load(self, chan: int) -> None:
        host, _, _ = self._conn()
        self._curl(f"{host}/cgi-bin/loadfile.cgi?action=stopLoad&channel={chan}",
                   "/dev/null", max_time=15)

    # ---- delivery verification (the §5 census fix) --------------------------

    def _expected_profile(self, chan: int) -> ExpectedProfile:
        """What a correct delivery for this channel looks like. The main-stream
        resolution/fps is device CONTENT and comes from VA_NVR_MAIN_STREAM (never
        baked in); the thresholds are structural budgets (module constants)."""
        stream_profiles = _parse_main_stream(os.environ.get("VA_NVR_MAIN_STREAM"))
        if stream_profiles is None:
            logger.info(
                "nvr ch%d: VA_NVR_MAIN_STREAM unset — stream-identity check "
                "inactive (head-identity%s still run)", chan,
                " + clock" if self._timestamp_reader is not None else "")
        return ExpectedProfile(
            stream_profiles=stream_profiles,
            identity_max_distance=IDENTITY_MAX_DHASH,
            clock_tol_s=CLOCK_TOL_S, min_kept_s=MIN_KEPT_S,
        )

    def _observe(self, cut: Path, requested: RequestedWindow) -> ObservedSignals:
        """Extract delivery signals from a cut clip for the pure verifier.

        The head identity is SELF-referential: the true first frames (decoded by
        index, so the sampler blindness that hid the census heads cannot recur)
        are perceptual-hashed against a frame from the clip's own body. The body
        is the requested camera (contamination is head-confined per the census),
        so a foreign head reads far — no persisted per-channel reference needed."""
        from va.media.frames import first_frames, frames_at, probe

        meta = probe(str(cut))
        resolution = None
        if meta.resolution:
            try:
                w, h = meta.resolution.lower().split("x")
                resolution = (int(w), int(h))
            except ValueError:
                resolution = None
        fps = int(round(meta.fps)) if meta.fps else None

        # Body reference = the clip's own midpoint. Derive it from the clip's
        # ACTUAL decoded duration, not the requested window: a short delivery
        # (or a test stub) would otherwise seek past the last frame.
        dur = meta.duration_seconds or requested.window_len_s
        body_t = min(max(dur / 2.0, 0.0), max(dur - 0.1, 0.0))
        body = frames_at(str(cut), [body_t])
        head: Tuple[HeadFrameSignal, ...] = ()
        if body:
            body_hash = dhash(body[0])
            head = tuple(
                HeadFrameSignal(t=t, distance=hamming(dhash(img), body_hash))
                for t, img in first_frames(str(cut), HEAD_FRAMES)
            )

        clock = ()
        if self._timestamp_reader is not None:
            clock = tuple(self._timestamp_reader.read_head_clock(str(cut), HEAD_FRAMES))

        return ObservedSignals(resolution=resolution, fps=fps, head=head, clock=clock)

    def _verify_and_trim(self, cut: Path, chan: int, start: datetime,
                         end: datetime) -> Path:
        """Verify a cut against the requested window; return an acceptable clip
        (possibly head-trimmed) or raise DeliveryRejected (fail closed).

        Duration is necessary but NOT sufficient — a contaminated clip is exactly
        the requested length. The pure verifier decides over extracted signals;
        this applies the trim and re-verifies once so a trim that didn't clean
        the delivery still fails closed."""
        window_len = (end - start).total_seconds()
        requested = RequestedWindow(
            stream_id=f"nvr-ch{chan}",
            start_epoch=float(int(start.timestamp())),
            window_len_s=window_len,
        )
        expected = self._expected_profile(chan)
        verdict: DeliveryVerdict = self._verifier(
            requested, self._observe(cut, requested), expected)

        if verdict.rejected:
            raise DeliveryRejected("; ".join(verdict.reasons) or "unspecified")
        if verdict.accepted:
            return cut

        # Trim: drop [0, trim_before_s) and re-encode. The delivered clip is now
        # shorter by the (sub-second, per the census) foreign head; its t=0 lags
        # start_epoch by that much — within the documented ~1 s loadfile
        # alignment caveat. Re-deriving start_epoch is a follow-up (it would have
        # to flow back to the catalog row before roles run).
        # Unique per source clip: the pull uses a private temp dir, but a
        # cache-hit verify shares the cache dir across windows — a fixed name
        # would collide between concurrent reingests.
        trimmed = cut.parent / (cut.stem + ".verified.mp4")
        self._trim_encode(cut, verdict.trim_before_s, window_len, trimmed)
        logger.warning(
            "nvr ch%d %s: delivery verification trimmed a foreign head "
            "(t<%.3fs) — %s", chan, start, verdict.trim_before_s,
            "; ".join(verdict.reasons))

        rechecked = RequestedWindow(
            stream_id=requested.stream_id,
            start_epoch=requested.start_epoch + verdict.trim_before_s,
            window_len_s=max(window_len - verdict.trim_before_s, 0.0),
        )
        recheck = self._verifier(rechecked, self._observe(trimmed, rechecked), expected)
        if not recheck.accepted:
            raise DeliveryRejected(
                "head trim did not clean the delivery — "
                + ("; ".join(recheck.reasons) or "still verified-bad"))
        return trimmed

    def _pull_window(self, chan: int, start: datetime, end: datetime,
                     out_mp4: Path) -> None:
        """Pull [start,end) on `chan` into out_mp4 — deterministic pad + PTS cut.

        Runs in up to two phases (validated live 2026-08-12, numbers in the
        module docstring):

        PADDED (normal): fetch [start-PAD_PRE, end+PAD_POST] in ONE loadfile
        session, then PTS-cut to exactly [PAD_PRE, PAD_PRE + window_len] —
        timestamp arithmetic, so the §5d seek lead-in that lands in the pre-pad
        is discarded. This assumes the device actually served footage from
        start-PAD_PRE.

        EXACT-WINDOW (ring-edge fallback): at the ~6-day ring edge — an outage
        backfill, the watcher's "pull what remains" case — the pre-pad can
        predate the oldest surviving footage even though [start,end] itself
        survives, so the padded cut can't land (it fails the duration gate, or
        would silently shift). Then re-fetch [start,end] with NO pad and cut
        [0, window_len]: aligned by construction, and an exact-window request
        measured single-request purity 1.000 over 300+ pulls — the trade is that
        a lead-in, if one occurs, is no longer padded away (rare, and better than
        losing recoverable footage).

        Each phase deterministically sanity-checks the cut: it must decode and
        its duration must be within DURATION_TOL_S of the requested window
        (catches zero-decodable and truncated pulls), retrying the whole pull up
        to MAX_TRIES. If BOTH phases fail the window is genuinely unpullable and
        the pull RAISES — fail closed, never a silently short clip. NB a
        persistently unpullable window still holds this camera's `va watch`
        watermark (watch.py aborts the camera's pass on ingest failure), so a
        window that outlives the ring is lost; that interaction is real, only
        narrowed (not removed) by the exact-window fallback.

        Residual timeline caveat: loadfile aligns t=0 to the requested start
        only to ~1 s normally; a ring-edge padded pull whose head is partially
        available can shift the clip by up to ~PAD_POST+DURATION_TOL_S before the
        exact-window fallback engages (PTS-accurate alignment is backlog).

        All intermediates live in a pull-private temp dir (parallel per-camera
        ingests must not share filenames), and the final mp4 lands via atomic
        rename. fetch() RE-VERIFIES an existing cache file (delivery verification
        is not a torn-write check), but the atomic rename must still hold: a clip
        killed mid-write must never be reusable under the final name.
        """
        self._conn()   # fail fast on missing credentials, before any work
        window_len = (end - start).total_seconds()
        work = Path(tempfile.mkdtemp(dir=out_mp4.parent,
                                     prefix=out_mp4.stem + ".pull-"))
        # (pre-pad, post-pad, label). The cut offset is always the pre-pad, so
        # the exact-window phase (pad 0) cuts [0, window_len].
        phases = ((PAD_PRE, PAD_POST, "padded"),
                  (0.0, 0.0, "exact-window fallback"))
        try:
            for pad_pre, pad_post, label in phases:
                pad_start = start - timedelta(seconds=pad_pre)
                pad_end = end + timedelta(seconds=pad_post)
                failures: List[str] = []
                for attempt in range(MAX_TRIES):
                    raw = self._fetch_window(chan, pad_start, pad_end, work)
                    if raw is None:
                        failures.append("no data")
                        break   # nothing served this phase — try the next
                    part = work / "cut.part.mp4"
                    self._trim_encode(raw, pad_pre, pad_pre + window_len, part)
                    dur = self._probe_cut(part)
                    if dur is None or abs(dur - window_len) > DURATION_TOL_S:
                        failures.append("no decodable frames" if dur is None
                                        else f"{dur:.1f}s")
                        part.unlink(missing_ok=True)
                        Path(raw).unlink(missing_ok=True)   # force a fresh fetch
                        continue
                    # Duration is necessary but NOT sufficient: a contaminated
                    # clip is exactly the requested length. Verify the DELIVERY
                    # against the request and trim/reject a bad head (§5 census
                    # fix). A rejection is treated like a bad cut — retry this
                    # phase, then the exact-window fallback, then fail closed.
                    try:
                        verified = self._verify_and_trim(part, chan, start, end)
                    except DeliveryRejected as exc:
                        failures.append(f"delivery rejected: {exc}")
                        part.unlink(missing_ok=True)
                        Path(raw).unlink(missing_ok=True)
                        continue
                    os.replace(verified, out_mp4)   # atomic: never a torn clip
                    if pad_pre == 0.0:
                        logger.warning(
                            "nvr pull ch%d %s: pre-pad footage unavailable "
                            "(ring edge?) — fell back to an exact-window "
                            "pull; a lead-in, if any, is NOT padded away",
                            chan, start,
                        )
                    return
                logger.warning(
                    "nvr pull ch%d %s: %s phase failed against a %.1fs window "
                    "(%s)", chan, start, label, window_len, ", ".join(failures),
                )
            raise RuntimeError(
                f"NVR pull ch{chan} {start:%Y-%m-%d %H:%M:%S}: neither a padded "
                f"nor an exact-window pull produced a clean ~{window_len:.1f}s "
                f"±{DURATION_TOL_S}s cut — refusing to ingest a malformed pull. "
                f"A persistently unpullable window holds this camera's watch "
                f"watermark until it can be pulled or the ring expires it."
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)
            self._stop_load(chan)

    def _fetch_window(self, chan: int, start: datetime, end: datetime,
                      work: Path) -> Optional[Path]:
        """One loadfile session for the whole window; returns the raw .dav.

        Deliberately NO -c copy remux: on some recordings this NVR's .dav
        demuxes to h264 packets the mpegts muxer rejects ("no startcode
        found", and h264_mp4toannexb does not repair it), while a full DECODE
        of the same file succeeds — measured 2026-08-11 on a window that
        downloaded 47 MB four times only to be reported as "no data" because
        the silent remux discarded it every time. Downstream needs only a
        re-encode (`_trim_encode`), which reads the .dav directly (bundled
        ffmpeg has the dhav demuxer). Seeking on the .dav is exact: a -ss 2
        -to 30 trim measured 28.00 s to the frame."""
        host, _, _ = self._conn()
        local_s = start.astimezone(_tz()) if _tz() else start.astimezone()
        local_e = end.astimezone(_tz()) if _tz() else end.astimezone()
        for attempt in range(MAX_TRIES):
            self._stop_load(chan)
            time.sleep(3 + attempt)   # settle: drain any prior session
            dav = work / "window.dav"
            url = (f"{host}/cgi-bin/loadfile.cgi?action=startLoad&channel={chan}"
                   f"&startTime={local_s:%Y-%m-%d %H:%M:%S}"
                   f"&endTime={local_e:%Y-%m-%d %H:%M:%S}").replace(" ", "%20")
            # 180 s: a 120 s window of 2688x1520 main stream is ~65 MB; the
            # healthy device serves ~2 MB/s (47 MB in 24 s) but degrades under
            # session pressure, and the old 60 s default silently truncated
            # nothing only because windows were small. Sized, not guessed.
            rc = self._curl(url, str(dav), max_time=180)
            self._stop_load(chan)   # close NOW so it cannot bleed into the next
            if rc != 0:
                # A partial download (e.g. exit 28, --max-time hit) can be
                # big enough to pass the size gate and would ingest as a
                # silently SHORT clip — footage missing with no signal.
                logger.warning("nvr ch%d fetch attempt %d: curl exited %d — "
                               "discarding partial download", chan, attempt, rc)
                dav.unlink(missing_ok=True)
                continue
            if dav.exists() and dav.stat().st_size > 2000:
                return dav
            dav.unlink(missing_ok=True)
        return None

    def _probe_cut(self, part: Path) -> Optional[float]:
        """Decoded duration of the cut in seconds, or None if it holds no
        decodable frames.

        The deterministic sanity gate: a full decode to the null muxer (clips
        are <= MAX_WINDOW_S, this is cheap) counts real frames and reports the
        decoded timeline, so it catches both a zero-decodable pull and a
        truncated one. Duration comes from the decoder's final progress clock
        (`time=`), falling back to the container `Duration:` header — never
        from file size, which a partial download can fake."""
        if not (part.exists() and part.stat().st_size > 2000):
            return None
        proc = subprocess.run(
            [self._ffmpeg(), "-hide_banner", "-i", str(part), "-f", "null", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, check=False,
        )
        err = proc.stderr or ""
        frames = re.findall(r"frame=\s*(\d+)", err)
        if not frames or int(frames[-1]) <= 0:
            return None
        clock = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", err)
        if not clock:
            clock = re.findall(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
        if not clock:
            return None
        h, m, s = clock[-1]
        return int(h) * 3600 + int(m) * 60 + float(s)

    def _trim_encode(self, raw: Path, a: float, b: float, part: Path) -> None:
        """Frame-accurate cut of [a,b) seconds into part (output-side seek +
        re-encode; verified accurate against a synthetic color-per-second
        clip, and on a real .dav: -ss 2 -to 30 measured 28.00 s; the real
        full-resolution cut of a 20 s target measured exactly 20.0 s).
        +genpts for the timestamps some .dav packets lack — decode-side
        seeking needs a sane clock."""
        subprocess.run(
            [self._ffmpeg(), "-hide_banner", "-y", "-fflags", "+genpts",
             "-i", str(raw),
             "-ss", f"{a:.2f}", "-to", f"{b:.2f}",
             "-c:v", "libx264", "-an", "-movflags", "+faststart", str(part)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
