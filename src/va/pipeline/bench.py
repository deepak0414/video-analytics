"""Benchmark harness — measures the ingest path + query latency in an ISOLATED
workdir, so numbers aren't polluted by existing state. (Baseline for the
performance work; see performance-and-productization-plan.md.)

Why a dedicated workdir (the caller passes a top-level dir distinct from `.va`/
`.va-shots`): (1) ingest is idempotent — a video already `done` in a shared
workdir would dedup-skip, measuring ~0 instead of the real decode/embed path;
(2) no contention with `.va`/`.va-shots` (shared `catalog.db` lock, cache). The
internal layout is a normal workdir; only the top-level path differs. The bench
CLEARS the workdir each run so the ingest actually executes.

Per the repo rule (determinism ≠ correctness), this captures the baseline every
later performance change is judged against — no optimization claims a win without
a before/after number from here.
"""
from __future__ import annotations

import shutil
import statistics
import time
from pathlib import Path
from typing import List, Optional

_RESULT_FIELDS = ("frames_indexed", "segments", "captioned_segments",
                  "transcript_lines", "speakers", "detections", "tracks",
                  "ocr_lines", "action_events", "text_vectors")

_DEFAULT_QUERIES = ["a person", "a car", "an outdoor scene", "text on a sign"]


def run_bench(
    video: str,
    workdir: str = ".va-bench",
    *,
    fps: float = 1.0,
    k: int = 10,
    iters: int = 10,
    queries: Optional[List[str]] = None,
) -> dict:
    """Clear `workdir`, ingest `video` (timed), then measure query p50/p95.
    Returns a baseline dict (also suitable to persist for before/after diffs)."""
    from va.pipeline.ingest import ingest
    from va.pipeline.paths import Workspace
    from va.pipeline.query import query as visual_query
    from va.storage.vector.sharded import ShardedVectorStore

    queries = queries or _DEFAULT_QUERIES

    # Isolate: a cleared workdir guarantees the ingest path actually runs.
    if Path(workdir).exists():
        shutil.rmtree(workdir)

    t0 = time.perf_counter()
    res = ingest(video, workdir=workdir, fps=fps)
    ingest_s = time.perf_counter() - t0

    vectors = ShardedVectorStore(Workspace(workdir).videos_root).count()

    # Warm up once (first call pays model-load / file-open), then time.
    visual_query(queries[0], workdir=workdir, k=k)
    lat_ms: List[float] = []
    for _ in range(max(1, iters)):
        for q in queries:
            t = time.perf_counter()
            visual_query(q, workdir=workdir, k=k)
            lat_ms.append((time.perf_counter() - t) * 1000.0)
    lat_ms.sort()
    p50 = statistics.median(lat_ms)
    p95 = lat_ms[min(len(lat_ms) - 1, int(round(len(lat_ms) * 0.95)) - 1)] if lat_ms else 0.0

    return {
        "video": video,
        "workdir": workdir,
        "fps": fps,
        "ingest_s": round(ingest_s, 3),
        "vectors": vectors,
        "query_k": k,
        "query_samples": len(lat_ms),
        "query_p50_ms": round(p50, 2),
        "query_p95_ms": round(p95, 2),
        "ingest_counts": {f: getattr(res, f, 0) for f in _RESULT_FIELDS},
    }


def find_local_media(search_root: str = ".") -> Optional[str]:
    """A local media file to benchmark against (real decode workload), e.g. one
    already ingested under a workdir's videos/. Returns None if none found."""
    cands = sorted(Path(search_root).glob("*/videos/*/media.*"))
    return str(cands[0]) if cands else None


_VIDEO_EXTS = (".mp4", ".webm", ".mkv", ".mov", ".avi")


def find_all_media(search_root: str = ".") -> List[str]:
    """The UNIQUE videos under any workdir's videos/ — one per video, video files
    only (skips `media.audio.wav` sidecars), deduped across workdirs by the
    `<key16>-<slug>` dir name, preferring the canonical `.va-shots` copy."""
    by_video: dict = {}
    for p in sorted(Path(search_root).glob("*/videos/*/media.*")):
        if p.suffix.lower() not in _VIDEO_EXTS:
            continue
        key = p.parent.name
        if key not in by_video or ".va-shots/" in str(p):
            by_video[key] = str(p)
    return list(by_video.values())


def bench_video(video: str, *, runs: int = 5, workdir: str = ".va-bench",
                fps: float = 1.0, k: int = 10, iters: int = 10,
                queries: Optional[List[str]] = None) -> dict:
    """Run the single-run benchmark `runs` times (clean workdir each) and average.
    A single run is noisy; the mean (with std / min-max) is the recorded number."""
    runs = max(1, runs)
    singles = [run_bench(video, workdir=workdir, fps=fps, k=k, iters=iters, queries=queries)
               for _ in range(runs)]
    ingest = [s["ingest_s"] for s in singles]
    p50 = [s["query_p50_ms"] for s in singles]
    last = singles[-1]
    return {
        "video": video,
        "runs": runs,
        "ingest_s_mean": round(statistics.mean(ingest), 3),
        "ingest_s_std": round(statistics.pstdev(ingest), 3) if runs > 1 else 0.0,
        "ingest_s_min": round(min(ingest), 3),
        "ingest_s_max": round(max(ingest), 3),
        "ingest_s_runs": [round(x, 3) for x in ingest],
        "query_p50_ms_mean": round(statistics.mean(p50), 2),
        "vectors": last["vectors"],
        "ingest_counts": last["ingest_counts"],
    }


def bench_all(videos: Optional[List[str]] = None, *, runs: int = 5,
              workdir: str = ".va-bench", fps: float = 1.0, k: int = 10,
              iters: int = 10, queries: Optional[List[str]] = None) -> dict:
    """Averaged benchmark for every video — the per-video baseline a later ingest
    optimization is checked against (every video should see the win, none regress)."""
    videos = videos if videos is not None else find_all_media()
    results = [bench_video(v, runs=runs, workdir=workdir, fps=fps, k=k, iters=iters,
                           queries=queries) for v in videos]
    return {"runs": runs, "fps": fps, "videos": results}


def render_bench_all(result: dict) -> str:
    out = [f"=== benchmark baseline ({result['runs']} runs/video, clean workdir each, "
           f"fps={result.get('fps', 1.0)}) ===",
           f"  {'video':<40} {'ingest_s mean±std':>18}  {'min–max':>13}  {'q_p50':>8}  {'vecs':>6}"]
    for v in result["videos"]:
        name = Path(v["video"]).parent.name[:40]
        out.append(f"  {name:<40} {v['ingest_s_mean']:>8.2f} ±{v['ingest_s_std']:<7.2f} "
                   f"{v['ingest_s_min']:>5.1f}–{v['ingest_s_max']:<6.1f} "
                   f"{v['query_p50_ms_mean']:>6.1f}ms {v['vectors']:>6}")
    return "\n".join(out)


def render_bench(b: dict) -> str:
    c = b["ingest_counts"]
    return "\n".join([
        "=== benchmark baseline ===",
        f"  video:    {b['video']}",
        f"  workdir:  {b['workdir']} (isolated)   fps: {b['fps']}",
        f"  ingest:   {b['ingest_s']:>8.3f} s   "
        f"({c['frames_indexed']} frames, {c['segments']} segments, {c['captioned_segments']} caps, "
        f"{c['transcript_lines']} tx, {c['detections']} det, {c['ocr_lines']} ocr, "
        f"{c['action_events']} act, {c['text_vectors']} text-vecs)",
        f"  corpus:   {b['vectors']} visual vectors",
        f"  query:    p50 {b['query_p50_ms']:.2f} ms   p95 {b['query_p95_ms']:.2f} ms   "
        f"(k={b['query_k']}, {b['query_samples']} samples)",
    ])
