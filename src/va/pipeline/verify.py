"""Retrieval Layer (SR.6) — apply the VLM verifier to a cheap retriever's output.

Two entry points, both no-ops when the configured verifier is the passthrough stub
(so the offline pipeline is untouched):

  verify_visual_hits  — RE-RANK/FILTER SigLIP hits: drop the ones a VLM confidently
                        rejects. Only ABOVE-floor hits are checked (a below-floor hit
                        can't cause a match, so verifying it would waste a VLM call and
                        risk nothing), capped by a budget. This is what overturns a
                        SigLIP attribute/composition false-positive ("blue Ferrari").

  verify_object_presence — when a fixed-vocab detector (YOLO) returns nothing for a
                        class, sample scene-representative frames and ask the VLM if the
                        object is present; return the YES count. This is what recovers a
                        novel/camouflaged object YOLO can't see ("snake").

Both are conservative by construction (the verifier only rejects on a confident
negative), so they can overturn the cheap retriever but not silently delete true hits.
"""
from __future__ import annotations

from typing import List, Optional, Sequence
from uuid import UUID

from va.media.frames import frames_at
from va.pipeline.paths import Workspace
from va.registry import get_vlm_verifier
from va.storage.structured.catalog_sqlite import Catalog


def verify_visual_hits(
    hits: Sequence,
    claim: str,
    workdir: str = ".va",
    *,
    floor: float = 0.10,
    max_verify: int = 16,
    stop_after_accepts: Optional[int] = None,
    verifier=None,
) -> List:
    """Filter SigLIP `hits` (score-descending) by VLM verification of `claim`.

    Below-`floor` hits and any beyond the `max_verify` budget pass through
    unchecked; above-floor hits within budget are dropped iff the VLM rejects.

    `stop_after_accepts` bounds cost: once that many hits have been accepted, the
    rest pass through unverified. With `1`, a true match confirms on its first
    (strongest) frame — one VLM call — while a true negative still verifies every
    above-floor candidate (you must check them all to be sure none is real).
    """
    verifier = verifier or get_vlm_verifier()
    if not getattr(verifier, "enabled", False):
        return list(hits)

    catalog = Catalog(Workspace(workdir).catalog_db)
    paths: dict[UUID, Optional[str]] = {}

    def path_for(vid: UUID) -> Optional[str]:
        if vid not in paths:
            v = catalog.get(vid)
            paths[vid] = v.local_path if v else None
        return paths[vid]

    out, checked, accepts = [], 0, 0
    try:
        for h in hits:
            done = stop_after_accepts is not None and accepts >= stop_after_accepts
            if h.score < floor or checked >= max_verify or done:
                out.append(h)
                continue
            p = path_for(h.video_id)
            if not p:
                out.append(h)            # can't fetch the frame -> don't guess-drop
                continue
            try:
                img = frames_at(p, [h.timestamp])[0]
            except Exception:
                out.append(h)
                continue
            checked += 1
            if verifier.verify(img, claim).accept:
                accepts += 1
                out.append(h)
            # else: VLM confidently rejected -> drop this hit
        return out
    finally:
        catalog.close()


def verify_scene_presence(
    claim: str,
    video_id: UUID,
    workdir: str = ".va",
    *,
    window: Optional[Sequence[float]] = None,
    samples: int = 10,
    verifier=None,
) -> int:
    """Sample up to `samples` scene-representative frames (within `window`=[lo,hi]
    if given, else across the whole video) and return how many the VLM confirms
    match `claim`. 0 under the no-op stub.

    This is RECALL-RECOVERY: it can surface a true match an embedding under-scored
    (the dresses wedding scored 0.022 on SigLIP, but the VLM confirms it), the
    scene-level twin of object presence. Strict by construction — only a confident
    YES counts, so a hedge cannot fabricate a match.
    """
    verifier = verifier or get_vlm_verifier()
    if not getattr(verifier, "enabled", False):
        return 0

    from va.storage.structured.segments import SegmentStore

    ws = Workspace(workdir)
    catalog = Catalog(ws.catalog_db)
    seg_store = SegmentStore(ws.catalog_db)
    try:
        video = catalog.get(video_id)
        if video is None or not video.local_path:
            return 0
        segments = seg_store.get_segments(video_id)
        if segments:
            times = [(s.start_time + s.end_time) / 2.0 for s in segments]
        else:  # no scenes -> even spacing across the known duration (fallback 60s)
            dur = video.duration_seconds or 60.0
            times = [dur * (i + 0.5) / samples for i in range(samples)]
        if window is not None:
            lo, hi = window[0], window[1]
            inside = [t for t in times if lo <= t <= hi]
            # fall back to evenly spaced points in the window if no scene midpoint lands in it
            times = inside or [lo + (hi - lo) * (i + 0.5) / samples for i in range(samples)]
        if len(times) > samples:                      # subsample evenly to the budget
            step = len(times) / samples
            times = [times[int(i * step)] for i in range(samples)]
        try:
            imgs = frames_at(video.local_path, times)
        except Exception:
            return 0
        return sum(1 for im in imgs if verifier.verify(im, claim).present)
    finally:
        seg_store.close()
        catalog.close()


def verify_object_presence(
    object_class: str,
    video_id: UUID,
    workdir: str = ".va",
    *,
    samples: int = 10,
    verifier=None,
) -> int:
    """Sample up to `samples` frames across the video and return how many the VLM
    says contain `object_class` — `verify_scene_presence` specialized to a class."""
    return verify_scene_presence(
        f"a {object_class}", video_id, workdir, samples=samples, verifier=verifier
    )
