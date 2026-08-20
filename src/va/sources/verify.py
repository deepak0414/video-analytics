"""Source-agnostic delivery verification.

The contract a pull path must honour: **never trust that what a source
DELIVERED matches what you REQUESTED — verify the delivery against the request,
and fail closed on a mismatch.** A time-seek into a recorder's ring buffer can
prepend a fragment of stale, cross-camera footage (see
`va-24h-data-integrity-investigation.md`); a device can silently serve a
low-rate sub-stream instead of the requested main stream. Neither is visible to
a duration check — both files are exactly the requested length. This module is
the seam that catches them.

Two halves, deliberately separated so the decision is testable without a
network, OCR or a GPU:

- **`verify_delivery(requested, observed, expected)`** is a PURE function over
  already-extracted signals. It returns accept / trim@k / reject. No I/O.
- The **extractors** (a source's strategy) turn a delivered file into
  `ObservedSignals`: a perceptual-hash of the TRUE first frames against the
  clip's own body (a cross-camera head reads far), the stream's resolution/fps,
  and — where a reader is injected — the burned-in wall-clock the recorder
  paints on each frame. `dhash`/`hamming` here are extractor helpers; the pure
  verifier never touches pixels.

Sources plug their own strategies behind this contract (`DeliveryVerifier`,
`SignalExtractor`, `TimestampReader` Protocols). Today only the NVR recorded
source implements them (`sources/nvr.py`); a future Ring / UniFi / RTSP source
would supply its own signal extractors and reuse this same pure verifier.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, Sequence, Tuple, runtime_checkable


class DeliveryRejected(RuntimeError):
    """A delivered clip could not be reconciled with the request and could not
    be repaired by trimming — the pull must fail closed rather than ingest it."""


# --- the signals the pure verifier decides over ------------------------------

@dataclass(frozen=True)
class RequestedWindow:
    """What the caller asked the source for."""

    stream_id: str          # human label for messages, e.g. "nvr-ch1"
    start_epoch: float      # UTC epoch that the delivered t=0 is supposed to be
    window_len_s: float     # requested duration


@dataclass(frozen=True)
class ExpectedProfile:
    """What a correct delivery for this stream looks like. Device-specific
    CONTENT (the main-stream resolution/fps) is supplied by the caller — never
    baked in here — so this module stays source-agnostic; the thresholds are
    structural budgets, calibrated against the census bands where noted.
    """

    # Whole-stream identity. None = not checked (e.g. unconfigured). A frozenset
    # of accepted main-stream PROFILES, each a (w, h, fps_or_None) tuple. The
    # resolution and fps are matched as a PAIR — a delivery taking one profile's
    # resolution and another's fps is a wrong feed, not a match; fps None in a
    # profile means "any fps at this resolution".
    stream_profiles: Optional[frozenset] = None
    # Head/self identity: a true head frame whose perceptual-hash distance from
    # the clip's own body exceeds this is a foreign fragment. The census on this
    # install measured same-camera frames <=18 and cross-camera >=24, so 20
    # separates them with margin.
    identity_max_distance: int = 20
    # Burned-in-clock gate: a frame whose painted wall-clock is off from
    # (start_epoch + t) by more than this many seconds is wrong-time footage.
    # The census called |Δ| <= 5 s "aligned".
    clock_tol_s: float = 5.0
    # A head trim must leave at least this much footage, else the clip is
    # unrecoverable and we reject (fail closed).
    min_kept_s: float = 1.0


@dataclass(frozen=True)
class HeadFrameSignal:
    """One TRUE decoded head frame and its perceptual distance from the body."""

    t: float          # timestamp (s) of this frame in the delivered clip
    distance: int     # perceptual-hash hamming distance from the body reference


@dataclass(frozen=True)
class ClockReading:
    """The recorder's burned-in wall-clock read off one frame."""

    t: float               # frame time (s) in the delivered clip
    observed_epoch: float  # UTC epoch the overlay showed at that frame


@dataclass(frozen=True)
class ObservedSignals:
    """What the extractors measured on the delivered file."""

    resolution: Optional[Tuple[int, int]] = None
    fps: Optional[int] = None
    head: Tuple[HeadFrameSignal, ...] = ()
    clock: Tuple[ClockReading, ...] = ()


@dataclass(frozen=True)
class DeliveryVerdict:
    action: str                                   # "accept" | "trim" | "reject"
    trim_before_s: float = 0.0                    # drop [0, trim_before_s)
    reasons: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def accepted(self) -> bool:
        return self.action == "accept"

    @property
    def rejected(self) -> bool:
        return self.action == "reject"


# --- the pure decision -------------------------------------------------------

def verify_delivery(
    requested: RequestedWindow,
    observed: ObservedSignals,
    expected: ExpectedProfile,
) -> DeliveryVerdict:
    """Decide whether a delivered clip matches the request. Pure — no I/O.

    Order of defences (any reject is final; a wrong stream can't be trimmed):
      1. Whole-stream identity — resolution/fps must match the expected
         main-stream profile. Catches a sub-stream / wrong-feed substitution.
      2. Head/self identity — a foreign fragment at the head reads far from the
         clip's own body under a perceptual hash. Trim past it.
      3. Burned-in clock — a frame whose painted wall-clock is off from
         (start_epoch + t) is wrong-time footage. Trim past it.
    A head that runs foreign to the end of what was inspected, or a trim that
    would leave less than `min_kept_s`, is unrecoverable → reject (fail closed).
    """
    # 1. Whole-stream identity. A mismatch is the whole clip, not a head — the
    #    device served a different stream; trimming cannot help. Resolution and
    #    fps are matched together as a (w, h, fps) PAIR so two configured
    #    profiles cannot cross-match into a wrong feed.
    if expected.stream_profiles is not None and observed.resolution is not None:
        w, h = observed.resolution

        def _matches(profile) -> bool:
            pw, ph, pfps = profile
            if (pw, ph) != (w, h):
                return False
            return pfps is None or observed.fps is None or pfps == observed.fps

        if not any(_matches(p) for p in expected.stream_profiles):
            return DeliveryVerdict("reject", 0.0, (
                f"delivered stream {w}x{h}@{observed.fps} is not {requested.stream_id}'s "
                f"expected main-stream profile — refusing (sub-stream / wrong feed)",
            ))

    reasons: list[str] = []
    trim = 0.0

    # 2. Head/self identity. Trim to the first frame AFTER the last foreign one
    #    in the inspected head (conservative: handles a non-prefix foreign frame
    #    within the head window, not just a clean prefix).
    verdict = _head_trim(
        observed.head,
        lambda h: h.distance > expected.identity_max_distance,
        lambda foreign: (
            f"{len(foreign)} head frame(s) differ from the clip body beyond the "
            f"identity band (max distance {max(h.distance for h in foreign)}) — "
            f"cross-source lead-in"
        ),
        "the inspected head does not end in a clip-body-matching frame — no "
        "verified-clean start found in the sampled head (fail closed)",
    )
    if verdict.rejected:
        return verdict
    if verdict.action == "trim":
        trim = max(trim, verdict.trim_before_s)
        reasons.extend(verdict.reasons)

    # 3. Burned-in clock (only when a reader was injected — `clock` is empty
    #    otherwise, and this half is skipped).
    def _skew(c: ClockReading) -> float:
        return abs(c.observed_epoch - (requested.start_epoch + c.t))

    verdict = _head_trim(
        observed.clock,
        lambda c: _skew(c) > expected.clock_tol_s,
        lambda foreign: (
            f"burned-in clock off by up to {max(_skew(c) for c in foreign):.0f}s "
            f"on {len(foreign)} frame(s) — stale-ring / wrong-time footage"
        ),
        "the inspected head does not end in an aligned clock reading — "
        "wrong-time footage with no verified-clean start (fail closed)",
    )
    if verdict.rejected:
        return verdict
    if verdict.action == "trim":
        trim = max(trim, verdict.trim_before_s)
        reasons.extend(verdict.reasons)

    if trim <= 0.0:
        return DeliveryVerdict("accept", 0.0, ("delivery matches the request",))
    if requested.window_len_s - trim < expected.min_kept_s:
        return DeliveryVerdict("reject", 0.0, tuple(reasons) + (
            f"trimming the verified-bad head to t={trim:.3f}s would leave < "
            f"{expected.min_kept_s:.1f}s of footage — unrecoverable (fail closed)",
        ))
    return DeliveryVerdict("trim", trim, tuple(reasons))


def _head_trim(signals, is_foreign, foreign_reason, all_foreign_reason) -> DeliveryVerdict:
    """Shared prefix/head-trim logic for the head-identity and clock signals.

    Returns accept (no foreign frames), trim (to just past the last foreign
    frame inspected), or reject (foreign runs to the end of the inspection —
    a clean start could not be located, so fail closed)."""
    if not signals:
        return DeliveryVerdict("accept")
    ordered = sorted(signals, key=lambda s: s.t)
    foreign = [s for s in ordered if is_foreign(s)]
    if not foreign:
        return DeliveryVerdict("accept")
    last_foreign_t = max(s.t for s in foreign)
    after = [s.t for s in ordered if s.t > last_foreign_t]
    if not after:
        return DeliveryVerdict("reject", 0.0, (all_foreign_reason,))
    return DeliveryVerdict("trim", min(after), (foreign_reason(foreign),))


# --- extractor helpers (pixel math; NOT part of the pure verifier) -----------

def dhash(image, hash_size: int = 8) -> int:
    """Row-wise difference perceptual hash of a PIL image. Solid-colour frames
    hash to 0 (no gradient) — synthetic tests must use structured frames."""
    import numpy as np

    small = np.asarray(image.convert("L").resize((hash_size + 1, hash_size)),
                       dtype=np.int16)
    bits_grid = small[:, :-1] > small[:, 1:]   # each pixel brighter than its right
    bits = 0
    for bit in bits_grid.reshape(-1):
        bits = (bits << 1) | int(bit)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


# --- injectable seams (a future source implements these) ---------------------

@runtime_checkable
class DeliveryVerifier(Protocol):
    def __call__(
        self,
        requested: RequestedWindow,
        observed: ObservedSignals,
        expected: ExpectedProfile,
    ) -> DeliveryVerdict: ...


@runtime_checkable
class SignalExtractor(Protocol):
    """Turns a delivered file + request into ObservedSignals for the verifier."""

    def observe(self, path, requested: RequestedWindow) -> ObservedSignals: ...


@runtime_checkable
class TimestampReader(Protocol):
    """Reads the burned-in wall-clock off the head frames of a delivered clip
    (e.g. via OCR). Injected where available; absent → the clock gate is
    skipped and the OCR-free defences still run."""

    def read_head_clock(self, path, n_frames: int) -> Sequence[ClockReading]: ...
