"""`va` command-line entrypoint.

Subcommands (wired to the pipeline in later tasks):
  va ingest <uri>         ingest a YouTube URL or local file (idempotent)
  va query  "<text>"      search ingested videos, print ranked moments
  va fixtures pull        download pinned test fixtures

Kept thin on purpose: it parses args and delegates to va.pipeline.*.
"""
from __future__ import annotations

import argparse
import os
import sys


def _cmd_ingest(args: argparse.Namespace) -> int:
    from va.pipeline.ingest import ingest

    result = ingest(args.uri, workdir=args.workdir, fps=args.fps, profile=args.profile)
    status = "already-ingested" if result.deduped else "ingested"
    if result.deduped and args.profile and result.video.profile != args.profile:
        print(f"note: --profile {args.profile} NOT applied — video already ingested "
              f"under profile '{result.video.profile or '(pre-profile)'}'; "
              f"use `va reingest {args.uri} --profile {args.profile}` to change it")
    print(f"[{status}] {result.video.source_type.value}:{result.video.source_key} "
          f"id={result.video.id} frames={result.frames_indexed} segments={result.segments} "
          f"captioned={result.captioned_segments} transcript_lines={result.transcript_lines} "
          f"speakers={result.speakers} "
          f"detections={result.detections} tracks={result.tracks} ocr_lines={result.ocr_lines} "
          f"actions={result.action_events} text_vectors={result.text_vectors}")
    return 0


def _cmd_ask(args: argparse.Namespace) -> int:
    from va.pipeline.ask import ask

    result = ask(args.question, workdir=args.workdir, k=args.k)
    print(result.rendered)
    if args.show_evidence:
        print("\n--- evidence ---")
        for item in result.evidence.items:
            print(f"  [{item.modality}] @{item.time_start:.1f}s  {item.content[:100]}")
        for note in result.evidence.notes:
            print(f"  [note] {note}")
    return 0


def _cmd_count(args: argparse.Namespace) -> int:
    from va.pipeline.objects import count_objects

    counts = count_objects(args.text, workdir=args.workdir, min_frames=args.min_frames)
    if not counts:
        print("no tracked objects for that class (or nothing ingested with tracking)")
        return 0
    for c in counts:
        first = f"{int(c.first_seen // 60)}:{int(c.first_seen % 60):02d}"
        last = f"{int(c.last_seen // 60)}:{int(c.last_seen % 60):02d}"
        print(f"{c.object_class}: {c.distinct} distinct  ({first} → {last})  video={c.video_id}")
    return 0


def _cmd_aggregate(args: argparse.Namespace) -> int:
    """`va aggregate count/events/histogram` — the typed-query tier's CLI.

    Prints the number TOGETHER with how it was derived (resolution provenance)
    and its caveats — a count never ships without its method."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from pydantic import ValidationError

    from va.contracts.aggregate import TimeWindow
    from va.pipeline import aggregate as agg

    def parse_dt(s: str) -> datetime:
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            raise SystemExit(f"unparseable time {s!r} (ISO 8601 wall-clock, "
                             f"e.g. 2026-08-11T00:00 or '2026-08-11 00:00:30')")

    try:
        window = TimeWindow(start=parse_dt(args.start), end=parse_dt(args.end),
                            tz=args.tz)
    except ValidationError as e:
        raise SystemExit(f"invalid window: {e}")
    zone = ZoneInfo(window.tz)

    def local(epoch: float) -> str:
        return datetime.fromtimestamp(epoch, zone).isoformat()

    # The canonical count runs for EVERY subcommand: its total is the
    # untruncated number, and its caveats/provenance are the method — a number
    # never prints without them (same every-op honesty as the dispatch path).
    r = agg.count_objects(args.category, window, workdir=args.workdir,
                          cameras=args.camera, dedup=args.dedup,
                          min_frames=args.min_frames)

    def print_method() -> None:
        res = r.resolution
        print(f"resolution: classes matched {res.categories_matched} "
              f"(via {res.category_source}); dedup {res.dedup_mode} "
              f"({res.dedup_source})")
        for c in r.caveats:
            print(f"caveat: {c}")

    if args.agcmd == "count":
        print(f"'{args.category}' from {local(window.epoch_bounds()[0])} "
              f"to {local(window.epoch_bounds()[1])} [{window.tz}]:")
        for cam in sorted(r.per_camera, key=lambda c: (-r.per_camera[c], c)):
            print(f"  {cam:<14} {r.per_camera[cam]}")
        print(f"  {'total':<14} {r.total}")
        print_method()
    elif args.agcmd == "events":
        rows = agg.list_events(args.category, window, workdir=args.workdir,
                               cameras=args.camera, limit=args.limit,
                               dedup=args.dedup, min_frames=args.min_frames)
        for row in rows:
            cam = row.camera or "(no camera)"
            print(f"{local(row.first_seen_epoch)}  {cam:<10} "
                  f"'{row.category}'  {row.frames} frames  "
                  f"track={row.track_id}")
        shown = (f"all {len(rows)}" if len(rows) == r.total
                 else f"first {len(rows)} of {r.total} (raise --limit for more)")
        print(f"{r.total} event(s) — showing {shown}")
        print_method()
    else:  # histogram
        try:
            buckets = agg.timeline_histogram(
                args.category, window, workdir=args.workdir, bucket=args.bucket,
                cameras=args.camera, dedup=args.dedup, min_frames=args.min_frames)
        except ValueError as e:  # bad bucket grammar / bucket-explosion guard
            raise SystemExit(str(e))
        peak = max((b.count for b in buckets), default=0)
        for b in buckets:
            bar = "#" * (0 if peak == 0 else round(20 * b.count / peak))
            print(f"{local(b.bucket_start_epoch)}  {b.count:>5}  {bar}")
        print(f"{sum(b.count for b in buckets)} total across "
              f"{len(buckets)} bucket(s) of {args.bucket}")
        print_method()
    return 0


def _cmd_objects(args: argparse.Namespace) -> int:
    from va.pipeline.objects import query_objects

    summaries = query_objects(args.text, workdir=args.workdir)
    if not summaries:
        print("no matching objects (class not detected, or nothing ingested)")
        return 0
    for s in summaries:
        first = f"{int(s.first_seen // 60)}:{int(s.first_seen % 60):02d}"
        last = f"{int(s.last_seen // 60)}:{int(s.last_seen % 60):02d}"
        print(f"{s.object_class}: {s.frames} frames  ({first} → {last}, "
              f"max conf {s.max_confidence:.2f})  video={s.video_id}")
    return 0


def _cmd_caption(args: argparse.Namespace) -> int:
    from va.pipeline.caption import search_captions

    hits = search_captions(args.text, workdir=args.workdir, k=args.k)
    if not hits:
        print("no caption matches (was anything captioned?)")
        return 0
    for h in hits:
        ts = f"{int(h.start_time // 60):d}:{int(h.start_time % 60):02d}"
        print(f"{h.score:.2f}  {ts:>6}  {h.caption}")
    return 0


def _cmd_transcript(args: argparse.Namespace) -> int:
    from va.pipeline.transcript import search_transcripts

    hits = search_transcripts(args.text, workdir=args.workdir, k=args.k,
                              speaker=args.speaker)
    if not hits:
        print("no transcript matches (was anything with audio ingested?)")
        return 0
    for h in hits:
        ts = f"{int(h.start_time // 60):d}:{int(h.start_time % 60):02d}"
        who = f"[{h.speaker}] " if h.speaker else ""
        print(f"{h.score:.2f}  {ts:>6}  {who}{h.text}")
    return 0


def _cmd_actions(args: argparse.Namespace) -> int:
    from va.pipeline.actions import search_actions

    hits = search_actions(args.text, workdir=args.workdir, k=args.k)
    if not hits:
        print("no action matches (was anything ingested with action recognition?)")
        return 0
    for h in hits:
        ts = f"{int(h.start_time // 60):d}:{int(h.start_time % 60):02d}"
        print(f"{h.score:.2f}  {ts:>6}  {h.action_class} "
              f"(conf {h.confidence:.2f}, {h.start_time:.1f}-{h.end_time:.1f}s)")
    return 0


def _cmd_ocr(args: argparse.Namespace) -> int:
    from va.pipeline.ocr import search_ocr

    hits = search_ocr(args.text, workdir=args.workdir, k=args.k)
    if not hits:
        print("no on-screen text matches (was anything ingested with OCR?)")
        return 0
    for h in hits:
        ts = f"{int(h.time_start // 60):d}:{int(h.time_start % 60):02d}"
        span = "" if h.sightings == 1 else f" (x{h.sightings}, last @{h.time_end:.0f}s)"
        print(f"{h.score:.2f}  {ts:>6}  {h.text}{span}")
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    from va.pipeline.query import query
    from va.pipeline.trace_links import trace_ingest_links
    from va.runtime.trace import trace, traced_run

    with traced_run("query", args.workdir):
        hits = query(args.text, workdir=args.workdir, k=args.k)
        trace("retriever", "visual_search", f"{len(hits)} hits",
              top=[{"score": round(h.score, 3), "t": round(h.timestamp, 1)} for h in hits[:3]])
        trace_ingest_links(args.workdir, {h.video_id for h in hits})
        if getattr(args, "verify", False):
            # SR.6: VLM-verify the candidates (drops attribute/composition false hits).
            # No-op unless a real verifier is configured (VA_CONFIG_DIR=run-*/config).
            from va.pipeline.verify import verify_visual_hits

            n0 = len(hits)
            hits = verify_visual_hits(hits, args.text, workdir=args.workdir,
                                      floor=0.10, stop_after_accepts=1)
            trace("retriever", "verify", f"{len(hits)}/{n0} survived VLM verification")
    if not hits:
        print("no results (is anything ingested?)")
        return 0
    for h in hits:
        ts = f"{int(h.timestamp // 60):d}:{int(h.timestamp % 60):02d}"
        print(f"{h.score:.3f}  {ts:>6}  {h.source_uri}")
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    from va.pipeline.manage import remove_video

    video = remove_video(args.workdir, args.video)
    if video is None:
        print(f"no video matching {args.video!r} (try UUID, source_key, URL, or path)")
        return 1
    print(f"removed {video.source_type.value}:{video.source_key} ({video.title or 'untitled'})")
    return 0


def _cmd_reingest(args: argparse.Namespace) -> int:
    from va.pipeline.manage import reingest_video

    result = reingest_video(args.workdir, args.video, fps=args.fps, profile=args.profile)
    if result is None:
        print(f"no video matching {args.video!r}")
        return 1
    print(f"[reingested] {result.video.source_type.value}:{result.video.source_key} "
          f"frames={result.frames_indexed} segments={result.segments} "
          f"captioned={result.captioned_segments} transcript_lines={result.transcript_lines} "
          f"speakers={result.speakers} "
          f"detections={result.detections} tracks={result.tracks} ocr_lines={result.ocr_lines} "
          f"actions={result.action_events} text_vectors={result.text_vectors}")
    return 0


def _cmd_textsearch(args: argparse.Namespace) -> int:
    from va.pipeline.text_search import search_text

    mods = args.modality.split(",") if args.modality else None
    hits = search_text(args.text, workdir=args.workdir, k=args.k, modalities=mods)
    if not hits:
        print("no semantic text matches (is text indexed? is a real embedder configured?)")
        return 0
    for h in hits:
        ts = f"{int(h.time_start // 60):d}:{int(h.time_start % 60):02d}"
        print(f"{h.score:.2f}  [{h.modality}] {ts:>6}  {h.text[:70]}")
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    from va.pipeline.migrate import migrate_workdir

    stats = migrate_workdir(args.workdir)
    print(f"migrated {args.workdir}: {stats}")
    return 0


def _active_config_line() -> str:
    """One-line summary of the config that staleness is measured against — surfaced by
    `va stale`/`va reprocess`. Both are the tools reached for during model switches, exactly
    when a forgotten VA_CONFIG_DIR would compare real-model rows against the stub config,
    flag EVERYTHING stale, and lead a `va reingest`/reprocess to overwrite real data with
    stub output (CLAUDE.md gotcha #2). Showing the basis makes that mismatch self-evident."""
    import os

    from va.configuration import load_config

    cfg = load_config()
    cfg_dir = os.environ.get("VA_CONFIG_DIR") or "config (default — stub backends)"
    embedder = (cfg.roles.get("visual_embedder") or {}).get("model", "?")
    return (f"comparing against: {cfg_dir} "
            f"(profile={cfg.active_profile}, visual_embedder={embedder})")


def _cmd_watch(args: argparse.Namespace) -> int:
    """The A-LSSRVF orchestrator (WS6.b): catch up each registered camera from
    its durable watermark — query the MotionSource, pull each new motion
    episode as an nvr:// window, ingest it, advance the watermark. `--interval
    0` = one pass (cron-friendly); otherwise loops forever."""
    from va.pipeline.watch import catch_up, run_watch

    kwargs = dict(
        camera_ids=args.camera or None,
        lookback_s=args.lookback_hours * 3600.0,
        settle_s=args.settle,
        max_windows=args.max_windows,
        gap_s=args.cluster_gap,
        open_instant_max_age_s=args.open_instant_age,
    )
    if args.interval <= 0:
        report = catch_up(args.workdir, **kwargs)
        for c in report.cameras:
            print(f"{c.camera_id}: +{c.windows_ingested} window(s)"
                  f"{f', {c.windows_failed} failed' if c.windows_failed else ''}"
                  f"{' (truncated — more next pass)' if c.truncated else ''} "
                  f"watermark -> {c.watermark_after}")
        print(f"{report.windows_ingested} window(s) ingested")
        return 0
    run_watch(args.workdir, interval_s=args.interval, **kwargs)
    return 0


def _cmd_motion_probe(args: argparse.Namespace) -> int:
    """Diagnostic: query the configured MotionSource for a local-time range and
    print the (optionally clustered) windows. The manual-validation entry point
    for vendor adapters (WS4.a) — run with VA_NVR_HOST/USER/PASS set and
    motion_source.model=lnr-eventlog to exercise the real device."""
    from datetime import datetime

    from va.registry import get_motion_source
    from va.roles.motion_source import cluster_events

    def to_epoch(s: str) -> float:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).astimezone().timestamp()
            except ValueError:
                continue
        raise SystemExit(f"unparseable time {s!r} (expected 'YYYY-MM-DD [HH:MM[:SS]]')")

    src = get_motion_source()
    events = src.events(to_epoch(args.start), to_epoch(args.end),
                        camera_ref=args.camera)
    if args.cluster_gap:
        events = cluster_events(events, gap_s=args.cluster_gap)
    for e in events:
        dur = e.end_epoch - e.start_epoch
        print(f"cam {e.camera_ref or '?'}: "
              f"{datetime.fromtimestamp(e.start_epoch).astimezone().isoformat()} "
              f"+{dur:.0f}s  [{e.kind}]")
    print(f"{len(events)} window(s)")
    return 0


def _cmd_stale(args: argparse.Namespace) -> int:
    from va.pipeline.stale import stale_report

    print(_active_config_line())

    report = stale_report(args.workdir, role=args.role)
    scope = f" for role {args.role}" if args.role else ""
    if not report:
        print(f"all videos current{scope}")
        return 0
    for e in report:
        label = e["title"] or e["source_uri"] or e["video_id"]
        fps = e.get("recorded_fps")
        fps_note = f" [ingested at fps={fps}]" if fps is not None else ""
        print(f"[stale] {label}{fps_note}: {', '.join(e['stale_roles'])}")
    unknown_fps = any(e.get("recorded_fps") is None for e in report)
    fps_help = (
        "pass `--fps` to match each video's original density — shown above as "
        "`[ingested at fps=N]` where known"
        + ("; videos with no fps shown predate provenance, so their original density is "
           "unknown and reingest's fps=1.0 default may differ from it" if unknown_fps else "")
    )
    print(f"\n{len(report)} video(s) need reprocessing{scope} — re-run `va reingest <video>` "
          f"UNDER THIS SAME CONFIG. ({fps_help}. And if the config line above isn't the one "
          f"you intend, everything looks stale under the wrong models — fix that first.)")
    return 0


def _cmd_reprocess(args: argparse.Namespace) -> int:
    from va.pipeline.reprocess import plan_reprocess

    print(_active_config_line())
    try:
        plan = plan_reprocess(args.workdir, role=args.role,
                              all_stale=args.all_stale, video=args.video)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    scope = f" for role {args.role}" if args.role else ""
    if not plan:
        which = f"video {args.video}" if args.video else "selection"
        print(f"nothing to reprocess{scope} — {which} is already current")
        return 0

    print(f"reprocess plan{scope} — {len(plan)} video(s):")
    for e in plan:
        label = e["title"] or e["source_uri"] or e["video_id"]
        fps = e.get("recorded_fps")
        fps_note = f"fps={fps}" if fps is not None else "fps=unknown"
        print(f"  {label} [{fps_note}]: {', '.join(e['stale_roles'])}")

    if args.dry_run:
        print("\n(dry run — no changes made)")
        return 0

    if not args.yes:
        # Executing OVERWRITES real data in place. Unlike read-only `va stale`, a wrong
        # VA_CONFIG_DIR here isn't just a misleading report — it re-embeds the whole corpus
        # with the other config's model (e.g. stub 64-dim hashes over real SigLIP vectors),
        # hours of GPU to recover. Require an explicit --yes so nothing mutates on one
        # keystroke; the plan above is the review step.
        sys.stdout.flush()
        print("\nNOT executed — this OVERWRITES the shown shards/rows in place under the config "
              "above. Re-run with --yes to execute, or --dry-run to only plan. Make sure that "
              "config is the one you intend: reprocessing under the wrong VA_CONFIG_DIR would "
              "overwrite real-model data with the other config's output.", file=sys.stderr)
        return 1

    from va.pipeline.reprocess import execute_reprocess

    # fps to preserve on a reingest fallback (reingest defaults to 1.0 — see va stale remedy)
    fps_by_vid = {e["video_id"]: e.get("recorded_fps") for e in plan}
    result = execute_reprocess(args.workdir, plan)
    print("\nexecuting (rows re-run, then provenance restamped):")
    for vid, r, n in result["reprocessed"]:
        detail = f"reprocessed ({n} rows)" if n is not None else "restamped (rebuilt via a dependency)"
        print(f"  {vid} · {r}: {detail}")
    for vid, r, reason in result["skipped"]:
        fps = fps_by_vid.get(vid)
        fps_arg = f" --fps {fps}" if fps is not None else ""
        print(f"  {vid} · {r}: skipped — {reason}; run `va reingest {vid}{fps_arg}`")
    for vid, r, err in result["failed"]:
        print(f"  {vid} · {r}: FAILED — {err}")
    nd, ns, nf = len(result["reprocessed"]), len(result["skipped"]), len(result["failed"])
    print(f"\ndone: {nd} reprocessed, {ns} skipped, {nf} failed")
    return 1 if nf else 0


def _cmd_fixtures(args: argparse.Namespace) -> int:
    from va.sources.fixtures import pull_fixtures

    pull_fixtures(args.workdir)
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn

        from va.web.app import create_app
    except ModuleNotFoundError as e:
        print(f"`va serve` needs the web extra (missing: {e.name}). "
              f"Install with: pip install -e '.[web]'", file=sys.stderr)
        return 1
    if getattr(args, "trace", False):       # convenience: serve --trace -> VA_TRACE=1
        os.environ["VA_TRACE"] = "1"
    uvicorn.run(create_app(args.workdir), host=args.host, port=args.port)
    return 0


def _cmd_trace(args: argparse.Namespace) -> int:
    from pathlib import Path

    from va.runtime.trace import find_run, list_runs, prune_traces, render_trace

    wd, target = args.workdir, args.target
    if target == "list":
        runs = list_runs(wd)
        if not runs:
            print("no traces found (run with VA_TRACE=1, or `serve --trace`)")
            return 0
        for r in runs:
            warn = f"  ⚠ {r['warnings']}" if r["warnings"] else ""
            print(f"{r['run_id']:24} {r['kind']:6} {r['ts']:25} {r['events']:>3} ev{warn}")
        return 0
    if target == "prune":
        n = prune_traces(wd, keep=args.keep, older_than_days=args.older_than,
                         clear_all=args.all)
        print(f"pruned {n} trace file(s)")
        return 0
    # otherwise: render a specific run (by id) or the most recent one
    path = find_run(wd, target) if (target and not args.last) else (
        Path(list_runs(wd)[0]["path"]) if list_runs(wd) else None)
    if path is None:
        print("no matching trace (try `va trace list`)")
        return 1
    print(render_trace(path))
    return 0


def _cmd_bench(args: argparse.Namespace) -> int:
    import json
    from pathlib import Path

    from va.pipeline.bench import bench_all, bench_video, find_all_media, render_bench_all

    qs = args.queries.split("|") if args.queries else None
    if args.video:                                  # one video, averaged over --runs
        result = {"runs": args.runs, "fps": args.fps, "videos": [
            bench_video(args.video, runs=args.runs, workdir=args.bench_workdir,
                        fps=args.fps, k=args.k, iters=args.iters, queries=qs)]}
    else:                                           # all videos under workdirs
        vids = find_all_media()
        if not vids:
            print("no media.* found under any workdir; pass --video <path>", file=sys.stderr)
            return 1
        result = bench_all(vids, runs=args.runs, workdir=args.bench_workdir,
                           fps=args.fps, k=args.k, iters=args.iters, queries=qs)
    print(render_bench_all(result))
    if args.save:
        Path(args.save).write_text(json.dumps(result, indent=2))
        print(f"  saved -> {args.save}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="va", description="Ctrl-F for Video")
    p.add_argument("--workdir", default=".va", help="state dir (db, vectors, cache)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("ingest", help="ingest a video URL or local path")
    pi.add_argument("uri")
    pi.add_argument("--fps", type=float, default=1.0, help="frame sampling rate")
    pi.add_argument(
        "--profile", default=None,
        help="footage profile to ingest under (config/profiles/footage/<name>.yaml; "
             "default derived from the source type)",
    )
    pi.set_defaults(func=_cmd_ingest)

    pq = sub.add_parser("query", help="visual search over ingested videos")
    pq.add_argument("text")
    pq.add_argument("-k", type=int, default=10, help="number of results")
    pq.add_argument("--verify", action="store_true",
                    help="SR.6: VLM-verify hits (needs a real verifier config)")
    pq.set_defaults(func=_cmd_query)

    pt = sub.add_parser("transcript", help="search what was said (Role 8 transcripts)")
    pt.add_argument("text")
    pt.add_argument("-k", type=int, default=10, help="number of results")
    pt.add_argument("--speaker", default=None,
                    help="filter to one speaker label, e.g. SPEAKER_01 (Role 9)")
    pt.set_defaults(func=_cmd_transcript)

    pts = sub.add_parser("textsearch",
                         help="semantic search over caption/transcript/OCR/action text (Retrieval Layer)")
    pts.add_argument("text")
    pts.add_argument("-k", type=int, default=10, help="number of results")
    pts.add_argument("--modality", default=None,
                     help="comma-separated filter: caption,transcript,on_screen_text,action")
    pts.set_defaults(func=_cmd_textsearch)

    pc = sub.add_parser("caption", help="search scene captions (Role 4)")
    pc.add_argument("text")
    pc.add_argument("-k", type=int, default=10, help="number of results")
    pc.set_defaults(func=_cmd_caption)

    pac = sub.add_parser("actions", help="search recognized actions (Role 7)")
    pac.add_argument("text", help="action words, e.g. 'eating' or 'driving'")
    pac.add_argument("-k", type=int, default=10, help="number of results")
    pac.set_defaults(func=_cmd_actions)

    px = sub.add_parser("ocr", help="search on-screen text (Role 10)")
    px.add_argument("text")
    px.add_argument("-k", type=int, default=10, help="number of results")
    px.set_defaults(func=_cmd_ocr)

    po = sub.add_parser("objects", help="object appearances (Role 5 detections)")
    po.add_argument("text", help="class name(s), e.g. 'car' or 'person dog'")
    po.set_defaults(func=_cmd_objects)

    pa = sub.add_parser("ask", help="complex question -> reasoned, cited answer (Role 11)")
    pa.add_argument("question")
    pa.add_argument("-k", type=int, default=5, help="evidence per tier")
    pa.add_argument("--show-evidence", action="store_true")
    pa.set_defaults(func=_cmd_ask)

    from va.provenance import PROVENANCE_ROLES

    psl = sub.add_parser("stale",
                         help="videos whose recorded model/config != the current one (§6-b)")
    # choices guards against an unstamped role (e.g. reasoner) or a typo — either would
    # otherwise fingerprint fine but match no recorded row, reporting EVERY video stale.
    psl.add_argument("--role", default=None, choices=list(PROVENANCE_ROLES),
                     metavar="ROLE",
                     help="only check one role, e.g. speech_to_text "
                          f"(one of: {', '.join(PROVENANCE_ROLES)})")
    psl.set_defaults(func=_cmd_stale)

    pmp = sub.add_parser(
        "motion-probe",
        help="query the configured MotionSource for a local-time range (WS-4 diagnostic)")
    pmp.add_argument("start", help="local time 'YYYY-MM-DD [HH:MM[:SS]]'")
    pmp.add_argument("end", help="local time 'YYYY-MM-DD [HH:MM[:SS]]'")
    pmp.add_argument("--camera", default=None,
                     help="source-native camera ref (NVR display number)")
    pmp.add_argument("--cluster-gap", type=float, default=30.0,
                     help="merge same-camera windows with gaps <= this many seconds; 0 = raw")
    pmp.set_defaults(func=_cmd_motion_probe)

    pw = sub.add_parser(
        "watch",
        help="catch up cameras from their watermarks: motion episodes -> nvr:// ingests (WS6.b)")
    pw.add_argument("--camera", action="append", default=None,
                    help="camera id (e.g. nvr-ch1); repeatable; default = all registered")
    pw.add_argument("--lookback-hours", type=float, default=1.0,
                    help="how far a NEVER-watched camera reaches back (default 1h; "
                         "the NVR ring keeps ~6 days)")
    pw.add_argument("--settle", type=float, default=120.0,
                    help="stay this many seconds behind now (open episodes settle)")
    pw.add_argument("--max-windows", type=int, default=50,
                    help="window budget per pass, split per camera (each camera "
                         "gets at least 1 — with more cameras than this, a pass "
                         "may pull up to one window per camera)")
    pw.add_argument("--interval", type=float, default=0.0,
                    help="seconds between passes; 0 = one pass and exit (cron-friendly)")
    pw.add_argument("--cluster-gap", type=float, default=30.0,
                    help="merge motion events with gaps <= this into one pull episode "
                         "(independent of the scene_detector gap_s)")
    pw.add_argument("--open-instant-age", type=float, default=600.0,
                    help="an open (lost-End) motion instant older than this is recovered "
                         "as one padded window instead of deferring the watermark")
    pw.set_defaults(func=_cmd_watch)

    prp = sub.add_parser(
        "reprocess",
        help="re-run stale roles in place (§6-b pillar B; text/visual embedders + captioner + object_detector (rebuilds object_tracker) wired, others → reingest)")
    prp.add_argument("--role", default=None, choices=list(PROVENANCE_ROLES), metavar="ROLE",
                     help=f"restrict to one role (one of: {', '.join(PROVENANCE_ROLES)})")
    # Exactly one video scope — an explicit choice, so a reprocess can never fan out across
    # the whole corpus by omission.
    scope = prp.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all-stale", action="store_true", help="every stale video")
    scope.add_argument("--video", default=None, metavar="IDENT",
                       help="one video by UUID, source_key, URL, or path")
    prp.add_argument("--dry-run", action="store_true",
                     help="plan only — make no changes")
    prp.add_argument("--yes", action="store_true",
                     help="actually execute (overwrite shards in place); required to mutate")
    prp.set_defaults(func=_cmd_reprocess)

    pn = sub.add_parser("count", help="distinct object instances (Role 6 tracks)")
    pn.add_argument("text", help="class name(s), e.g. 'car' or 'person dog'")
    pn.add_argument("--min-frames", type=int, default=2,
                    help="ignore tracks seen in fewer frames (flicker filter)")
    pn.set_defaults(func=_cmd_count)

    pag = sub.add_parser(
        "aggregate",
        help="windowed, tz-aware object aggregation (typed query tier)",
        description=(
            "Deterministic aggregation over object tracks, bounded to an "
            "explicit wall-clock window. --tz is REQUIRED: a count with no "
            "timezone is ambiguous (the same window counted 111 local vs 147 "
            "UTC on real footage). Every result prints its caveats — today's "
            "counts are a raw upper bound (no cross-window/camera "
            "re-identification, parked objects included, window membership by "
            "track start). Only wall-clock-anchored videos can be windowed "
            "(e.g. NVR ingests, which record start_epoch): standalone/edited "
            "videos (YouTube, local files) have no clock anchor, are EXCLUDED, "
            "and the exclusion is disclosed in the caveats — a workdir with "
            "none prints NOT APPLICABLE rather than a bare 0; use `va count` "
            "(whole-corpus) for such footage."))
    agsub = pag.add_subparsers(dest="agcmd", required=True)

    def _ag_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("category",
                       help="object category as a DETECTOR CLASS NAME, e.g. "
                            "'car' or 'person' (plurals ok: 'cars'; synonyms/"
                            "hypernyms like 'vehicle' or 'people' do NOT map "
                            "to classes until the Role-12 taxonomy)")
        p.add_argument("--from", dest="start", required=True, metavar="WHEN",
                       help="window start, ISO 8601 wall-clock (e.g. 2026-08-11T00:00)")
        p.add_argument("--to", dest="end", required=True, metavar="WHEN",
                       help="window end (exclusive), same format")
        p.add_argument("--tz", required=True,
                       help="IANA timezone the window is expressed in, e.g. "
                            "America/Los_Angeles (REQUIRED — an unqualified "
                            "count is meaningless)")
        p.add_argument("--camera", action="append", default=None, metavar="CAM",
                       help="restrict to a camera id, e.g. nvr-ch2 (repeatable; "
                            "default: all cameras)")
        p.add_argument("--dedup", choices=["raw", "instance"], default="raw",
                       help="'instance' (cross-window ReID) is accepted but "
                            "falls back to raw with a caveat until Role 12 lands")
        p.add_argument("--min-frames", type=int, default=2,
                       help="ignore tracks seen in fewer frames (flicker filter)")

    pagc = agsub.add_parser("count", help="how many distinct tracks, per camera")
    _ag_common(pagc)
    page = agsub.add_parser("events", help="the rows behind a count, wall-clock placed")
    _ag_common(page)
    page.add_argument("--limit", type=int, default=100, help="max rows printed")
    pagh = agsub.add_parser("histogram", help="counts per time bucket across the window")
    _ag_common(pagh)
    pagh.add_argument("--bucket", default="1h", metavar="WIDTH",
                      help="bucket width: <int><s|m|h|d>, e.g. 1h, 30m (default 1h)")
    pag.set_defaults(func=_cmd_aggregate)

    psv = sub.add_parser("serve", help="web UI: ingest + search from a browser")
    psv.add_argument("--host", default="0.0.0.0", help="bind address (default: all interfaces)")
    psv.add_argument("--port", type=int, default=8080)
    psv.add_argument("--trace", action="store_true",
                     help="enable pipeline tracing for the server (sets VA_TRACE=1)")
    psv.set_defaults(func=_cmd_serve)

    ptr = sub.add_parser("trace", help="show/list/prune pipeline traces (needs VA_TRACE runs)")
    ptr.add_argument("target", nargs="?",
                     help="run_id | 'list' | 'prune'  (default: most recent run)")
    ptr.add_argument("--last", action="store_true", help="show the most recent run")
    ptr.add_argument("--keep", type=int, help="prune: keep the N newest")
    ptr.add_argument("--older-than", type=float, dest="older_than",
                     help="prune: remove files older than N days")
    ptr.add_argument("--all", action="store_true", help="prune: remove all traces")
    ptr.set_defaults(func=_cmd_trace)

    pbn = sub.add_parser("bench",
                         help="benchmark ingest + query latency in an isolated workdir")
    pbn.add_argument("--video", default=None,
                     help="local media file to ingest (default: auto-find a media.* under a workdir)")
    pbn.add_argument("--bench-workdir", dest="bench_workdir", default=".va-bench",
                     help="ISOLATED workdir, cleared each run (default: .va-bench)")
    pbn.add_argument("--runs", type=int, default=5,
                     help="clean ingest runs per video, averaged (default: 5)")
    pbn.add_argument("--fps", type=float, default=1.0)
    pbn.add_argument("-k", type=int, default=10, help="top-k per query")
    pbn.add_argument("--iters", type=int, default=10, help="query repetitions for p50/p95")
    pbn.add_argument("--queries", default=None, help="pipe-separated query list")
    pbn.add_argument("--save", default=None, help="write the baseline JSON to this path")
    pbn.set_defaults(func=_cmd_bench)

    prm = sub.add_parser("remove", help="delete a video everywhere (rows + artifacts)")
    prm.add_argument("video", help="video UUID, source_key, URL, or path")
    prm.set_defaults(func=_cmd_remove)

    pri = sub.add_parser("reingest", help="remove + ingest again (e.g. after model change)")
    pri.add_argument("video", help="video UUID, source_key, URL, or path")
    pri.add_argument("--fps", type=float, default=1.0)
    pri.add_argument(
        "--profile", default=None,
        help="footage profile to reingest under (default: the video's recorded profile)",
    )
    pri.set_defaults(func=_cmd_reingest)

    pmg = sub.add_parser("migrate-layout", help="migrate a workdir to layout v2 (per-video dirs)")
    pmg.set_defaults(func=_cmd_migrate)

    pf = sub.add_parser("fixtures", help="manage test fixtures")
    pfsub = pf.add_subparsers(dest="fcmd", required=True)
    pfpull = pfsub.add_parser("pull", help="download pinned fixtures")
    pfpull.set_defaults(func=_cmd_fixtures)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
