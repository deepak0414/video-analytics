"""Dual time model translation (WS-3, plan §4).

The storage rule: every stored timestamp stays VIDEO-RELATIVE (seconds from that
chunk's t=0); a chunk may carry an absolute base (`videos.start_epoch`, UTC epoch
seconds of t=0). This module is the only place the two representations meet:

- `absolute_time(video, rel)` — relative → wall-clock (None when the video has
  no base: A-EV videos are relative-only by design).
- `wallclock_to_chunks(videos, t0, t1)` — a wall-clock range → the per-chunk
  relative ranges that cover it, skipping NULL-epoch videos. This is how an
  A-LSSRVF query like "yesterday 02:00–04:00" becomes concrete (chunk,
  relative-range) lookups.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from va.contracts.video import Video


def absolute_time(video: Video, relative: float) -> Optional[float]:
    """UTC epoch seconds for a video-relative timestamp; None if no base."""
    if video.start_epoch is None:
        return None
    return video.start_epoch + relative


@dataclass(frozen=True)
class ChunkRange:
    """A video-relative window inside one chunk, produced from a wall-clock range."""
    video_id: str
    rel_start: float
    # Always set today: for unknown-duration chunks it is capped at the RANGE end
    # (t1), not the chunk's (unknown) end. Optional so a future open-ended
    # variant can use None without a shape change.
    rel_end: Optional[float]


def wallclock_to_chunks(
    videos: Iterable[Video], t0: float, t1: float
) -> list[ChunkRange]:
    """Translate the wall-clock range [t0, t1] (UTC epoch seconds) into per-chunk
    relative ranges.

    Per chunk: overlap of [t0, t1] with [start_epoch, start_epoch + duration],
    clamped and re-based to chunk-relative seconds. Chunks with no `start_epoch`
    are skipped (relative-only, A-EV). A chunk with an unknown duration is
    included whenever it starts at or before t1, with `rel_end` CAPPED AT THE
    RANGE END (t1 rebased) — the chunk may actually end sooner; callers reading
    past its real data simply find nothing. Results are ordered by start_epoch.
    """
    if t1 < t0:
        raise ValueError(f"empty wall-clock range: t1 ({t1}) < t0 ({t0})")
    out: list[tuple[float, ChunkRange]] = []
    for v in videos:
        if v.start_epoch is None:
            continue
        rel_start = max(0.0, t0 - v.start_epoch)
        if v.duration_seconds is not None:
            if v.start_epoch + v.duration_seconds < t0 or v.start_epoch > t1:
                continue  # no overlap
            rel_end: Optional[float] = min(v.duration_seconds, t1 - v.start_epoch)
        else:
            if v.start_epoch > t1:
                continue  # starts after the range; unknown length can't help
            rel_end = t1 - v.start_epoch  # the range's cap, not the chunk's end
        out.append((v.start_epoch, ChunkRange(str(v.id), rel_start, rel_end)))
    out.sort(key=lambda pair: pair[0])
    return [cr for _, cr in out]
