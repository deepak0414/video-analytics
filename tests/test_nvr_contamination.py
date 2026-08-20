"""Delivery-verification regression tests (the `.va-24h` contamination fix).

Reproduces the census failure (`va-24h-data-integrity-investigation.md`): the
NVR time-seek prepended a stale, cross-camera head to ~30% of pulls, and the
duration check passed every one because a contaminated clip is exactly the
requested length. These tests pin the source-agnostic verifier
(`sources/verify.py`) and the NVR strategy that feeds it — all offline, all
synthetic (no NVR / OCR / GPU). Each reproduces the pre-fix behaviour it guards
against (the CLAUDE.md rule: a regression test that can't reproduce the original
failure is decoration).
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from va.media.frames import first_frames, frames_at, probe
from va.media.synth import write_frames_video
from va.sources.nvr import IDENTITY_MAX_DHASH, NvrRecordedSource
from va.sources.verify import (
    ClockReading,
    DeliveryRejected,
    ExpectedProfile,
    HeadFrameSignal,
    ObservedSignals,
    RequestedWindow,
    dhash,
    hamming,
    verify_delivery,
)

DAY = 86400.0
E = datetime(2026, 8, 10, 1, 0, 0, tzinfo=timezone.utc).timestamp()  # start_epoch
T0 = datetime(2026, 8, 10, 1, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 8, 10, 1, 0, 30, tzinfo=timezone.utc)            # 30 s window


def _gradient(direction: str) -> np.ndarray:
    """A horizontal brightness ramp — real spatial STRUCTURE so a perceptual
    hash can tell two frames apart. 'inc' hashes to all-0, 'dec' to all-1
    (hamming distance 64 between them; a solid colour would hash to 0 either
    way and be indistinguishable — the reason synth frames must be structured)."""
    ramp = np.linspace(0, 255, 64).astype(np.uint8)
    if direction == "dec":
        ramp = ramp[::-1]
    row = np.tile(ramp, (64, 1))
    return np.stack([row, row, row], axis=-1)


FOREIGN = _gradient("inc")   # stands in for the cross-camera lead-in
BODY = _gradient("dec")      # stands in for the requested camera's footage


@pytest.fixture(autouse=True)
def _no_ambient_main_stream(monkeypatch):
    """CLAUDE.md tells the operator of this box to export VA_NVR_MAIN_STREAM for
    real pulls; it must not leak into the accept-path tests here (whose 64x64
    synth clips would be refused as wrong-stream, reddening the offline suite —
    the repo's documented env-pollution failure mode). Tests that need it set it
    explicitly after this runs."""
    monkeypatch.delenv("VA_NVR_MAIN_STREAM", raising=False)


# --- the pure verifier: clock gate ------------------------------------------

def test_clock_gate_trims_a_stale_head():
    """A head off by ~7 days (the ring-cycle offset) with an aligned body is
    trimmed to the first aligned frame."""
    observed = ObservedSignals(clock=(
        ClockReading(t=0.0, observed_epoch=E - 7 * DAY),   # foreign
        ClockReading(t=1.0, observed_epoch=E + 1.0),       # aligned
        ClockReading(t=2.0, observed_epoch=E + 2.0),
    ))
    v = verify_delivery(RequestedWindow("nvr-ch1", E, 30.0), observed, ExpectedProfile())
    assert v.action == "trim" and v.trim_before_s == 1.0


def test_clock_gate_rejects_wholly_wrong_time_footage():
    """Every frame off by ~7 days = wrong-week footage throughout → fail closed
    (the census's four wholly-foreign clips)."""
    observed = ObservedSignals(clock=tuple(
        ClockReading(t=float(t), observed_epoch=E - 7 * DAY + t) for t in range(4)))
    v = verify_delivery(RequestedWindow("nvr-ch1", E, 30.0), observed, ExpectedProfile())
    assert v.rejected


def test_clock_gate_accepts_aligned_footage():
    observed = ObservedSignals(clock=tuple(
        ClockReading(t=float(t), observed_epoch=E + t) for t in range(4)))
    v = verify_delivery(RequestedWindow("nvr-ch1", E, 30.0), observed, ExpectedProfile())
    assert v.accepted


def test_pre_fix_path_accepts_the_contaminated_delivery():
    """Reproduction: the OLD pull described a delivery only by its (correct)
    duration. Described with NO extracted signals, the verifier — like the old
    duration-only gate — accepts it. The fix is that the NVR strategy now
    EXTRACTS the head/clock/stream signals so the gate above has something to
    reject; with them empty, a contaminated clip still passes."""
    v = verify_delivery(RequestedWindow("nvr-ch1", E, 30.0),
                        ObservedSignals(), ExpectedProfile())
    assert v.accepted


# --- the pure verifier: stream identity -------------------------------------

def test_stream_identity_rejects_a_substream():
    """A low-rate sub-stream (352x240) served instead of the main stream is
    rejected outright — trimming cannot fix a wrong stream (the census's four
    352x240 clips)."""
    observed = ObservedSignals(resolution=(352, 240), fps=10)
    expected = ExpectedProfile(stream_profiles=frozenset({(2688, 1520, 20)}))
    v = verify_delivery(RequestedWindow("nvr-ch1", E, 30.0), observed, expected)
    assert v.rejected


def test_stream_identity_accepts_the_main_stream():
    observed = ObservedSignals(resolution=(2688, 1520), fps=20)
    expected = ExpectedProfile(stream_profiles=frozenset({(2688, 1520, 20)}))
    assert verify_delivery(RequestedWindow("nvr-ch1", E, 30.0),
                           observed, expected).accepted


def test_stream_identity_matches_resolution_and_fps_as_a_pair():
    """A multi-entry profile must not cross-match: a delivery taking one entry's
    resolution and another's fps is a wrong feed (the pooling bug — a wholly
    foreign clip is self-consistent, so head identity can't catch it)."""
    expected = ExpectedProfile(
        stream_profiles=frozenset({(2688, 1520, 20), (1920, 1080, 15)}))
    req = RequestedWindow("nvr-ch1", E, 30.0)
    assert verify_delivery(
        req, ObservedSignals(resolution=(2688, 1520), fps=20), expected).accepted
    assert verify_delivery(
        req, ObservedSignals(resolution=(2688, 1520), fps=15), expected).rejected  # cross


# --- the pure verifier: head/self identity + unrecoverable trims ------------

def test_head_identity_trims_a_foreign_prefix():
    observed = ObservedSignals(head=(
        HeadFrameSignal(t=0.0, distance=40),
        HeadFrameSignal(t=0.05, distance=38),
        HeadFrameSignal(t=0.10, distance=2),
        HeadFrameSignal(t=0.15, distance=1),
    ))
    v = verify_delivery(RequestedWindow("nvr-ch1", E, 30.0), observed, ExpectedProfile())
    assert v.action == "trim" and v.trim_before_s == 0.10


def test_head_identity_rejects_when_the_whole_inspected_head_is_foreign():
    observed = ObservedSignals(head=tuple(
        HeadFrameSignal(t=i * 0.05, distance=40) for i in range(8)))
    v = verify_delivery(RequestedWindow("nvr-ch1", E, 30.0), observed, ExpectedProfile())
    assert v.rejected


def test_a_trim_that_leaves_too_little_footage_is_unrecoverable():
    """A foreign head that consumes almost the whole (short) window can't be
    salvaged — fail closed rather than store a sliver."""
    observed = ObservedSignals(head=(
        HeadFrameSignal(t=0.0, distance=40),
        HeadFrameSignal(t=0.4, distance=40),
        HeadFrameSignal(t=0.5, distance=1),
    ))
    v = verify_delivery(RequestedWindow("nvr-ch1", E, 1.0),
                        observed, ExpectedProfile(min_kept_s=1.0))
    assert v.rejected


# --- the true-first-frame extractor (the sampler-blindness fix) -------------

def test_first_frames_sees_the_true_marker_frame_zero(tmp_path):
    """A 1-frame foreign head at t=0 must be visible to the extractor. The
    deleted `-vf fps=4` sampler's first output frame was NOT frame 0 (measured
    28-39 dHash from t=0 on the real clips), so a 1-5-frame head was invisible —
    exactly the blindness reproduced here: a sample taken 0.25 s in (where an
    fps-resample lands) sees only the body and would clear the clip."""
    clip = write_frames_video(tmp_path / "clip.mp4",
                              [(FOREIGN, 0.1), (BODY, 3.0)], fps=10)  # 1 marker frame

    head0 = first_frames(str(clip), 8)[0][1]
    assert hamming(dhash(head0), dhash(Image.fromarray(FOREIGN))) == 0, \
        "the extractor must see the true marker frame at t=0"

    mid = frames_at(str(clip), [0.25])[0]     # where a coarse fps sampler lands
    assert hamming(dhash(mid), dhash(Image.fromarray(BODY))) == 0, \
        "sampling past t=0 misses the sub-sample head — the old blindness"


# --- the NVR strategy: verify-and-trim over real clips ----------------------

def _src():
    return NvrRecordedSource()


def test_verify_and_trim_removes_a_foreign_head(tmp_path):
    """Integration: a fabricated two-segment clip (cross-camera head + correct
    body) through the real extract→verify→trim path. The head is removed and the
    body preserved."""
    cut = write_frames_video(tmp_path / "cut.mp4",
                             [(FOREIGN, 0.5), (BODY, 5.0)], fps=10)
    end = T0 + timedelta(seconds=5.5)

    out = _src()._verify_and_trim(Path(cut), 1, T0, end)

    assert Path(out).name.endswith(".verified.mp4")
    assert abs(probe(str(out)).duration_seconds - 5.0) <= 0.3   # ~0.5 s head gone
    body_hash = dhash(Image.fromarray(BODY))
    assert all(hamming(dhash(img), body_hash) == 0
               for _, img in first_frames(str(out), 3)), "head must be clean now"


def test_the_old_duration_gate_alone_would_have_accepted_that_clip(tmp_path):
    """Reproduction of the census defect: the contaminated two-segment clip is
    exactly its requested length, so the duration-only gate (the current shipped
    behaviour before this fix) passes it unchanged."""
    cut = write_frames_video(tmp_path / "cut.mp4",
                             [(FOREIGN, 0.5), (BODY, 5.0)], fps=10)
    dur = _src()._probe_cut(Path(cut))
    window_len = 5.5
    assert dur is not None and abs(dur - window_len) <= 2.0, \
        "duration gate passes the contaminated clip — why duration is not enough"


def test_verify_and_trim_accepts_a_clean_clip_unchanged(tmp_path):
    clean = write_frames_video(tmp_path / "clean.mp4", [(BODY, 5.0)], fps=10)
    out = _src()._verify_and_trim(Path(clean), 1, T0, T0 + timedelta(seconds=5.0))
    assert str(out) == str(clean), "a clean delivery is returned untouched"


def test_verify_and_trim_fails_closed_on_a_wrong_stream(tmp_path, monkeypatch):
    """With the channel's main-stream profile configured, a delivery at the
    wrong resolution raises — the OCR-free guard against a sub-stream swap."""
    monkeypatch.setenv("VA_NVR_MAIN_STREAM", "2688x1520@20")
    clean = write_frames_video(tmp_path / "clean.mp4", [(BODY, 5.0)], fps=10)  # 64x64
    with pytest.raises(DeliveryRejected, match="main-stream profile"):
        _src()._verify_and_trim(Path(clean), 1, T0, T0 + timedelta(seconds=5.0))


def test_pull_fails_closed_when_delivery_cannot_be_verified(tmp_path, monkeypatch):
    """End of the line: a delivery the verifier rejects is treated like a bad
    cut — retried through both phases, then the whole pull RAISES and lands no
    file (fail closed, never a silently contaminated clip)."""
    monkeypatch.setenv("VA_NVR_MAIN_STREAM", "2688x1520@20")   # our synth is 64x64
    monkeypatch.setattr(NvrRecordedSource, "_conn",
                        staticmethod(lambda: ("http://nvr.test", "u", "p")))
    monkeypatch.setattr(NvrRecordedSource, "_stop_load", lambda self, c: None)

    def fake_fetch(self, chan, start, end, work):
        dav = work / "window.dav"
        dav.write_bytes(b"d" * 4096)
        return dav

    def fake_cut(self, raw, a, b, part):     # deliver a real but wrong-stream clip
        write_frames_video(part, [(BODY, b - a)], fps=10)

    monkeypatch.setattr(NvrRecordedSource, "_fetch_window", fake_fetch)
    monkeypatch.setattr(NvrRecordedSource, "_trim_encode", fake_cut)
    monkeypatch.setattr(NvrRecordedSource, "_probe_cut", lambda self, part: 3.0)

    out = tmp_path / "out.mp4"
    short_end = T0 + timedelta(seconds=3)
    with pytest.raises(RuntimeError, match="neither a padded"):
        _src()._pull_window(1, T0, short_end, out)
    assert not out.exists(), "a delivery that fails verification must land no clip"


def test_identity_band_constant_separates_the_census_bands():
    """The shipped threshold sits inside the census gap (same-camera <=18,
    cross-camera >=24)."""
    assert 18 < IDENTITY_MAX_DHASH < 24


def test_parse_main_stream_keeps_pairs_and_fails_closed_on_garbage():
    """Direct coverage of the parser (the round-3 pooling-bug site): each entry's
    resolution+fps stays PAIRED, fps may be omitted, and a non-blank-but-empty
    spec RAISES rather than silently deactivating the gate (fail open)."""
    from va.sources.nvr import _parse_main_stream

    assert _parse_main_stream("2688x1520@20,1920x1080@15") == \
        frozenset({(2688, 1520, 20), (1920, 1080, 15)})
    assert _parse_main_stream("2688x1520") == frozenset({(2688, 1520, None)})  # fps omitted
    assert _parse_main_stream("") is None          # unset -> gate inactive
    assert _parse_main_stream(None) is None
    for bad in (",", "garbage", "2688x1520@notafps", "2688xWIDE@20"):
        with pytest.raises(ValueError):
            _parse_main_stream(bad)


def test_fetch_trims_a_foreign_head_in_an_existing_cache_file(tmp_path, monkeypatch):
    """The cache-hit TRIM branch of fetch(): a pre-gate cache clip carrying a
    cross-camera head is REPLACED in place by the head-trimmed clip when fetch()
    reuses it — the upgrade path this fix exists to close, with no re-pull."""
    monkeypatch.setenv("VA_NVR_TZ", "UTC")   # VA_NVR_MAIN_STREAM stays unset (fixture)
    src = NvrRecordedSource()
    resolved = src.resolve("nvr://1/2026-08-10T01:00:00/2026-08-10T01:00:06")  # 6 s
    cache = tmp_path / "cache"
    cache.mkdir()
    out = cache / (resolved.source_key.replace(":", "_") + ".mp4")
    write_frames_video(out, [(FOREIGN, 0.5), (BODY, 5.5)], fps=10)   # foreign head + body

    def dead_pull(self, *a):
        raise AssertionError("a trimmable cache hit must be repaired in place, not re-pulled")

    monkeypatch.setattr(NvrRecordedSource, "_pull_window", dead_pull)

    got = src.fetch(resolved, cache)

    assert got.local_path == str(out), "the trimmed clip lands back at the cache path"
    assert abs(probe(str(out)).duration_seconds - 5.5) <= 0.3, "~0.5 s foreign head gone"
    body_hash = dhash(Image.fromarray(BODY))
    assert all(hamming(dhash(img), body_hash) == 0
               for _, img in first_frames(str(out), 3)), "cached head trimmed away"


class _StubClockReader:
    """A burned-in-clock reader stand-in (no OCR). It reports a 7-day-stale head
    then aligned frames on the original cut, and — because a real reader re-reads
    the recorder's overlay on the TRIMMED clip, which is aligned — all-aligned on
    the trimmed file. That models the recheck's shifted start_epoch flow."""

    def read_head_clock(self, path, n_frames):
        if "verified" in str(path):                    # the trimmed clip
            return [ClockReading(float(i), (E + 1.0) + i) for i in range(3)]
        return [ClockReading(0.0, E - 7 * DAY),        # foreign head
                ClockReading(1.0, E + 1.0),            # aligned body
                ClockReading(2.0, E + 2.0)]


def test_injected_clock_reader_trims_a_stale_head_through_the_nvr_wiring(tmp_path):
    """Finding-4 coverage: the clock signal is same-camera, so head/self identity
    can't see it — only an injected TimestampReader can. Drive the whole
    NvrRecordedSource wiring (extraction call + the recheck's shifted epoch) with
    a stub reader; a uniform-body clip (head-identity clean) is trimmed by the
    clock gate alone."""
    clip = write_frames_video(tmp_path / "cut.mp4", [(BODY, 6.0)], fps=10)
    src = NvrRecordedSource(timestamp_reader=_StubClockReader())

    out = src._verify_and_trim(Path(clip), 1, T0, T0 + timedelta(seconds=6))

    assert Path(out).name.endswith(".verified.mp4")
    assert abs(probe(str(out)).duration_seconds - 5.0) <= 0.3, "1 s stale head gone"


def test_injected_clock_reader_leaves_aligned_footage_untouched(tmp_path):
    class _Aligned:
        def read_head_clock(self, path, n_frames):
            return [ClockReading(float(i), E + i) for i in range(3)]

    clip = write_frames_video(tmp_path / "cut.mp4", [(BODY, 6.0)], fps=10)
    src = NvrRecordedSource(timestamp_reader=_Aligned())
    out = src._verify_and_trim(Path(clip), 1, T0, T0 + timedelta(seconds=6))
    assert str(out) == str(clip), "aligned clock -> accepted untouched"
