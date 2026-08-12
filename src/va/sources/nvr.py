"""NVR recorded-window source (WS4.c) — pull a wall-clock window from the recorder.

URI form:  nvr://<channel>/<start>/<end>
           nvr://1/2026-08-01T12:00:00/2026-08-01T12:00:30

`<channel>` is the NVR's 1-indexed DISPLAY channel number (the same ref
MotionEvents carry). Times are ISO-8601; naive times are interpreted in the
NVR's clock timezone (`VA_NVR_TZ`, else the system-local rules, DST-aware —
the same convention as the lnr-eventlog MotionSource).

The pull is ONE loadfile session for the window, densely dHash-verified and
trimmed to its longest internally-consistent run. §5d's stale lead-in (this
firmware intermittently serves ~1 s from another channel/day after a seek) is
real but rare: measured 2026-08-10 over 10 windows across 4 days, 4 cameras and
00:57-22:09, a single request scored purity 1.000 with zero leading
contamination every time, while the former 10 s chunk recipe left 2 dirty
chunks in 300. Every chunk is a seek, and a seek is the risk — so chunking
multiplied exposure to the bug it was meant to defeat, at ~30x the round-trips.

Verification deliberately does NOT judge footage against a live `snapshot.cgi`
reference. That snapshot carries the lighting of the download moment, so
backfilled footage is judged against the wrong scene: measured on this NVR, the
same camera scores 22-23 against a live reference six hours later and 38 at
night (IR mode), while a genuinely DIFFERENT camera scores 25-38 — overlapping
ranges, so no threshold can separate "same camera, other hour" from "wrong
camera". Instead:
  - frames are checked against the pull's OWN consensus scene (catches a
    contaminated minority, i.e. the lead-in), and
  - that consensus is checked against a per-channel `ReferenceLibrary` (catches
    a wholly-wrong clip, which would be self-consistent).
Known modes therefore verify without consulting the present, so night pulls on
a night-seeded channel work exactly like daylight ones. The live snapshot keeps
ONE narrow job: admitting a lighting mode the library has never seen (it is
reliably the right camera, and near-real-time pulls share its lighting) — see
`_pull_window` for the flow and the deliberate residual gap.

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

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from va.contracts.video import Camera, ResolvedVideo, SourceType

logger = logging.getLogger(__name__)

_URI_RE = re.compile(
    r"^nvr://(?P<chan>\d+)/(?P<start>[^/]+)/(?P<end>[^/]+)$"
)

# Structure/budget knobs for the pull (not content):
MAX_WINDOW_S = 120    # refuse absurd windows; motion episodes are ~30-70 s
DHASH_THRESH = 18     # same-camera frames measure <=13; cross-camera 25-38
MAX_TRIES = 4         # window fetch attempts before the pull fails
FPS_SAMPLE = 4        # dense verification sampling rate
MIN_KEEP_S = 1.0      # keep a pull only if its clean run is at least this long
UNDECODABLE = 10 ** 6  # self-distance for a frame with no hash; no real frame
                       # can score this, so it can never sit in a clean run


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


def dhash(img, s: int = 8):
    import numpy as np

    g = np.asarray(img.convert("L").resize((s + 1, s)), dtype="int16")
    return (g[:, 1:] > g[:, :-1]).flatten()


def hamming(a, b) -> int:
    import numpy as np

    return int(np.count_nonzero(a != b))


class ReferenceLibrary:
    """Per-CHANNEL scene hashes, persisted outside the transient cache.

    Keyed per channel and never shared: a single pooled library would happily
    accept ch3's footage for a ch0 request, since every camera here is "ours" —
    which is exactly the cross-camera contamination the check exists to catch.

    Entries accumulate across lighting modes (daylight, dusk, IR night), so a
    night pull is judged against a night entry instead of whatever the camera
    happens to see at download time. It is a CACHE, not a fact about the
    install: delete it and the next pull reseeds. That is why it lives in the
    workdir as JSON rather than in the catalog schema, which would buy a
    migration obligation forever for rebuildable data.
    """

    MAX_PER_CHANNEL = 12          # bounded; distinct lighting modes are few

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, chan: int) -> Path:
        return self.root / f"ch{chan}.json"

    def load(self, chan: int) -> List[List[int]]:
        p = self._path(chan)
        if not p.exists():
            return []
        try:
            return json.loads(p.read_text()).get("scenes", [])
        except Exception:  # noqa: BLE001 — a corrupt cache must not break a pull
            logger.warning("nvr reference library for ch%d unreadable — reseeding",
                           chan)
            return []

    def accepts(self, chan: int, cons) -> Tuple[bool, Optional[int]]:
        """(is this channel's scene, best distance) — (True, None) when unseeded."""
        import numpy as np

        known = self.load(chan)
        if not known:
            return True, None                      # first sight: seed, see below
        best = min(hamming(np.asarray(k, dtype=bool), cons) for k in known)
        return best <= DHASH_THRESH, best

    def add(self, chan: int, cons) -> None:
        import numpy as np

        known = self.load(chan)
        if any(hamming(np.asarray(k, dtype=bool), cons) <= DHASH_THRESH
               for k in known):
            return                                  # this mode already covered
        known.append([int(b) for b in np.asarray(cons).ravel()])
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self._path(chan).with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"scenes": known[-self.MAX_PER_CHANNEL:]}))
            os.replace(tmp, self._path(chan))       # atomic: never a torn library
        except OSError:
            # The library is a CACHE and must never break a pull in either
            # direction: load() swallows corruption, so add() must swallow a
            # failed write (read-only subtree, ENOSPC). By the time we are
            # seeding, the verified clip has already landed — aborting the
            # ingest over bookkeeping would fail a pull that succeeded
            # (round-2 review minor 1). The channel just stays unseeded.
            logger.warning("nvr reference library for ch%d not writable — "
                           "the channel stays unseeded", chan)


def consensus_hash(hashes):
    """The scene the MAJORITY of these frames agree on, bit by bit.

    This is the reference a pull is verified against, replacing a live snapshot.
    A live snapshot is taken NOW, so it encodes the lighting of the moment we
    happen to download — which is not the lighting of the footage whenever we
    backfill. Measured on this NVR: the same camera scores 22-23 against a live
    reference six hours later and 38 at night (IR mode), while a genuinely
    different camera scores 25-38. Those overlap, so no threshold can separate
    "same camera, different hour" from "wrong camera" when the reference is
    pinned to the present. The pull's own majority scene carries no such bias.

    Undecodable frames (None) are EXCLUDED — a torn frame must not vote. An
    all-zeros stand-in would: zeros is the genuine dhash of any dark/uniform
    frame, so on night footage sentinel votes would look like real ones.
    """
    import numpy as np

    valid = [np.asarray(h).ravel() for h in hashes if h is not None]
    if not valid:
        raise ValueError("no decodable frames to form a consensus")
    return np.mean(np.stack(valid), axis=0) > 0.5


def self_distances(hashes) -> List[int]:
    """Each frame's Hamming distance from the pull's own consensus scene.

    Catches a contaminated MINORITY — the stale lead-in §5d documents, where the
    first frames of a pull come from another camera or date. It cannot catch a
    wholly-wrong clip (that would be self-consistent), which is why camera
    identity is checked separately against the per-channel reference library.

    Undecodable frames (None) score UNDECODABLE: they keep their slot so the
    index -> time mapping stays intact, but can never sit inside a clean run.
    """
    if not hashes:
        return []
    if all(h is None for h in hashes):
        return [UNDECODABLE] * len(hashes)
    cons = consensus_hash(hashes)
    return [UNDECODABLE if h is None else hamming(cons, h) for h in hashes]


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

    def _curl(self, url: str, out: str, max_time: int = 60) -> int:
        """Returns curl's exit status. Callers that download a payload MUST
        check it: a transfer killed by --max-time (exit 28) leaves a partial
        file that can pass a size gate and ingest as a silently SHORT clip
        (round-1 review minor 2). Best-effort callers (stopLoad, snapshot)
        may ignore it."""
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
                     out_mp4: Path, refs: "ReferenceLibrary" = None) -> None:
        """Pull [start,end) on `chan` into out_mp4, verified without a live reference.

        ONE request for the whole window, not a chunk loop. Measured 2026-08-10
        over 10 windows spanning 4 days, 4 cameras and 00:57-22:09: a single
        request scored purity 1.000 with zero leading contamination on every
        sample, while the 10 s chunk recipe produced 2 dirty chunks in 300. Each
        chunk is a fresh seek, and a seek is what risks the §5d stale lead-in —
        so chunking multiplied exposure to the bug it was adopted to defeat, at
        ~30x the round-trips (a stop/settle per chunk).

        Verification is two independent checks, because they catch different
        failures (see `self_distances` and `ReferenceLibrary`):
          - self-consistency trims a contaminated MINORITY (lead-in);
          - the per-channel library rejects a wholly-wrong clip.
        Known lighting modes verify against the library alone — no live
        lookup, so night backfill of a night-seeded channel just works. A
        scene the library does NOT know is admitted only if it matches a LIVE
        snapshot (reliably the right camera, valid for the current lighting):
        that is how a day-seeded channel acquires its night entry the first
        time the watcher pulls after dark. Wrong-camera footage matches
        neither and is refused. The residual gap is deliberate: backfilling a
        never-seeded mode while the live scene disagrees (night footage
        pulled at noon on a day-only channel) cannot be verified by anything
        this design trusts, so it fails with recovery guidance rather than
        fail open.

        The library is seeded only AFTER the pull passes verification and the
        trim lands, and from the CLEAN RUN's consensus — a failed or
        contaminated first pull must never become a channel's reference
        (round-1 review findings 1 and 4).

        All intermediates live in a pull-private temp dir (parallel per-camera
        ingests must not share filenames), and the final mp4 lands via atomic
        rename — fetch() trusts an existing cache file, so a clip killed
        mid-write must never be reusable under the final name.
        """
        host, _, _ = self._conn()
        work = Path(tempfile.mkdtemp(dir=out_mp4.parent,
                                     prefix=out_mp4.stem + ".pull-"))
        try:
            raw = self._fetch_window(chan, start, end, work)
            if raw is None:
                raise RuntimeError(
                    f"NVR pull ch{chan} {start:%Y-%m-%d %H:%M:%S}: no data "
                    f"returned after {MAX_TRIES} attempts"
                )
            hashes = self._frame_hashes(raw)
            n_valid = sum(1 for h in hashes if h is not None)
            if n_valid < 3:
                raise RuntimeError(
                    f"NVR pull ch{chan} {start:%Y-%m-%d %H:%M:%S}: only "
                    f"{n_valid} decodable frames of {len(hashes)} sampled — "
                    f"nothing to verify"
                )
            cons = consensus_hash(hashes)

            # Camera identity FIRST: trimming a clip that is entirely the wrong
            # camera would otherwise "succeed" on its own self-consistency.
            # BESIDE the cache, not inside it: cache/ is documented transient,
            # and a wipe would silently return every camera to the unverified
            # first-sight state. out_mp4 lives in <workdir>/cache/, so its
            # grandparent is the workdir. `refs` stays injectable for tests —
            # the CALL keeps its 4 positional args, because test doubles of this
            # method exist and widening a call silently breaks them (CLAUDE.md).
            refs = refs or ReferenceLibrary(out_mp4.parent.parent / "nvr_refs")
            ok, best = refs.accepts(chan, cons)
            if not ok:
                # Library miss: a NEW lighting mode, or the wrong camera. A
                # LIVE snapshot separates them for the current moment — it is
                # reliably the right camera, and the watcher pulls episodes
                # near-real-time, so its lighting matches the footage. This is
                # how a day-seeded channel legitimately acquires night mode.
                snap = self._snapshot_hash(chan, work)
                snap_d = None if snap is None else hamming(snap, cons)
                if snap_d is not None and snap_d <= DHASH_THRESH:
                    logger.info(
                        "nvr pull ch%d %s: new lighting mode for this channel "
                        "(closest library entry %d) — verified against the "
                        "live scene (distance %d), will be seeded on success",
                        chan, start, best, snap_d,
                    )
                else:
                    live = ("the live view is unavailable" if snap_d is None
                            else f"the live view (distance {snap_d})")
                    raise RuntimeError(
                        f"NVR pull ch{chan} {start:%Y-%m-%d %H:%M:%S}: footage "
                        f"matches neither a known scene for this channel "
                        f"(closest {best} > {DHASH_THRESH}) nor {live} — "
                        f"refusing it rather than ingesting another camera's "
                        f"video. If this is backfill of a lighting mode not "
                        f"seen yet (e.g. night footage pulled in daylight), "
                        f"re-pull while the live scene matches the recording; "
                        f"to start over, delete <workdir>/nvr_refs/ch{chan}.json"
                    )
            first_sight = best is None
            if first_sight:
                logger.warning(
                    "nvr pull ch%d %s: first scene recorded for this channel — "
                    "accepted UNVERIFIED; on success it seeds the reference. "
                    "Later pulls in this lighting mode are checked against it; "
                    "seed deliberately (one pull per camera per lighting mode) "
                    "if that first-sight trust is not acceptable.", chan, start,
                )

            dists = self_distances(hashes)
            run = longest_clean_run(dists)
            if run is None or (run[1] - run[0] + 1) / FPS_SAMPLE < MIN_KEEP_S:
                raise RuntimeError(
                    f"NVR pull ch{chan} {start:%Y-%m-%d %H:%M:%S}: no clean run "
                    f"of at least {MIN_KEEP_S}s — footage is internally "
                    f"inconsistent"
                )
            a, b = run[0] / FPS_SAMPLE, (run[1] + 1) / FPS_SAMPLE
            trimmed = (run[1] - run[0] + 1) < len(dists)
            part = work / "trim.part.mp4"
            self._trim_encode(raw, a, b, part)
            if not (part.exists() and part.stat().st_size > 2000):
                raise RuntimeError(f"NVR pull ch{chan}: trim produced no output")
            os.replace(part, out_mp4)   # atomic: cache never holds a torn clip
            # Seed ONLY now — after every check and the trim succeeded — and
            # from the CLEAN RUN's consensus, not the whole window's: a failed
            # pull must never poison the channel, and a seed must not carry
            # votes from frames the trim just threw away (round-1 findings
            # 1 and 4). Frames in the clean run are decodable by construction
            # (undecodable frames score UNDECODABLE and cannot enter a run).
            refs.add(chan, consensus_hash(hashes[run[0]:run[1] + 1]))
            if trimmed:
                logger.warning(
                    "nvr pull ch%d %s: trimmed %.1fs of inconsistent footage "
                    "from a %.1fs window — t=0 is start_epoch+%.1fs, so stored "
                    "timestamps run early by that much (timeline drift; "
                    "PTS-accurate alignment is backlog)", chan, start,
                    (len(dists) - (run[1] - run[0] + 1)) / FPS_SAMPLE,
                    len(dists) / FPS_SAMPLE, a,
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
        the silent remux discarded it every time. Downstream needs only
        decodable frames (`_frame_hashes`) and a re-encode (`_trim_encode`),
        and both read the .dav directly (bundled ffmpeg has the dhav
        demuxer). Seeking on the .dav is exact: a -ss 2 -to 30 trim measured
        28.00 s to the frame."""
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

    def _frame_hashes(self, media: Path):
        """dHash every sampled frame; None for an undecodable one.

        Reads the pulled .dav directly (+genpts synthesizes the timestamps
        some of its packets lack, so fps sampling stays uniform). None keeps
        the index -> time mapping intact (dropping a slot would shift every
        later trim bound 1/FPS_SAMPLE early) while being impossible to
        mistake for footage. A stand-in HASH cannot do that job: all-zeros is
        the genuine dhash of any dark/uniform frame, so on night footage a
        zeros sentinel would read as clean, vote in the consensus, and could
        even seed the reference library (round-1 finding 3)."""
        from PIL import Image

        fdir = media.with_suffix(".frames")
        fdir.mkdir(exist_ok=True)
        subprocess.run(
            [self._ffmpeg(), "-hide_banner", "-y", "-fflags", "+genpts",
             "-i", str(media),
             "-vf", f"fps={FPS_SAMPLE},scale=400:-1", str(fdir / "f_%04d.jpg")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        out = []
        for f in sorted(fdir.glob("*.jpg")):
            if f.stat().st_size > 500:
                try:
                    out.append(dhash(Image.open(f).convert("RGB")))
                except Exception:  # noqa: BLE001 — torn frame is DIRTY, not fatal
                    out.append(None)
            else:
                out.append(None)
            f.unlink(missing_ok=True)
        fdir.rmdir()
        return out

    def _snapshot_hash(self, chan: int, work: Path):
        """dHash of a LIVE snapshot, or None if one cannot be fetched.

        The live view is reliably the right camera but carries the lighting of
        THIS moment, so it is consulted only to admit a lighting mode the
        library does not know yet — never to judge footage against a mode it
        does. (Judging everything by the live view is the design this branch
        removes: it rejected night backfill wholesale.)"""
        from PIL import Image

        try:
            host, _, _ = self._conn()
            snap = work / f"snap_ch{chan}.jpg"
            self._curl(f"{host}/cgi-bin/snapshot.cgi?channel={chan}",
                       str(snap), max_time=20)
            if not (snap.exists() and snap.stat().st_size > 2000):
                return None
            return dhash(Image.open(snap).convert("RGB"))
        except Exception:  # noqa: BLE001 — no live view degrades, never aborts
            logger.warning("nvr ch%d: live snapshot unavailable", chan)
            return None

    def _trim_encode(self, raw: Path, a: float, b: float, part: Path) -> None:
        """Frame-accurate trim of [a,b) seconds into part (output-side seek +
        re-encode; verified accurate against a synthetic color-per-second
        clip, and on a real .dav: -ss 2 -to 30 measured 28.00 s). +genpts for
        the timestamps some .dav packets lack — decode-side seeking needs a
        sane clock. One encode per pull — the old per-chunk encode+concat is
        gone with chunking itself."""
        subprocess.run(
            [self._ffmpeg(), "-hide_banner", "-y", "-fflags", "+genpts",
             "-i", str(raw),
             "-ss", f"{a:.2f}", "-to", f"{b:.2f}",
             "-c:v", "libx264", "-an", "-movflags", "+faststart", str(part)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
