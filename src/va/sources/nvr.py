"""NVR chunk source (WS4.c) — pull a wall-clock window from the recorder.

URI form:  nvr://<channel>/<start>/<end>
           nvr://1/2026-08-01T12:00:00/2026-08-01T12:00:30

`<channel>` is the NVR's 1-indexed DISPLAY channel number (the same ref
MotionEvents carry). Times are ISO-8601; naive times are interpreted in the
NVR's clock timezone (`VA_NVR_TZ`, else the system-local rules, DST-aware —
the same convention as the lnr-eventlog MotionSource).

The pull is the verify-and-trim recipe proven in the NVR experiment
(nvr-access-notes.md §5d): this firmware's `loadfile.cgi` intermittently serves
a ~1 s STALE lead-in from another channel/day after any seek, so every 10 s
chunk is pulled in an isolated load session, densely dHash-verified against a
live `snapshot.cgi` reference for its channel, TRIMMED to its longest clean
run, and re-encoded uniformly so chunks concat cleanly. Unverifiable chunks
are retried with escalating settle time, then dropped (logged) — a gap beats
cross-camera contamination.

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
CHUNK_S = 10          # loadfile is pulled in isolated 10 s sessions (§5d recipe)
MAX_WINDOW_S = 120    # refuse absurd windows; motion episodes are ~30-70 s
DHASH_THRESH = 18     # same-camera frames measure <=13; cross-camera 25-38
MAX_TRIES = 4         # per-chunk attempts before dropping it
FPS_SAMPLE = 4        # dense verification sampling rate
MIN_KEEP_S = 1.0      # keep a chunk only if its clean run is at least this long


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


def chunk_bounds(start: datetime, end: datetime) -> List[Tuple[datetime, datetime]]:
    """Split [start,end) into CHUNK_S-second pull sessions (last one clipped)."""
    out = []
    t = start
    while t < end:
        nxt = min(end, t + timedelta(seconds=CHUNK_S))
        out.append((t, nxt))
        t = nxt
    return out


def dhash(img, s: int = 8):
    import numpy as np

    g = np.asarray(img.convert("L").resize((s + 1, s)), dtype="int16")
    return (g[:, 1:] > g[:, :-1]).flatten()


def hamming(a, b) -> int:
    import numpy as np

    return int(np.count_nonzero(a != b))


def longest_clean_run(hs: List[int], thresh: int = DHASH_THRESH
                      ) -> Optional[Tuple[int, int]]:
    """Longest contiguous run of per-frame Hammings <= thresh, inclusive bounds."""
    best = cur0 = None
    for i, h in enumerate(hs):
        if h <= thresh:
            cur0 = i if cur0 is None else cur0
            if best is None or (i - cur0) > (best[1] - best[0]):
                best = (cur0, i)
        else:
            cur0 = None
    return best


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
            # a misleading "could not fetch a live reference frame" later.
            raise RuntimeError(
                "VA_NVR_USER/VA_NVR_PASS must not contain newline characters"
            )
        quoted = raw.replace("\\", "\\\\").replace('"', '\\"')
        return f'user = "{quoted}"\n'

    def _curl(self, url: str, out: str, max_time: int = 60) -> None:
        _, user, password = self._conn()
        subprocess.run(
            self._curl_argv(url, out, max_time),
            input=self._curl_config(user, password),
            text=True,
            check=False,
        )

    def _stop_load(self, chan: int) -> None:
        host, _, _ = self._conn()
        self._curl(f"{host}/cgi-bin/loadfile.cgi?action=stopLoad&channel={chan}",
                   "/dev/null", max_time=15)

    def _reference_hash(self, chan: int, workdir: Path):
        """Trusted scene reference: a LIVE snapshot is reliably the right camera."""
        from PIL import Image

        host, _, _ = self._conn()
        ref = workdir / f"ref_ch{chan}.jpg"
        self._curl(f"{host}/cgi-bin/snapshot.cgi?channel={chan}", str(ref),
                   max_time=20)
        if not (ref.exists() and ref.stat().st_size > 2000):
            raise RuntimeError(f"could not fetch a live reference frame for ch{chan}")
        return dhash(Image.open(ref).convert("RGB"))

    def _frame_hammings(self, ts_file: Path, refh) -> List[int]:
        from PIL import Image

        fdir = ts_file.with_suffix(".frames")
        fdir.mkdir(exist_ok=True)
        subprocess.run(
            [self._ffmpeg(), "-hide_banner", "-y", "-i", str(ts_file),
             "-vf", f"fps={FPS_SAMPLE},scale=400:-1", str(fdir / "f_%03d.jpg")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        hs = []
        for f in sorted(fdir.glob("*.jpg")):
            if f.stat().st_size > 500:
                try:
                    hs.append(hamming(refh, dhash(Image.open(f).convert("RGB"))))
                except Exception:  # noqa: BLE001 — a torn frame is DIRTY, not fatal
                    # A >500-byte but corrupt JPEG (ffmpeg died mid-write) must
                    # count dirty and stay inside the retry machinery, not
                    # abort the whole ingest (round-5 review finding).
                    hs.append(10**6)
            else:
                # Unreadable/truncated frame: keep the index -> time mapping
                # intact and treat it as DIRTY (an unverified frame must not
                # sit inside a "clean" run, and skipping it would shift every
                # later trim bound 1/FPS_SAMPLE early).
                hs.append(10**6)
            f.unlink(missing_ok=True)
        fdir.rmdir()
        return hs

    def _pull_window(self, chan: int, start: datetime, end: datetime,
                     out_mp4: Path) -> None:
        """Verify-and-trim pull of [start,end) on `chan` into out_mp4.

        All intermediates live in a pull-private temp dir (parallel per-camera
        ingests must not share chunk/ref filenames), and the final mp4 lands
        via atomic rename — fetch() trusts an existing cache file, so a clip
        killed mid-concat must never be reusable under the final name.
        """
        host, _, _ = self._conn()
        work = Path(tempfile.mkdtemp(dir=out_mp4.parent,
                                     prefix=out_mp4.stem + ".pull-"))
        segs: List[Path] = []
        dropped = 0
        try:
            # Inside the try: a failed reference fetch (offline camera) must
            # not leak the pull-private dir (round-4 review finding).
            refh = self._reference_hash(chan, work)
            ff = self._ffmpeg()
            for k, (cs, ce) in enumerate(chunk_bounds(start, end)):
                seg = self._pull_chunk_verified(chan, cs, ce, refh,
                                                work / f"chunk_{k}")
                if seg is None:
                    dropped += 1
                    logger.warning("nvr pull ch%d %s: chunk %d unverifiable "
                                   "after %d tries — dropped", chan, start, k,
                                   MAX_TRIES)
                else:
                    segs.append(seg)
            if not segs:
                raise RuntimeError(
                    f"NVR pull ch{chan} {start:%Y-%m-%d %H:%M:%S}: every chunk "
                    "was unverifiable — nothing usable retrieved (NB: frames "
                    "verify against a LIVE snapshot reference, so a pull whose "
                    "lighting no longer matches the recording, e.g. a daytime "
                    "window pulled after dark, rejects everything)"
                )
            part = work / "concat.part.mp4"
            subprocess.run(
                [ff, "-hide_banner", "-y",
                 "-i", "concat:" + "|".join(str(s) for s in segs),
                 "-c", "copy", "-movflags", "+faststart", str(part)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
            if not (part.exists() and part.stat().st_size > 2000):
                raise RuntimeError(f"NVR pull ch{chan}: concat produced no output")
            os.replace(part, out_mp4)   # atomic: cache never holds a torn clip
            if dropped:
                logger.warning(
                    "nvr pull ch%d %s: %d/%d chunks dropped as unverifiable — "
                    "footage after each gap plays up to %ds earlier than "
                    "start_epoch+t suggests (timeline drift; PTS-accurate "
                    "alignment is backlog)", chan, start, dropped,
                    dropped + len(segs), CHUNK_S,
                )
        finally:
            shutil.rmtree(work, ignore_errors=True)
            self._stop_load(chan)

    def _pull_chunk_verified(self, chan: int, cs: datetime, ce: datetime,
                             refh, tag: Path) -> Optional[Path]:
        host, _, _ = self._conn()
        ff = self._ffmpeg()
        local_cs = cs.astimezone(_tz()) if _tz() else cs.astimezone()
        local_ce = ce.astimezone(_tz()) if _tz() else ce.astimezone()
        for attempt in range(MAX_TRIES):
            self._stop_load(chan)
            time.sleep(3 + attempt)  # escalating settle: drain prior sessions
            dav = tag.with_suffix(".dav")
            url = (f"{host}/cgi-bin/loadfile.cgi?action=startLoad&channel={chan}"
                   f"&startTime={local_cs:%Y-%m-%d %H:%M:%S}"
                   f"&endTime={local_ce:%Y-%m-%d %H:%M:%S}").replace(" ", "%20")
            self._curl(url, str(dav))
            self._stop_load(chan)  # close NOW so it can't bleed into the next pull
            if not (dav.exists() and dav.stat().st_size > 2000):
                dav.unlink(missing_ok=True)
                continue
            raw = tag.with_suffix(".raw.ts")
            subprocess.run(
                [ff, "-hide_banner", "-y", "-fflags", "+genpts", "-i", str(dav),
                 "-c", "copy", "-f", "mpegts", str(raw)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
            dav.unlink(missing_ok=True)
            if not (raw.exists() and raw.stat().st_size > 2000):
                raw.unlink(missing_ok=True)
                continue
            hs = self._frame_hammings(raw, refh)
            run = longest_clean_run(hs)
            if run and (run[1] - run[0] + 1) / FPS_SAMPLE >= MIN_KEEP_S:
                a, b = run[0] / FPS_SAMPLE, (run[1] + 1) / FPS_SAMPLE
                seg = tag.with_suffix(".ts")
                subprocess.run(
                    [ff, "-hide_banner", "-y", "-i", str(raw),
                     "-ss", f"{a:.2f}", "-to", f"{b:.2f}",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                     "-an", "-f", "mpegts", str(seg)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False,
                )
                raw.unlink(missing_ok=True)
                if seg.exists() and seg.stat().st_size > 2000:
                    return seg
                seg.unlink(missing_ok=True)
                continue
            raw.unlink(missing_ok=True)  # no clean run -> retry
        return None
