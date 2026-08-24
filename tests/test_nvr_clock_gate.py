"""Default burned-in-clock TimestampReader — the census's "mandatory item 1".

The delivery verifier (`sources/verify.py`) shipped a burned-in-clock gate as an
injectable seam with NO default reader (PR #43), so same-camera WRONG-WEEK footage
slipped through. `sources/ocr_clock.py` supplies that reader: OCR the Lorex overlay
off the TRUE head frames, parse it, take a cross-frame consensus, and hand the pure
verifier the readings it gates over.

All offline, all synthetic — the reader is driven by a FAKE per-frame OCR (no
RapidOCR); the one real-OCR check is opt-in (`RUN_OCR_CLOCK=1`). The tests pin the
policy from `va-24h-data-integrity-investigation.md`: reject a CONFIDENT, LARGE,
CONSISTENT wrong-week mismatch; FAIL OPEN on anything unreadable, low-confidence, or
disagreeing (the CLAUDE.md rule: never false-reject good footage on OCR noise). The
pre-reader gap is reproduced alongside the fix (a regression test must reproduce the
original failure).
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from va.media.synth import write_frames_video
from va.sources.nvr import CLOCK_OCR_TOL_S, CLOCK_TOL_S, NvrRecordedSource
from va.sources.ocr_clock import (
    OcrClockReader,
    RapidOcrFrameOcr,
    clock_readings_from_texts,
    default_timestamp_reader,
    parse_lorex_clock,
)
from va.sources.verify import (
    DeliveryRejected,
    ExpectedProfile,
    HeadFrameSignal,
    ObservedSignals,
    RequestedWindow,
    verify_delivery,
)

DAY = 86400.0
UTC = timezone.utc
T0 = datetime(2026, 8, 10, 1, 0, 0, tzinfo=UTC)   # requested window start
E = T0.timestamp()                                 # its UTC epoch (start_epoch)


def _overlay(epoch: float, tz=UTC) -> str:
    """A Lorex-format overlay string for the wall-clock `tz` shows at `epoch`."""
    return datetime.fromtimestamp(epoch, tz=tz).strftime("%m-%d-%Y %I:%M:%S %p")


@pytest.fixture(autouse=True)
def _clean_nvr_env(monkeypatch):
    """Keep the operator's real-pull env off these synthetic tests: an ambient
    VA_NVR_MAIN_STREAM would refuse the 64x64 clips as wrong-stream, and the
    conftest forces VA_NVR_CLOCK_GATE=off for the suite (the wiring tests override
    it explicitly)."""
    monkeypatch.delenv("VA_NVR_MAIN_STREAM", raising=False)


# --- pure parse: the Lorex overlay -----------------------------------------

def test_parse_lorex_pm():
    assert parse_lorex_clock("08-04-2026 09:35:39 PM") == datetime(2026, 8, 4, 21, 35, 39)


def test_parse_lorex_am_noon_and_midnight():
    assert parse_lorex_clock("08-04-2026 12:00:05 AM") == datetime(2026, 8, 4, 0, 0, 5)
    assert parse_lorex_clock("08-04-2026 12:00:05 PM") == datetime(2026, 8, 4, 12, 0, 5)
    assert parse_lorex_clock("08-04-2026 01:07:00 AM") == datetime(2026, 8, 4, 1, 7, 0)


def test_parse_ignores_surrounding_ocr_noise():
    # Full-frame OCR returns the camera label + channel name around the clock.
    assert parse_lorex_clock("Ch1 Front Door  08-04-2026 09:35:39 PM  REC") == \
        datetime(2026, 8, 4, 21, 35, 39)


def test_parse_rejects_garbage_out_of_range_and_missing_meridiem():
    assert parse_lorex_clock("no timestamp here") is None
    assert parse_lorex_clock("") is None
    # meridiem REQUIRED — an OCR miss there is unreadable, never a 12 h-shifted read
    assert parse_lorex_clock("08-04-2026 09:35:39") is None
    # a mis-OCR'd digit that yields an impossible date/time is not a clock
    assert parse_lorex_clock("13-45-2026 09:35:39 PM") is None
    assert parse_lorex_clock("08-04-2026 15:35:39 PM") is None   # hour > 12 on a 12h clock


def test_parse_rejects_dropped_padding_digit_rather_than_shifting_days():
    # The real-frame failure the reviewer found on .va-24h: RapidOCR consistently
    # DROPS a zero-padded leading digit. Month/day/hour are matched as `\d{2}`, so a
    # single-digit field is a NON-parse (fail open), NOT a ~10-day / hours-off read.
    assert parse_lorex_clock("08-1-2026 10:55:58 PM") is None    # 08-11 -> 08-1 (day)
    assert parse_lorex_clock("0810-2026 09:03:28 PM") is None    # mo-day separator gone
    assert parse_lorex_clock("8-11-2026 10:55:58 PM") is None    # 08 -> 8 (month)
    assert parse_lorex_clock("08-11-2026 1:55:58 PM") is None    # 10/11/12 -> 1 (hour)
    # the correctly zero-padded overlay still parses
    assert parse_lorex_clock("08-11-2026 10:55:58 PM") == datetime(2026, 8, 11, 22, 55, 58)


# --- pure consensus + gate decision ----------------------------------------

def _req(window_len=30.0):
    return RequestedWindow("nvr-ch1", E, window_len)


def _decide(readings, tol=CLOCK_OCR_TOL_S, window_len=30.0):
    return verify_delivery(_req(window_len), ObservedSignals(clock=readings),
                           ExpectedProfile(clock_tol_s=tol))


def test_consensus_aligned_reads_are_accepted():
    frames = [(i * 0.3, _overlay(E + i * 0.3), 0.95) for i in range(6)]
    readings = clock_readings_from_texts(frames, UTC)
    assert len(readings) == 6
    assert _decide(readings).accepted


def test_consensus_wrong_week_reads_are_rejected():
    """The whole point: every head frame reads ~7 days off -> the gate rejects."""
    frames = [(i * 0.3, _overlay(E - 7 * DAY + i * 0.3), 0.95) for i in range(6)]
    readings = clock_readings_from_texts(frames, UTC)
    assert len(readings) == 6           # all agree on the wrong week
    assert _decide(readings).rejected


def test_garbled_text_fails_open():
    frames = [(i * 0.3, "CH1 :: no legible clock ::", 0.95) for i in range(6)]
    assert clock_readings_from_texts(frames, UTC) == ()
    assert _decide(clock_readings_from_texts(frames, UTC)).accepted   # gate skipped


def test_low_confidence_reads_fail_open():
    # A perfectly wrong-week clock but every box below the OCR floor -> not gated on.
    frames = [(i * 0.3, _overlay(E - 7 * DAY), 0.2) for i in range(6)]
    assert clock_readings_from_texts(frames, UTC) == ()


def test_no_agreeing_cluster_fails_open():
    """Genuine noise: every legible frame reads a DIFFERENT time (no two agree), so
    no reading clears the agree floor -> `()` -> the gate is skipped (fail open)."""
    frames = [(i * 0.1, _overlay(E + i * 3600), 0.95) for i in range(4)]  # 1h apart
    assert clock_readings_from_texts(frames, UTC) == ()


def test_foreign_prefix_then_aligned_tail_is_trimmed_not_rejected():
    """The MAJOR fix: a wrong-week HEAD that is a MAJORITY of the short inspected
    window (5 of 8 head frames, carrying its own ~7-day-old clock) followed by an
    aligned tail must be TRIMMED at the tail — the same recovery the dHash head gate
    does — not rejected. The old 'emit only the largest cluster' consensus dropped
    the aligned minority, leaving an all-foreign signal the verifier could only
    reject; now every >= MIN_AGREE cluster is kept in time order so the verifier
    trims between them."""
    stale = [(i * 0.05, _overlay(E - 7 * DAY + i * 0.05), 0.95) for i in range(5)]
    aligned = [(0.25 + i * 0.05, _overlay(E + 0.25 + i * 0.05), 0.95) for i in range(3)]
    readings = clock_readings_from_texts(stale + aligned, UTC)
    assert len(readings) == 8                       # aligned tail NOT dropped
    verdict = _decide(readings)
    assert verdict.action == "trim"
    assert verdict.trim_before_s == pytest.approx(0.25, abs=1e-6)


def test_head_identity_and_clock_both_foreign_prefix_trims_not_rejects():
    """The combination the reviewer flagged as uncovered: a cross-camera lead-in of
    5 of 8 head frames reads foreign under BOTH the dHash head-identity signal AND
    its own (~7-day-old) burned-in clock, followed by 3 clean/aligned frames. The
    verifier must TRIM to the clean tail (max of the two signals' trims), not reject
    — a reject here would fail-close a delivery the head gate alone recovers."""
    head = tuple(HeadFrameSignal(t=i * 0.05, distance=40) for i in range(5)) + \
        tuple(HeadFrameSignal(t=0.25 + i * 0.05, distance=2) for i in range(3))
    clock = clock_readings_from_texts(
        [(i * 0.05, _overlay(E - 7 * DAY + i * 0.05), 0.95) for i in range(5)]
        + [(0.25 + i * 0.05, _overlay(E + 0.25 + i * 0.05), 0.95) for i in range(3)],
        UTC,
    )
    verdict = verify_delivery(
        _req(), ObservedSignals(head=head, clock=clock),
        ExpectedProfile(clock_tol_s=CLOCK_OCR_TOL_S),
    )
    assert verdict.action == "trim"
    assert verdict.trim_before_s == pytest.approx(0.25, abs=1e-6)


def test_a_lone_outlier_frame_is_dropped_not_acted_on():
    """OCR-noise robustness: one stale/mis-read frame among a confident majority is
    DROPPED, never turned into a reject."""
    frames = [
        (0.0, _overlay(E), 0.95),
        (0.1, _overlay(E), 0.95),
        (0.2, _overlay(E), 0.95),
        (0.3, _overlay(E - 7 * DAY), 0.95),   # the outlier
    ]
    readings = clock_readings_from_texts(frames, UTC)
    assert len(readings) == 3                 # outlier dropped
    assert _decide(readings).accepted


def test_a_single_confident_read_is_not_enough():
    frames = [(0.0, _overlay(E - 7 * DAY), 0.95)]   # only one legible frame
    assert clock_readings_from_texts(frames, UTC) == ()   # need >= 2 to act


def test_coarse_tolerance_accepts_legit_drift_that_a_tight_band_would_reject():
    """Legitimate loadfile alignment DRIFT reaches ~56 s (census "drift" band) and
    is GOOD footage. The coarse OCR-path tolerance must accept it; the 5 s "aligned"
    band would wrongly reject it — which is why the reader path widens the gate."""
    frames = [(i * 0.3, _overlay(E + 56 + i * 0.3), 0.95) for i in range(6)]
    readings = clock_readings_from_texts(frames, UTC)
    assert readings
    assert _decide(readings, tol=CLOCK_OCR_TOL_S, window_len=120.0).accepted
    assert _decide(readings, tol=CLOCK_TOL_S, window_len=120.0).rejected


def test_readings_are_localized_with_the_nvr_timezone():
    """The overlay is painted in NVR-LOCAL time; tz-aware localization must produce
    the RIGHT epoch (aligned to the request), not one hours off."""
    from zoneinfo import ZoneInfo

    la = ZoneInfo("America/Los_Angeles")
    local_wall = _overlay(E, tz=la)                    # LA wall-clock for instant E
    frames = [(0.0, local_wall, 0.95), (0.3, local_wall, 0.95)]

    aligned = clock_readings_from_texts(frames, la)    # correct tz -> epoch == request
    assert aligned and aligned[0].observed_epoch == pytest.approx(E, abs=1.0)
    assert _decide(aligned).accepted


def test_a_consistent_ocr_time_field_misread_within_the_band_is_tolerated():
    """MINOR-3 robustness: RapidOCR errs the SAME way across the near-identical head,
    so a misread minute/hour digit (here 09:35 -> 09:55, +20 min) agrees across every
    frame — a confident, all-foreign signal. At a tight 600 s tolerance that would
    false-reject GOOD footage; the OCR-path 12 h band (nvr.CLOCK_OCR_TOL_S, the census
    foreign floor) forgives it while still catching wrong-day/wrong-week."""
    frames = [(i * 0.2, _overlay(E + 20 * 60 + i * 0.2), 0.95) for i in range(6)]
    readings = clock_readings_from_texts(frames, UTC)
    assert readings
    assert _decide(readings).accepted                       # 20 min < 12 h -> tolerated
    assert _decide(readings, tol=600.0).rejected            # the old tight band rejected


def test_am_pm_flip_plus_drift_stays_within_the_tolerance():
    """A consistent AM/PM OCR flip is EXACTLY 12 h; combined with legitimate loadfile
    drift in the same direction it exceeds a plain 12 h band and would false-reject
    GOOD footage. The tolerance carries a margin over 12 h so this is accepted, while
    wrong-week (~7 d) still rejects — this pins that margin."""
    off = 12 * 3600 + 40                                     # flip (12 h) + 40 s drift
    frames = [(i * 0.2, _overlay(E + off + i * 0.2), 0.95) for i in range(6)]
    readings = clock_readings_from_texts(frames, UTC)
    assert readings
    assert _decide(readings).accepted                       # within 12 h + margin
    assert _decide(readings, tol=12 * 3600).rejected        # a plain 12 h band rejects it
    wrongweek = [(i * 0.2, _overlay(E - 7 * DAY + i * 0.2), 0.95) for i in range(6)]
    assert _decide(clock_readings_from_texts(wrongweek, UTC)).rejected  # still caught


def test_dst_fall_back_fold_error_is_absorbed_by_the_coarse_tolerance():
    """On the DST fall-back night one local wall-clock maps to TWO epochs 3600 s apart
    (fold 0 = the still-DST 01:xx, fold 1 = the standard-time one). The reader keeps
    the default fold 0, so a correct clip in the SECOND 01:xx hour reads 3600 s early —
    but 1 h is far inside the 12 h OCR-path tolerance, so it is ACCEPTED, not the
    once-a-year false-reject a tight band would cause. No fold disambiguation needed."""
    from zoneinfo import ZoneInfo

    la = ZoneInfo("America/Los_Angeles")
    naive = datetime(2026, 11, 1, 1, 30, 0)            # US fall-back night 2026
    e_pdt = naive.replace(tzinfo=la, fold=0).timestamp()   # first 01:30 (PDT)
    e_pst = naive.replace(tzinfo=la, fold=1).timestamp()   # second 01:30 (PST)
    assert e_pst - e_pdt == 3600.0                     # genuinely ambiguous hour
    overlay = "11-01-2026 01:30:00 AM"
    frames = [(0.0, overlay, 0.95), (0.1, overlay, 0.95)]
    req = RequestedWindow("nvr-ch1", e_pst, 30.0)      # footage is the SECOND 01:30

    readings = clock_readings_from_texts(frames, la)   # default fold 0 -> 3600 s off
    assert readings[0].observed_epoch == pytest.approx(e_pdt)
    assert verify_delivery(req, ObservedSignals(clock=readings),
                           ExpectedProfile(clock_tol_s=CLOCK_OCR_TOL_S)).accepted


# --- the reader over real head frames (fake OCR, no RapidOCR) ----------------

def _gradient(direction: str) -> np.ndarray:
    ramp = np.linspace(0, 255, 64).astype(np.uint8)
    if direction == "dec":
        ramp = ramp[::-1]
    row = np.tile(ramp, (64, 1))
    return np.stack([row, row, row], axis=-1)


BODY = _gradient("dec")   # a uniform, hashable body: head-identity gate stays clean


class _FakeFrameOcr:
    """Returns a fixed overlay (or nothing) for every frame — stands in for the
    RapidOCR per-frame path so the offline suite needs no real OCR."""

    def __init__(self, text, conf=0.95):
        self._text, self._conf = text, conf

    def read_frame(self, image):
        return () if self._text is None else [(self._text, self._conf)]


def _clip(tmp_path):
    return write_frames_video(tmp_path / "cut.mp4", [(BODY, 6.0)], fps=10)


def test_ocr_clock_reader_rejects_a_wrong_week_clip(tmp_path, monkeypatch):
    """Integration: the real OcrClockReader (fake OCR reading ~7 days off) drives
    NvrRecordedSource's extract->verify path to a fail-closed rejection. The body is
    uniform so head-identity is clean — only the clock gate can reject."""
    monkeypatch.setenv("VA_NVR_TZ", "UTC")
    reader = OcrClockReader(_FakeFrameOcr(_overlay(E - 7 * DAY)))
    src = NvrRecordedSource(timestamp_reader=reader)
    with pytest.raises(DeliveryRejected):
        src._verify_and_trim(Path(_clip(tmp_path)), 1, T0, T0 + timedelta(seconds=6))


def test_pre_reader_default_accepts_the_same_wrong_week_clip(tmp_path, monkeypatch):
    """Reproduction of the gap this reader closes: with NO clock reader (the pre-PR
    default) the burned-in clock is never read, so the same head-clean wrong-week
    clip is ACCEPTED — the hole the census called mandatory item 1."""
    monkeypatch.setenv("VA_NVR_TZ", "UTC")
    src = NvrRecordedSource(timestamp_reader=None)      # gate off = pre-reader
    out = src._verify_and_trim(Path(_clip(tmp_path)), 1, T0, T0 + timedelta(seconds=6))
    assert str(out) == str(_clip(tmp_path))


def test_ocr_clock_reader_accepts_an_aligned_clip(tmp_path, monkeypatch):
    monkeypatch.setenv("VA_NVR_TZ", "UTC")
    clip = _clip(tmp_path)
    reader = OcrClockReader(_FakeFrameOcr(_overlay(E)))
    src = NvrRecordedSource(timestamp_reader=reader)
    out = src._verify_and_trim(Path(clip), 1, T0, T0 + timedelta(seconds=6))
    assert str(out) == str(clip)


def test_ocr_clock_reader_fails_open_on_an_unreadable_clock(tmp_path, monkeypatch):
    monkeypatch.setenv("VA_NVR_TZ", "UTC")
    clip = _clip(tmp_path)
    reader = OcrClockReader(_FakeFrameOcr(None))         # OCR reads no text
    src = NvrRecordedSource(timestamp_reader=reader)
    out = src._verify_and_trim(Path(clip), 1, T0, T0 + timedelta(seconds=6))
    assert str(out) == str(clip), "an unreadable clock must not reject good footage"


class _RaisingFrameOcr:
    """A per-frame OCR backend that THROWS on every frame (e.g. an onnxruntime
    crash) — to pin the fail-open policy for a broken OCR engine."""

    def read_frame(self, image):
        raise RuntimeError("OCR backend blew up")


def test_reader_fails_open_when_the_ocr_backend_raises(tmp_path, monkeypatch):
    """Fail-open is THE policy of this change: a crashing OCR engine must yield NO
    reading (clip returned unchanged), never a failed pull / wedged watcher. Covers
    OcrClockReader's per-frame guard, which no other test exercised."""
    monkeypatch.setenv("VA_NVR_TZ", "UTC")
    clip = _clip(tmp_path)
    src = NvrRecordedSource(timestamp_reader=OcrClockReader(_RaisingFrameOcr()))
    out = src._verify_and_trim(Path(clip), 1, T0, T0 + timedelta(seconds=6))
    assert str(out) == str(clip), "a crashing OCR backend must not reject good footage"


def test_rapidocr_frame_ocr_self_disables_on_backend_failure(monkeypatch):
    """The other fail-open branch: RapidOcrFrameOcr must swallow a backend build/
    inference failure, return no text, and disable itself — never propagate."""
    import va.adapters.ocr.rapidocr_inproc as rapid

    def _boom(*a, **k):
        raise RuntimeError("cannot build RapidOCR")

    monkeypatch.setattr(rapid, "RapidOCRReader", _boom)
    f = RapidOcrFrameOcr()
    assert list(f.read_frame(object())) == []            # fail open, no raise
    assert f._disabled is True                            # self-disabled for next frame


def test_reader_never_raises_when_head_frames_cannot_be_decoded(tmp_path):
    reader = OcrClockReader(_FakeFrameOcr(_overlay(E)))
    assert tuple(reader.read_head_clock(str(tmp_path / "absent.mp4"), 8)) == ()


def test_head_clock_frames_spans_the_window_from_frame_zero(tmp_path):
    """Pin the head-window fix: the clock inspects a ~1.5 s span sampled from frame 0,
    NOT the first ~0.4 s. A revert to first_frames(0.4 s) fails this."""
    from va.media.frames import head_clock_frames

    clip = write_frames_video(tmp_path / "span.mp4", [(BODY, 3.0)], fps=20)
    heads = head_clock_frames(str(clip), 1.5, 8)
    ts = [t for t, _ in heads]
    assert len(heads) == 8
    assert ts == sorted(ts)
    assert ts[0] == 0.0
    assert 1.4 <= ts[-1] <= 1.5, "spans ~1.5 s (not the ~0.4 s of first_frames)"


class _BrightnessKeyedOcr:
    """Content-keyed fake OCR: dark frames read a wrong-week clock, bright frames read
    an aligned clock. Lets a synthetic head->body transition drive the real reader over
    real decoded frames (no dHash gate involved — this isolates the clock span)."""

    def __init__(self, head_epoch, body_epoch):
        self._head, self._body = _overlay(head_epoch), _overlay(body_epoch)

    def read_frame(self, image):
        mean = float(np.asarray(image).mean())
        return [(self._head if mean < 128 else self._body, 0.95)]


def test_reader_trims_a_1s_stale_head_via_the_1_5s_window(tmp_path, monkeypatch):
    """The round-3 fix, end-to-end at the reader: a 1.0 s wrong-week head over an
    aligned body must TRIM at the body (~1.0 s), not reject. The reader's ~1.5 s window
    reaches the aligned tail; a 0.4 s window would see only the head and REJECT."""
    monkeypatch.setenv("VA_NVR_TZ", "UTC")
    dark = np.full((64, 64, 3), 30, dtype=np.uint8)     # HEAD: mean 30 -> wrong-week
    bright = np.full((64, 64, 3), 220, dtype=np.uint8)  # BODY: mean 220 -> aligned
    clip = write_frames_video(tmp_path / "headbody.mp4",
                              [(dark, 1.0), (bright, 2.0)], fps=20)
    reader = OcrClockReader(_BrightnessKeyedOcr(E - 7 * DAY, E + 1.5))
    readings = reader.read_head_clock(str(clip), 8)
    verdict = verify_delivery(RequestedWindow("nvr-ch1", E, 30.0),
                              ObservedSignals(clock=readings),
                              ExpectedProfile(clock_tol_s=CLOCK_OCR_TOL_S))
    assert verdict.action == "trim", "the 1.5 s window must reach the aligned tail"
    assert 0.9 <= verdict.trim_before_s <= 1.3


# --- enablement / wiring ----------------------------------------------------

def test_default_reader_disabled_by_knob(monkeypatch):
    monkeypatch.setenv("VA_NVR_CLOCK_GATE", "off")
    assert default_timestamp_reader() is None


def test_default_reader_none_when_ocr_backend_missing(monkeypatch):
    monkeypatch.delenv("VA_NVR_CLOCK_GATE", raising=False)
    import importlib.util
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: None)
    assert default_timestamp_reader() is None


def test_default_reader_forced_on_without_ocr_still_degrades(monkeypatch):
    monkeypatch.setenv("VA_NVR_CLOCK_GATE", "on")
    import importlib.util
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: None)
    assert default_timestamp_reader() is None    # warns, never crashes a pull


def test_default_reader_present_when_available(monkeypatch):
    pytest.importorskip("rapidocr")
    monkeypatch.delenv("VA_NVR_CLOCK_GATE", raising=False)
    reader = default_timestamp_reader()
    assert isinstance(reader, OcrClockReader)     # lazily built — model not loaded here
    assert hasattr(reader, "read_head_clock")


def test_nvr_source_auto_wires_the_default_reader(monkeypatch):
    sentinel = object()
    monkeypatch.setattr("va.sources.ocr_clock.default_timestamp_reader",
                        lambda *a, **k: sentinel)
    assert NvrRecordedSource()._timestamp_reader is sentinel
    # an explicit None forces the gate OFF; an explicit reader is honoured
    assert NvrRecordedSource(timestamp_reader=None)._timestamp_reader is None
    reader = object()
    assert NvrRecordedSource(timestamp_reader=reader)._timestamp_reader is reader


def test_expected_profile_uses_the_coarse_tolerance_only_with_a_reader():
    with_reader = NvrRecordedSource(timestamp_reader=object())
    assert with_reader._expected_profile(1).clock_tol_s == CLOCK_OCR_TOL_S
    no_reader = NvrRecordedSource(timestamp_reader=None)
    assert no_reader._expected_profile(1).clock_tol_s == CLOCK_TOL_S
    # the coarse tolerance sits just above the largest benign error — a 12 h AM/PM
    # flip plus loadfile drift — and far below a ring cycle (~7 d): forgives OCR
    # time-field misreads, catches wrong-day/wrong-week
    assert 12 * 3600 < CLOCK_OCR_TOL_S < 24 * 3600 < 7 * 24 * 3600


# --- opt-in real-OCR check (not needed by the offline suite) -----------------

@pytest.mark.skipif(not os.environ.get("RUN_OCR_CLOCK"),
                    reason="opt-in real-OCR check (set RUN_OCR_CLOCK=1); the offline "
                           "suite drives the reader with a fake OCR")
def test_real_rapidocr_reads_a_rendered_lorex_clock():
    pytest.importorskip("rapidocr")
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (720, 80), (0, 0, 0))
    ImageDraw.Draw(img).text((10, 25), "08-04-2026 09:35:39 PM", fill=(255, 255, 255))
    joined = " ".join(t for t, _ in RapidOcrFrameOcr().read_frame(img))
    parsed = parse_lorex_clock(joined)
    assert parsed is not None and parsed.date() == datetime(2026, 8, 4).date()
