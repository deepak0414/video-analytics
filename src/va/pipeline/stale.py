"""Stale-video report (WS-1 §6-b, PROV-4).

Compare each video's *recorded* provenance fingerprint (PROV-3, `role_provenance`)
against the CURRENT config's fingerprint (PROV-1, `role_fingerprint`). A (video, role) is
stale when they differ — or the role was never stamped — i.e. the videos a model/config
upgrade needs to reprocess (pillar B). Read-only; drives `va stale`.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

from va.configuration import (
    GATE_DEPENDENTS,
    GATEABLE_ROLES,
    config_for,
    default_footage_profile,
)
from va.contracts.video import IngestStatus
from va.pipeline.paths import Workspace
from va.provenance import PROVENANCE_ROLES, role_fingerprint
from va.storage.structured.catalog_sqlite import Catalog
from va.storage.structured.provenance_store import ProvenanceStore


def stale_report(workdir: str, role: Optional[str] = None) -> list[dict[str, Any]]:
    """DONE videos with >= 1 stale role. A role is stale when its recorded fingerprint !=
    the current config's (or it was never stamped — a done-but-unstamped video, e.g.
    pre-PROV-3, counts as stale for every role). `role` limits the check to that one role.
    Non-done videos are skipped (they need re-ingest, not a role reprocess). Returns
    `[{video_id, source_uri, title, stale_roles, recorded_fps}]`, newest catalog order
    (`recorded_fps` = the ingest fps to preserve on reprocess, or None if unknown).
    """
    if role is not None and role not in PROVENANCE_ROLES:
        # A role that's never stamped (e.g. the on-demand reasoner) or a typo would
        # fingerprint fine but match no recorded row, marking EVERY video stale — a
        # confidently-wrong report. Fail loudly instead (the CLI also guards via choices).
        raise ValueError(
            f"unknown provenance role {role!r}; expected one of {', '.join(PROVENANCE_ROLES)}")
    roles = [role] if role else list(PROVENANCE_ROLES)
    # One config snapshot PER FOOTAGE PROFILE for the whole report (memoized): each
    # video is compared against the config its roles actually run under — its
    # recorded profile (WS2.c record==reality) — not the base config, which would
    # read every profile-ingested video as permanently stale for overridden roles.
    # Roles the profile DISABLES are excluded outright: they are deliberately
    # unstamped (never run under this profile), not stale.
    _snap: dict[str, tuple[dict[str, str], set[str]]] = {}

    def _current(v) -> tuple[dict[str, str], set[str]]:
        prof = v.profile or default_footage_profile(v.source_type.value)
        if prof not in _snap:
            cfg = config_for(v.profile, v.source_type.value)
            fps_ = {r: role_fingerprint(r, cfg)["fingerprint"] for r in roles}
            disabled = {r for r in GATEABLE_ROLES
                        if r in cfg.roles and not cfg.role(r).enabled}
            for parent in list(disabled):
                # dependency closure: ingest skips these with their parent
                disabled |= GATE_DEPENDENTS.get(parent, frozenset())
            disabled &= set(roles)
            _snap[prof] = (fps_, disabled)
        return _snap[prof]

    ws = Workspace(workdir)
    cat = Catalog(ws.catalog_db)
    pv = ProvenanceStore(ws.catalog_db)
    try:
        out: list[dict[str, Any]] = []
        for v in cat.list():
            # Only DONE videos: a never-completed ingest has no rows to reprocess (it
            # needs re-ingest, not a role reprocess), so listing it as stale-everywhere
            # would conflate incomplete-ingest with model drift.
            if v.ingest_status is not IngestStatus.done:
                continue
            rows = pv.get(v.id)
            recorded = {row["role"]: row["fingerprint"] for row in rows}
            try:
                current, disabled = _current(v)
            except Exception as e:  # noqa: BLE001 — e.g. the video's profile yaml was
                # renamed; one broken profile must not kill the whole report.
                logger.warning(
                    "stale: skipping video %s — footage profile %r can't be loaded (%s)",
                    v.id, v.profile, e)
                continue
            # A disabled role is excluded ONLY while unstamped (never ran under this
            # profile). Stamped-AND-disabled means the profile was edited after the
            # ingest: the rows contradict the profile and must surface as stale
            # (the remedy — reingest under the carried profile — purges them).
            stale = [r for r in roles
                     if (r in disabled and recorded.get(r) is not None)
                     or (r not in disabled and recorded.get(r) != current[r])]
            if stale:
                # Surface the recorded ingest fps (all roles of one ingest share it) so a
                # reprocess can preserve the frame density Roles 2/5/6/7 saw — `va reingest`
                # defaults to fps=1.0, which would otherwise silently change it. fps is a run
                # arg with no config baseline, so it is REPORTED, not compared (staleness is
                # still fingerprint-only). None when unknown (never stamped) or inconsistent.
                fps_vals = {row["fps"] for row in rows if row["fps"] is not None}
                out.append({
                    "video_id": str(v.id),
                    "source_uri": v.source_uri,
                    "title": v.title,
                    "stale_roles": stale,
                    "recorded_fps": next(iter(fps_vals)) if len(fps_vals) == 1 else None,
                    # The footage profile the comparison ran under — reprocess must
                    # rebuild + restamp under this same profile (WS2.c).
                    "profile": v.profile,
                    "source_type": v.source_type.value,
                })
        return out
    finally:
        pv.close()
        cat.close()
