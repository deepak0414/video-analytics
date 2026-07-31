"""Batch-reprocess selection (WS-1 §6-b pillar B / RPRC-3 — dry-run front-end).

`plan_reprocess` resolves the stale (video, role) set scoped by role/video/all-stale,
read-only. Offline: stub ingest, then poke provenance to simulate a model change. The CLI
`va reprocess` can only PLAN today — execution (RPRC-1) is gated off.
"""
import pytest

from va.media.synth import write_color_video
from va.pipeline.ingest import ingest
from va.pipeline.paths import Workspace
from va.pipeline.reprocess import plan_reprocess
from va.storage.structured.provenance_store import ProvenanceStore


_COLORS_A = [("red", (220, 30, 30), 2.0), ("green", (30, 180, 30), 2.0)]
_COLORS_B = [("blue", (30, 30, 200), 2.0), ("yellow", (200, 200, 30), 2.0)]


def _clip(tmp_path, name="clip.mp4", colors=None):
    # distinct content -> distinct sha256 -> distinct source_key (else ingest DEDUPES them)
    return write_color_video(tmp_path / name, colors or _COLORS_A, fps=10)


def _make_stale(wd, video_id, role="ocr"):
    pv = ProvenanceStore(Workspace(wd).catalog_db)
    try:
        pv.record(video_id, role, "old-model", "STALE-FP", fps=2.0)   # != current fingerprint
    finally:
        pv.close()


def test_all_stale_returns_the_stale_set(tmp_path):
    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=2.0)
    _make_stale(wd, res.video.id)
    plan = plan_reprocess(wd, all_stale=True)
    assert len(plan) == 1
    assert plan[0]["stale_roles"] == ["ocr"]
    assert plan[0]["recorded_fps"] == 2.0            # fps carried through for the executor


def test_nothing_stale_gives_empty_plan(tmp_path):
    wd = str(tmp_path / ".va")
    ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    assert plan_reprocess(wd, all_stale=True) == []


def test_video_scope_selects_only_that_video(tmp_path):
    wd = str(tmp_path / ".va")
    a = ingest(str(_clip(tmp_path, "a.mp4", _COLORS_A)), workdir=wd, fps=1.0)
    b = ingest(str(_clip(tmp_path, "b.mp4", _COLORS_B)), workdir=wd, fps=1.0)
    assert a.video.id != b.video.id                  # distinct content -> two videos
    _make_stale(wd, a.video.id)                      # only A is stale
    pa = plan_reprocess(wd, video=str(a.video.id))
    assert len(pa) == 1 and pa[0]["video_id"] == str(a.video.id)
    assert plan_reprocess(wd, video=str(b.video.id)) == []   # B is current -> nothing


def test_role_filter_scopes_within_all_stale(tmp_path):
    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    _make_stale(wd, res.video.id, role="ocr")
    assert plan_reprocess(wd, all_stale=True, role="visual_embedder") == []
    assert plan_reprocess(wd, all_stale=True, role="ocr")[0]["stale_roles"] == ["ocr"]


def test_role_and_video_scopes_combine(tmp_path):
    # --role + --video together must intersect: only that video, only that role's staleness.
    # Guards against a refactor that filters by video before role-scoping (which would leak a
    # video's other stale roles into a --role query, so the executor re-runs current roles).
    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    _make_stale(wd, res.video.id, role="ocr")        # stale for ocr, current for the rest
    vid = str(res.video.id)
    assert plan_reprocess(wd, video=vid, role="ocr")[0]["stale_roles"] == ["ocr"]   # A: stale
    assert plan_reprocess(wd, video=vid, role="visual_embedder") == []              # B: current


def test_requires_exactly_one_video_scope(tmp_path):
    wd = str(tmp_path / ".va")
    ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    with pytest.raises(ValueError):
        plan_reprocess(wd)                           # neither scope
    with pytest.raises(ValueError):
        plan_reprocess(wd, all_stale=True, video="x")    # both scopes


def test_unknown_video_raises(tmp_path):
    wd = str(tmp_path / ".va")
    ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    with pytest.raises(ValueError):
        plan_reprocess(wd, video="not-a-real-video")


def test_unknown_role_raises(tmp_path):
    wd = str(tmp_path / ".va")
    ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    with pytest.raises(ValueError):
        plan_reprocess(wd, all_stale=True, role="reasoner")   # unstamped role


def test_non_done_video_scope_flags_reingest_not_current(tmp_path):
    # a --video target whose ingest never completed must NOT read as "already current" (an
    # empty plan): stale_report done-filters it, so surface that it needs re-ingest instead.
    from va.contracts.video import IngestStatus
    from va.storage.structured.catalog_sqlite import Catalog

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    cat = Catalog(Workspace(wd).catalog_db)
    try:
        cat.set_status(res.video.id, IngestStatus.pending)   # simulate a crashed ingest
    finally:
        cat.close()
    with pytest.raises(ValueError, match="not complete"):
        plan_reprocess(wd, video=str(res.video.id))


def test_cli_execution_is_gated_off(tmp_path, capsys):
    from va.cli import build_parser

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=2.0)
    _make_stale(wd, res.video.id)
    parser = build_parser()

    # no --dry-run: the plan is shown, but execution REFUSES (rc=1) — never a silent no-op
    args = parser.parse_args(["--workdir", wd, "reprocess", "--all-stale"])
    rc = args.func(args)
    cap = capsys.readouterr()
    assert rc == 1
    assert "not implemented" in (cap.out + cap.err)
    assert "ocr" in cap.out                          # plan still printed

    # --dry-run: rc=0 and a clear no-changes notice
    args = parser.parse_args(["--workdir", wd, "reprocess", "--all-stale", "--dry-run"])
    rc = args.func(args)
    cap = capsys.readouterr()
    assert rc == 0 and "dry run" in cap.out


def test_cli_requires_a_scope():
    from va.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit):                  # neither --all-stale nor --video
        parser.parse_args(["--workdir", "x", "reprocess"])
    with pytest.raises(SystemExit):                  # both (mutually exclusive)
        parser.parse_args(["--workdir", "x", "reprocess", "--all-stale", "--video", "v"])
