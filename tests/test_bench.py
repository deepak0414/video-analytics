"""Bench harness smoke test (offline, stub backends, synth clip)."""
from va.media.synth import write_color_video
from va.pipeline.bench import (
    bench_all,
    bench_video,
    render_bench,
    render_bench_all,
    run_bench,
)


def test_bench_reports_ingest_and_query_metrics(tmp_path):
    video = write_color_video(tmp_path / "clip.mp4", [("red", (220, 30, 30), 3.0)], fps=10)
    bench = str(tmp_path / ".va-bench")
    b = run_bench(str(video), workdir=bench, fps=1.0, k=3, iters=2, queries=["red", "blue"])

    assert b["ingest_s"] > 0
    assert b["vectors"] > 0                       # frames were embedded
    assert b["query_samples"] == 2 * 2            # 2 queries x 2 iters
    assert b["query_p50_ms"] >= 0 and b["query_p95_ms"] >= 0
    assert b["ingest_counts"]["frames_indexed"] > 0
    assert "benchmark baseline" in render_bench(b)


def test_bench_clears_workdir_so_ingest_actually_reruns(tmp_path):
    # the whole point of the isolated/cleared workdir: ingest runs every time
    # (a shared, already-`done` workdir would dedup-skip and measure ~0).
    video = write_color_video(tmp_path / "clip.mp4", [("green", (30, 180, 30), 2.0)], fps=10)
    bench = str(tmp_path / ".va-bench")
    b1 = run_bench(str(video), workdir=bench, fps=1.0, k=2, iters=1, queries=["green"])
    b2 = run_bench(str(video), workdir=bench, fps=1.0, k=2, iters=1, queries=["green"])
    assert b1["ingest_counts"]["frames_indexed"] > 0
    assert b2["ingest_counts"]["frames_indexed"] > 0   # re-ingested, NOT dedup-skipped
    assert b2["vectors"] == b1["vectors"]


def test_bench_video_averages_runs(tmp_path):
    video = write_color_video(tmp_path / "clip.mp4", [("red", (220, 30, 30), 2.0)], fps=10)
    v = bench_video(str(video), runs=3, workdir=str(tmp_path / ".va-bench"),
                    fps=1.0, k=2, iters=1, queries=["red"])
    assert v["runs"] == 3 and len(v["ingest_s_runs"]) == 3
    assert v["ingest_s_min"] <= v["ingest_s_mean"] <= v["ingest_s_max"]
    assert v["vectors"] > 0
    assert "benchmark baseline" in render_bench_all({"runs": 3, "videos": [v]})


def test_bench_all_covers_every_video(tmp_path):
    a = write_color_video(tmp_path / "a.mp4", [("red", (220, 30, 30), 1.5)], fps=10)
    b = write_color_video(tmp_path / "b.mp4", [("blue", (30, 30, 220), 1.5)], fps=10)
    res = bench_all([str(a), str(b)], runs=2, workdir=str(tmp_path / ".va-bench"),
                    fps=1.0, k=2, iters=1, queries=["red"])
    assert res["runs"] == 2 and len(res["videos"]) == 2
    assert all(x["ingest_s_mean"] > 0 for x in res["videos"])
