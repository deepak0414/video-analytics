"""Ingest stamps per-role provenance (WS-1 §6-b, PROV-3). Offline: stub backends +
a synth color clip, so every tracked role runs and gets a row.
"""
from va.media.synth import write_color_video
from va.pipeline.ingest import ingest
from va.pipeline.paths import Workspace
from va.provenance import PROVENANCE_ROLES, role_fingerprint
from va.storage.structured.catalog_sqlite import Catalog
from va.storage.structured.provenance_store import ProvenanceStore


def _clip(tmp_path):
    return write_color_video(
        tmp_path / "clip.mp4",
        [("red", (220, 30, 30), 2.0), ("green", (30, 180, 30), 2.0)], fps=10)


def test_ingest_stamps_provenance_for_every_tracked_role(tmp_path):
    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    pv = ProvenanceStore(Workspace(wd).catalog_db)
    try:
        roles = {r["role"] for r in pv.get(res.video.id)}
    finally:
        pv.close()
    assert roles == set(PROVENANCE_ROLES)            # exactly the tracked roles — no drift


def test_stamped_fingerprint_matches_the_identity_helper(tmp_path):
    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    pv = ProvenanceStore(Workspace(wd).catalog_db)
    try:
        ve = next(r for r in pv.get(res.video.id) if r["role"] == "visual_embedder")
    finally:
        pv.close()
    ident = role_fingerprint("visual_embedder")
    assert ve["model"] == ident["model"]
    assert ve["fingerprint"] == ident["fingerprint"]
    assert ve["fps"] == 1.0                          # the run-arg is recorded on the row


def test_reingest_restamps_provenance_at_the_new_fps(tmp_path):
    from va.pipeline.manage import reingest_video

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    reingest_video(wd, str(res.video.id), fps=2.0)   # re-run the same source at a new fps

    ws = Workspace(wd)
    cat = Catalog(ws.catalog_db)
    try:
        v = cat.get_by_source_key(res.video.source_key)   # reingest gives a fresh id
    finally:
        cat.close()
    pv = ProvenanceStore(ws.catalog_db)
    try:
        rows = pv.get(v.id)
        assert rows and all(r["fps"] == 2.0 for r in rows)          # re-stamped at new fps
        assert len(rows) == len({r["role"] for r in rows})          # one row per role (upsert)
    finally:
        pv.close()


def test_a_failed_role_is_not_stamped_as_current(tmp_path, monkeypatch):
    # a best-effort role that RAISES must NOT get a provenance row (absent = stale to
    # `va stale`), else a transient failure is masked as current and never reprocessed.
    import va.pipeline.ingest as ing

    class _BoomOCR:
        def read(self, *a, **k):
            raise RuntimeError("ocr boom")

    monkeypatch.setattr(ing, "get_ocr_reader", lambda *a, **k: _BoomOCR())

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    pv = ProvenanceStore(Workspace(wd).catalog_db)
    try:
        roles = {r["role"] for r in pv.get(res.video.id)}
    finally:
        pv.close()
    assert "ocr" not in roles                          # failed role skipped...
    assert "visual_embedder" in roles                  # ...others still stamped


def test_stamp_uses_config_pinned_at_role_launch_not_ingest_end(tmp_path, monkeypatch):
    # The provenance stamp must fingerprint the config pinned when the roles LAUNCH, not a
    # fresh load_config() at ingest END: otherwise a mid-ingest roles.yaml edit would stamp
    # old-model rows with the NEW fingerprint -> `va stale` reports them current -> a MISSED
    # stale, the one failure §6-b forbids. Guards that the pinned cfg flows into every
    # role_fingerprint call (a refactor dropping the cfg arg would call it with cfg=None).
    import va.pipeline.ingest as ing
    import va.provenance as provenance

    pinned = object()                                  # a sentinel "config at role-launch"
    monkeypatch.setattr(ing, "load_config", lambda: pinned)

    seen: list = []
    monkeypatch.setattr(provenance, "role_fingerprint",
                        lambda role, cfg=None: seen.append(cfg) or {"model": "m", "fingerprint": "fp"})

    wd = str(tmp_path / ".va")
    ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)

    assert seen, "role_fingerprint was never called during stamping"
    assert all(c is pinned for c in seen)              # the pinned cfg, never a fresh end-load
