"""Default burned-in-clock `TimestampReader` for the NVR delivery verifier.

This is the census's **mandatory item 1** (`va-24h-data-integrity-investigation.md`):
the delivery verifier (`sources/verify.py`) ships a burned-in-clock gate as an
INJECTABLE seam, but PR #43 wired NO default reader — so the gate was inactive and
SAME-CAMERA WRONG-WEEK footage (a stale ring-buffer fragment of the right camera but
a ~7-day-old week) slipped through: it reads CLOSE to the clip body under the
head-identity dHash gate (it IS the same camera), so only the recorder's own
burned-in wall-clock can tell it apart from the requested window.

This module supplies that reader by OCR-ing the Lorex overlay
(`MM-DD-YYYY hh:mm:ss AM/PM`, e.g. ``08-04-2026 09:35:39 PM``) off the TRUE head
frames (`media/frames.head_clock_frames` — a ~1.5 s span, `CLOCK_HEAD_SECONDS`,
decoded sequentially from frame 0 and sampled sparsely, so the `-vf fps=N` sampler
blindness that hid the census heads cannot recur AND a multi-frame wrong-week head
shows its aligned tail) and parsing it to a UTC epoch the pure verifier compares
against ``start_epoch + t``.

**Robustness policy (CRITICAL — determinism is not correctness).** Wrong-week is
DAYS off, so a COARSE read catches it; second precision is unnecessary. The census
bands make the safe decision explicit: an ALIGNED read is |Δ| ≤ 5 s (94.3 %),
legitimate loadfile alignment DRIFT reaches 56 s (2.6 %, still GOOD footage), and
FOREIGN wrong-week footage is |Δ| ≥ 12 h (~7 days in practice). So this reader is
built to REJECT ONLY a CONFIDENT, LARGE, CONSISTENT mismatch and to FAIL OPEN on
anything it cannot read cleanly — losing a good clip to flaky OCR is worse than the
current no-reader state:

  - Per frame: a box below the OCR confidence floor, or text that does not parse to
    a full Lorex timestamp (the meridiem is REQUIRED — an OCR miss there yields NO
    reading, never a 12-hour-shifted one; and month/day/hour must be zero-padded, so
    a dropped digit is a non-parse, never a ~10-day-off one), contributes NOTHING.
  - Across frames: a reading is emitted only if it AGREES (base epoch within
    `AGREE_TOL_S`) with at least `MIN_AGREE_FRAMES` of the survivors — a lone outlier
    (a single stale or mis-read frame among agreeing ones) is DROPPED as noise. Too
    few agreeing reads emit `()` → the clock gate is skipped and the OCR-free
    head/stream guards still run (fail open).
  - The emitted readings are handed to the verifier request-BLIND, in time order;
    the verifier decides aligned-vs-foreign against `start_epoch` and TRIMS a foreign
    head that is followed by an aligned tail (so a multi-frame cross-camera lead-in
    is trimmed like the dHash gate does, not rejected), REJECTS an all-foreign run
    (wrong-week), and ACCEPTS an aligned run.
  - The gate tolerance is coarse (`nvr.CLOCK_OCR_TOL_S`, just over 12 h — a 12 h AM/PM
    flip plus drift — and far below the ~7-day foreign band), so an aligned or
    legitimately-drifted clip is never rejected — only a wrong-WEEK offset trips it.

RESIDUAL (honest limitation): fail-open covers the OCR error modes that produce NO
reading — low confidence, an unreadable clock, a dropped padding digit, a lone
mis-read frame, or frames that disagree. It does NOT cover a CONSISTENT valid-digit
SUBSTITUTION across the whole head (e.g. every frame misreads 08-10 as 08-15): that
parses to a confident, agreeing, multi-day skew and reads exactly like contamination,
so it can false-reject GOOD footage (and a live pull's exact-window re-pull repeats
the same misread). This is rarer than the dropped-digit class the `\d{2}` fix removed
and is bounded by the ring, but it is a real residual. The robust fix (backlog) is a
head-vs-BODY clock cross-check: a substitution misreads head and body identically
(→ noise, accept), while genuine contamination reads a foreign head over an aligned
body (→ trim/reject) — the clock analogue of the dHash head-identity gate.

COVERAGE (honest): the reader targets wrong-week footage (a stale ring fragment of
the right camera but a ~7-day-old week). The clock inspects a ~1.5 s span
(CLOCK_HEAD_SECONDS): a wrong-week head that fits inside it (with an aligned tail) is
TRIMMED at that tail; a head that FILLS the window — the real .va-24h heads run 2-3 s+
— has no aligned tail inspected and is REJECTED. Reject is the right outcome, not a
regression: on a live pull it re-runs the pull's exact-window fallback (no pre-pad
seek), which re-pulls the window CLEAN — better than a trimmed clip missing its onset.
A SINGLE-frame stale lead-in is below the agree floor and is left to the dHash
head-identity gate rather than risk acting on one OCR read. Defence in depth, not a
cure.

Enablement: `default_timestamp_reader()` returns this reader when the `[ocr]` extra
is importable and `VA_NVR_CLOCK_GATE` is not set to an off value; unavailable or
disabled → None (the gate stays inactive, exactly as before). The OCR engine is the
Role-10 RapidOCR adapter, REUSED (not reinvented) and lazily built on first use.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone, tzinfo
from typing import List, Optional, Protocol, Sequence, Tuple, runtime_checkable

from va.sources.verify import ClockReading

logger = logging.getLogger(__name__)

# --- reader knobs (structure, not device content) ---------------------------
MIN_OCR_CONFIDENCE = 0.5   # a box below this is treated as unread (RapidOCR's own
                           # default floor; a garbled overlay contributes nothing)
MIN_AGREE_FRAMES = 2       # need at least this many confident, parsed head reads
                           # before the gate will act at all (else fail open)
AGREE_TOL_S = 30.0         # head reads whose base epoch (observed − t) lands within
                           # this of each other agree; wider than any within-clip
                           # second jitter, far below the wrong-week scale
CLOCK_HEAD_SECONDS = 1.5   # span of the head the clock inspects. A same-camera
                           # wrong-week lead-in can run ~1 s (longer than a
                           # cross-camera head); inspecting only ~0.4 s would see it
                           # as all-foreign and REJECT a recoverable clip instead of
                           # TRIMMING at its aligned tail. Sampled sparsely (below)
                           # so this costs a bounded number of OCR calls.

_DISABLED_VALUES = frozenset({"0", "off", "false", "no", "disable", "disabled", "none"})
_ENABLED_VALUES = frozenset({"1", "on", "true", "yes", "enable", "enabled", "auto"})

# The Lorex burned-in overlay: MM-DD-YYYY hh:mm:ss AM/PM, read out of arbitrary
# surrounding OCR noise (camera label, channel name). Separators are permissive
# because OCR spacing is unreliable; the MERIDIEM is REQUIRED (see module policy).
#
# Month/day/hour are matched as ZERO-PADDED `\d{2}`, NOT `\d{1,2}` — and that is a
# correctness requirement, not cosmetics. The Lorex overlay always zero-pads these
# fields, but real RapidOCR on genuine frames systematically DROPS a leading digit
# (`08-1-2026` for 08-11-2026, `0810-2026`), and because the overlay pixels barely
# change frame to frame it drops it CONSISTENTLY across the head — a `\d{1,2}` match
# then parses a confident, agreeing, ~10-day-off reading on GOOD footage and the
# gate false-rejects it (breaking the fail-open guarantee). Requiring `\d{2}` makes
# a dropped digit a NON-parse (contributes nothing → fail open) instead of a wrong
# time. Validated on `.va-24h`'s 9806 real OCR rows: off-by->600s rows 73->19, and
# videos with a MAJORITY of parsed reads off 3->0 (no good clip false-rejects),
# at the cost of ~1.2% of correct parses. Minutes/seconds were already `\d{2}`.
_CLOCK_RE = re.compile(
    r"(?P<mo>\d{2})\s*[-/.]\s*(?P<d>\d{2})\s*[-/.]\s*(?P<y>\d{4})"
    r"[^0-9]{0,4}"
    r"(?P<h>\d{2})\s*[:.]\s*(?P<mi>\d{2})\s*[:.]\s*(?P<s>\d{2})"
    r"\s*(?P<ap>[AaPp])\s*[Mm]"
)


def parse_lorex_clock(text: str) -> Optional[datetime]:
    """Parse a Lorex burned-in overlay out of OCR `text` to a NAIVE wall-clock
    datetime (the recorder paints LOCAL time; localization to an epoch is the
    reader's job, via the NVR timezone). Pure and side-effect-free.

    Returns None when no full ``MM-DD-YYYY hh:mm:ss AM/PM`` timestamp is present or
    the fields are out of range — INCLUDING when the AM/PM meridiem is missing (the
    Lorex overlay always carries it, so an OCR miss there is treated as unreadable,
    fail open, rather than parsed as a 12-hour-shifted time) and INCLUDING when a
    zero-padded month/day/hour digit was dropped (`08-1-2026`): the overlay pads
    those fields, so a single digit is a mis-OCR and yields NO reading rather than a
    ~10-day-off one (see `_CLOCK_RE`).
    """
    m = _CLOCK_RE.search(text or "")
    if not m:
        return None
    mo, d, y = int(m["mo"]), int(m["d"]), int(m["y"])
    h, mi, s = int(m["h"]), int(m["mi"]), int(m["s"])
    if not (1 <= h <= 12):        # 12-hour clock; anything else is a misread
        return None
    if m["ap"].lower() == "p":
        h = h % 12 + 12           # 12 PM -> 12, 1..11 PM -> 13..23
    else:
        h = h % 12                # 12 AM -> 0, 1..11 AM -> 1..11
    try:
        return datetime(y, mo, d, h, mi, s)
    except ValueError:
        return None               # e.g. month 13 / day 45 from a mis-OCR'd digit


def _nvr_clock_tz() -> Optional[tzinfo]:
    """The timezone the burned-in clock is painted in — VA_NVR_TZ if set, else the
    system-local rules. Mirrors `sources.nvr._tz()` (the canonical NVR-clock tz
    convention); duplicated here to avoid an import cycle with nvr.py."""
    name = os.environ.get("VA_NVR_TZ")
    if name:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    return None


def _to_epoch(naive: datetime, tz: Optional[tzinfo]) -> float:
    """Localize a naive wall-clock reading to a UTC epoch using the NVR clock tz
    (None = system-local rules, DST-aware) so it can be compared to the request's
    UTC `start_epoch`. Without this a correct clip would read HOURS off its request.

    The DST fall-back hour maps one wall-clock to two epochs 3600 s apart; this
    keeps the default (fold 0). That 1 h ambiguity does NOT need disambiguating
    here: the OCR-path gate tolerance (`nvr.CLOCK_OCR_TOL_S`, 12 h) is far wider
    than one hour, so either fold is well within tolerance — only a wrong-DAY /
    wrong-WEEK skew trips the gate."""
    if tz is not None:
        return naive.replace(tzinfo=tz).timestamp()
    return naive.replace(tzinfo=None).astimezone().timestamp()


def clock_readings_from_texts(
    frames: Sequence[Tuple[float, str, float]],
    tz: Optional[tzinfo] = None,
    *,
    min_confidence: float = MIN_OCR_CONFIDENCE,
    min_agree: int = MIN_AGREE_FRAMES,
    agree_tol_s: float = AGREE_TOL_S,
) -> Tuple[ClockReading, ...]:
    """Turn per-frame OCR text into the burned-in `ClockReading`s the pure verifier
    gates over — PARSE + NOISE-FILTER in one pure, unit-testable function.

    `frames` is ``(t_seconds, ocr_text, confidence)`` per inspected head frame. This
    function does NOISE reduction only; it is deliberately request-BLIND — deciding
    which reads are *aligned* vs *foreign* (against `start_epoch`) and whether a
    foreign run is a trimmable head or a reject is the pure verifier's job, over the
    readings emitted here. The split matters: pre-filtering to a single "consensus"
    cluster here would DROP an aligned tail and turn a trimmable cross-camera head
    (which carries its own ~7-day-old clock across a majority of the short inspected
    window) into an all-foreign signal the verifier can only reject. So instead:

      1. Drop any frame below `min_confidence` or without a parseable Lorex clock.
      2. Cluster the survivors by base epoch (observed − t) and KEEP every reading
         whose cluster has ``>= min_agree`` members — i.e. drop only SINGLETON
         outliers (a lone stale/mis-read frame among agreeing ones) as noise.
      3. If fewer than `min_agree` readings survive, return `()` (fail open) — too
         few agreeing reads to gate on; the OCR-free defences still run.

    The kept readings are returned in TIME ORDER for the verifier's prefix logic: a
    foreign head followed by an aligned tail TRIMS at the tail; an all-foreign run
    with no aligned tail REJECTS (wrong-week); an aligned run ACCEPTS. Returning `()`
    leaves `ObservedSignals.clock` empty, so the clock gate is skipped entirely.
    """
    parsed: List[Tuple[float, float]] = []   # (t, observed_epoch)
    for t, text, conf in frames:
        if conf < min_confidence:
            continue
        dt = parse_lorex_clock(text)
        if dt is None:
            continue
        parsed.append((float(t), _to_epoch(dt, tz)))

    if len(parsed) < min_agree:
        return ()

    # Keep every reading that agrees with >= min_agree of the survivors (itself
    # included) — drop only singleton outliers. This preserves BOTH a foreign head
    # cluster AND an aligned tail cluster, so the verifier can trim between them;
    # only a lone mis-read frame is discarded as noise.
    bases = [ep - t for (t, ep) in parsed]
    kept = [
        i for i, bi in enumerate(bases)
        if sum(1 for bj in bases if abs(bj - bi) <= agree_tol_s) >= min_agree
    ]
    if len(kept) < min_agree:
        return ()
    return tuple(
        ClockReading(t=parsed[j][0], observed_epoch=parsed[j][1])
        for j in sorted(kept, key=lambda j: parsed[j][0])
    )


# --- the per-frame OCR seam (reuses the Role-10 RapidOCR adapter) ------------

@runtime_checkable
class FrameOcr(Protocol):
    """Reads text off ONE decoded frame. Lets the reader be tested with a fake OCR
    (no RapidOCR) and lets a future backend swap in behind the same seam."""

    def read_frame(self, image) -> Sequence[Tuple[str, float]]:
        """Return ``(text, confidence)`` per detected box (empty if none)."""
        ...


class RapidOcrFrameOcr:
    """Per-frame OCR that REUSES the Role-10 RapidOCR adapter — the same PP-OCR
    models, BGR handling, confidence floor and ModelManager caching — instead of
    standing up a second OCR engine. Built lazily on first use; on ANY build or
    inference failure it disables itself and returns no text, so the reader (and the
    pull) fail OPEN rather than crash on an OCR problem."""

    def __init__(self, load: Optional[dict] = None):
        self._load = load
        self._reader = None
        self._disabled = False

    def read_frame(self, image) -> Sequence[Tuple[str, float]]:
        if self._disabled:
            return ()
        try:
            if self._reader is None:
                from va.adapters.ocr.rapidocr_inproc import RapidOCRReader

                self._reader = RapidOCRReader(self._load)
            # Reuse the adapter's single-frame path (BGR convert + model + the
            # per-box confidence floor). It already drops boxes below its own
            # min_confidence, so what returns is usable text.
            lines = self._reader._read_frame(image)
            return [(ln.text, ln.confidence) for ln in lines]
        except Exception as exc:   # noqa: BLE001 - fail open, never break a pull
            self._disabled = True
            logger.warning(
                "nvr clock gate: OCR backend unavailable (%s) — clock gate "
                "inactive; the OCR-free delivery guards still run", exc)
            return ()


# --- the TimestampReader --------------------------------------------------------

_UNSET = object()


class OcrClockReader:
    """Reads the burned-in wall-clock off a clip's TRUE head frames via OCR and
    returns consensus `ClockReading`s for the delivery verifier's clock gate.

    Implements the `sources.verify.TimestampReader` protocol. NEVER raises — any
    decode/OCR trouble degrades to `()` (fail open), so a flaky reader can never
    turn a good pull into a hard failure."""

    def __init__(self, frame_ocr: FrameOcr, *, tz=_UNSET):
        self._frame_ocr = frame_ocr
        # tz left UNSET is resolved from the environment per read (VA_NVR_TZ); tests
        # can pin one explicitly.
        self._tz = tz

    def read_head_clock(self, path, n_frames: int) -> Sequence[ClockReading]:
        try:
            from va.media.frames import head_clock_frames

            # `n_frames` bounds the OCR calls; the frames are spread over
            # CLOCK_HEAD_SECONDS so a long wrong-week head still shows its aligned
            # tail to the verifier (which then trims rather than rejects).
            heads = head_clock_frames(str(path), CLOCK_HEAD_SECONDS, n_frames)
        except Exception as exc:   # noqa: BLE001 - fail open
            logger.warning("nvr clock gate: could not decode head frames of %s "
                           "(%s) — clock gate inactive for this clip", path, exc)
            return ()

        tz = _nvr_clock_tz() if self._tz is _UNSET else self._tz
        frames: List[Tuple[float, str, float]] = []
        for t, img in heads:
            try:
                boxes = self._frame_ocr.read_frame(img)
            except Exception:      # noqa: BLE001 - one bad frame must not abort
                boxes = ()
            if not boxes:
                continue
            text = " ".join(str(b[0]) for b in boxes)
            conf = max((float(b[1]) for b in boxes), default=0.0)
            frames.append((float(t), text, conf))

        return clock_readings_from_texts(frames, tz)


def default_timestamp_reader(load: Optional[dict] = None):
    """The default burned-in-clock reader for an NVR pull, or None.

    Enablement (mirrors how the real role backends are opt-in, and how the rest of
    the NVR pull is env-configured): the gate auto-enables when the `[ocr]` extra is
    importable, UNLESS ``VA_NVR_CLOCK_GATE`` is set to an off value
    (``off``/``0``/``false``/…). ``on`` forces the attempt. When the extra is
    absent the gate stays inactive (None) and the OCR-free head/stream guards still
    run — exactly the pre-reader behaviour."""
    knob = os.environ.get("VA_NVR_CLOCK_GATE", "").strip().lower()
    if knob and knob in _DISABLED_VALUES:
        return None
    if knob and knob not in _ENABLED_VALUES:
        logger.warning("ignoring unrecognised VA_NVR_CLOCK_GATE=%r — treating as "
                       "auto (enable when the [ocr] extra is available)", knob)

    import importlib.util

    if importlib.util.find_spec("rapidocr") is None:
        msg = ("nvr clock gate inactive: the [ocr] extra is not installed "
               "(install it to enable burned-in-clock verification); the OCR-free "
               "delivery guards still run")
        if knob == "on":
            logger.warning("VA_NVR_CLOCK_GATE=on but " + msg)
        else:
            logger.info(msg)
        return None

    return OcrClockReader(RapidOcrFrameOcr(load))
