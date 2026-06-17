"""`va` command-line entrypoint.

Subcommands (wired to the pipeline in later tasks):
  va ingest <uri>         ingest a YouTube URL or local file (idempotent)
  va query  "<text>"      search ingested videos, print ranked moments
  va fixtures pull        download pinned test fixtures

Kept thin on purpose: it parses args and delegates to va.pipeline.*.
"""
from __future__ import annotations

import argparse
import sys


def _cmd_ingest(args: argparse.Namespace) -> int:
    from va.pipeline.ingest import ingest

    result = ingest(args.uri, workdir=args.workdir, fps=args.fps)
    status = "already-ingested" if result.deduped else "ingested"
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

    hits = query(args.text, workdir=args.workdir, k=args.k)
    if getattr(args, "verify", False):
        # SR.6: VLM-verify the candidates (drops attribute/composition false hits).
        # No-op unless a real verifier is configured (VA_CONFIG_DIR=run-*/config).
        from va.pipeline.verify import verify_visual_hits

        hits = verify_visual_hits(hits, args.text, workdir=args.workdir,
                                  floor=0.10, stop_after_accepts=1)
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

    result = reingest_video(args.workdir, args.video, fps=args.fps)
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
    uvicorn.run(create_app(args.workdir), host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="va", description="Ctrl-F for Video")
    p.add_argument("--workdir", default=".va", help="state dir (db, vectors, cache)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("ingest", help="ingest a video URL or local path")
    pi.add_argument("uri")
    pi.add_argument("--fps", type=float, default=1.0, help="frame sampling rate")
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

    pn = sub.add_parser("count", help="distinct object instances (Role 6 tracks)")
    pn.add_argument("text", help="class name(s), e.g. 'car' or 'person dog'")
    pn.add_argument("--min-frames", type=int, default=2,
                    help="ignore tracks seen in fewer frames (flicker filter)")
    pn.set_defaults(func=_cmd_count)

    psv = sub.add_parser("serve", help="web UI: ingest + search from a browser")
    psv.add_argument("--host", default="0.0.0.0", help="bind address (default: all interfaces)")
    psv.add_argument("--port", type=int, default=8080)
    psv.set_defaults(func=_cmd_serve)

    prm = sub.add_parser("remove", help="delete a video everywhere (rows + artifacts)")
    prm.add_argument("video", help="video UUID, source_key, URL, or path")
    prm.set_defaults(func=_cmd_remove)

    pri = sub.add_parser("reingest", help="remove + ingest again (e.g. after model change)")
    pri.add_argument("video", help="video UUID, source_key, URL, or path")
    pri.add_argument("--fps", type=float, default=1.0)
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
