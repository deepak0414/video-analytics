"""NVR recorded-window source (WS4.c) — pull a wall-clock window from the recorder.

URI form:  nvr://<channel>/<start>/<end>
           nvr://1/2026-08-01T12:00:00/2026-08-01T12:00:30

`<channel>` is the NVR's 1-indexed DISPLAY channel number (the same ref
MotionEvents carry). Times are ISO-8601; naive times are interpreted in the
NVR's clock timezone (`VA_NVR_TZ`, else the system-local rules, DST-aware —
the same convention as the lnr-eventlog MotionSource).

The pull is DETERMINISTIC: one loadfile session for a PADDED window
[start - PAD_PRE, end + PAD_POST], then a PTS cut to the exact requested
window. No fingerprinting. §5d's stale lead-in (this firmware intermittently
serves ~1-2 s from a previous load session's buffer after a time-seek) is
bounded and confined to the HEAD of the stream, so a fixed pre-pad absorbs it
and the cut discards it, whatever it contains. Validated against the real
device 2026-08-12 (scratchpad detpull/{validate_det,reliability,characterize}.py):
  - 7/7 windows across every lighting mode (deep-night IR, morning,
    late-morning, noon, afternoon, late-evening, late-night) had the target
    window clean behind the pad (max dHash-from-consensus <= 2 — night IR
    behaved identically to noon);
  - the same window pulled twice was BYTE-IDENTICAL (frame spread 0:
    676/676, 666/666, 646/646) — loadfile is deterministic per window;
  - a real full-resolution PTS cut of a 20 s target measured exactly 20.0 s.

Window IDENTITY is trusted from the request: a channel's stored file is
single-camera, so asking channel N for [start, end] and discarding the seek
lead-in leaves nothing for perceptual verification to add. The previous
design — consensus-dHash verify-and-trim plus a per-channel lighting-mode
`ReferenceLibrary` with live-snapshot admission of unseen modes — is GONE: it
was lighting-dependent and false-refused correct footage (11 dusk windows in
one backfill were right-camera footage rejected for a lighting mismatch). A
pre-existing `<workdir>/nvr_refs/` directory is vestigial (it was a cache)
and can be deleted.

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


class NvrRecordedSource:
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
        rename — fetch() trusts an existing cache file, so a clip killed
        mid-write must never be reusable under the final name.
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
                    if dur is not None and abs(dur - window_len) <= DURATION_TOL_S:
                        os.replace(part, out_mp4)   # atomic: never a torn clip
                        if pad_pre == 0.0:
                            logger.warning(
                                "nvr pull ch%d %s: pre-pad footage unavailable "
                                "(ring edge?) — fell back to an exact-window "
                                "pull; a lead-in, if any, is NOT padded away",
                                chan, start,
                            )
                        return
                    failures.append("no decodable frames" if dur is None
                                    else f"{dur:.1f}s")
                    part.unlink(missing_ok=True)
                    Path(raw).unlink(missing_ok=True)   # force a fresh fetch
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
