"""Provenance identity (WS-1 §6-b, PROV-1).

A stable fingerprint of *what* produced a role's output for a video, so a later
stale-check (PROV-4 `va stale`) can tell which videos are on an outdated model/config
after an upgrade, and a batch reprocess (B) can re-run only those.

**Conservative by exclusion.** The fingerprint hashes the role's model id, its scored
vocabulary, and *every* config param (profile `load` + roles.yaml role-level) EXCEPT a
small, stable set that only changes speed/placement (`device`, `dtype`, `batch_size`,
`residency`, …) or is a credential/routing key (`token`, `endpoint`, …). Enumerating every
output-affecting knob per adapter proved fragile — each reads its own (whisper's `model`
checkpoint, pyannote's `min/max/num_speakers`, the captioner's `max_new_tokens`,
bytetrack's `frame_rate`, yolo's `conf`, ocr's `lang`, …). Excluding the few speed-only
keys instead makes a new or unknown knob output-affecting *by default*, so the failure
mode is a **false stale** (a needless reprocess — wasteful but safe), never a **missed
stale** (mixed old/new data shipped silently).

Known limitations (a `(role, cfg)` identity — documented, not oversights):
- Run-time args that aren't in the config — notably the ingest sampling `--fps` — are
  invisible here; PROV-3 records those on the row separately (see the plan).
- It sees config-SET params, not an adapter's effective defaults: setting a param to its
  own default still changes the fingerprint (a safe false-stale), and a change to an
  adapter's *code* default (a new build) is not seen — provenance tracks config/model
  identity, not build version.
- An unconfigured role fingerprints its vocab but its model as `"unknown"` (the registry's
  default backend isn't resolved here); low exposure — shipped configs set every role.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from va.configuration import Config, load_config

# Keys excluded from the fingerprint: they change speed/placement (device/dtype/batch per
# the D1 decision, plus profile infra) or are credentials/routing that never change the
# stored output. Everything else is treated as output-affecting: a false stale is safe, a
# missed one is not. Grow this set (never the reverse) if a proven non-output key churns.
_NON_OUTPUT_KEYS = frozenset({
    "device", "dtype", "batch_size", "batch", "residency", "num_workers", "compile",
    "token", "hf_token", "auth_token", "api_key", "endpoint",
})

# The roles §6-b stamps provenance for (PROV-3 writes them at ingest, PROV-4 `va stale`
# reads them). The reasoner (Role 11) is on-demand and not stamped here; its one persisted
# output — deep-scan's `observations` cache — is instead kept fresh by folding the
# captioner + reasoner fingerprints into deep_scan's cache keys (so an upgrade re-runs it,
# not a provenance row). ingest._record_provenance stamps exactly this set (a test guards drift).
PROVENANCE_ROLES = (
    "scene_detector", "visual_embedder", "vlm_captioner", "object_detector",
    "object_tracker", "action_recognizer", "speech_to_text", "speaker_diarizer",
    "ocr", "text_embedder",
)


def role_fingerprint(role: str, cfg: Optional[Config] = None) -> dict[str, str]:
    """Provenance identity for `role` under the active (or given) config:
    `{"model": <id>, "fingerprint": <16-hex over the salient params>}`.

    Same model + vocab + non-speed load params -> same fingerprint; changing any of them
    -> a new one (the signal PROV-4 uses to find videos on an outdated model/config). An
    unconfigured role's model degrades to `"unknown"` rather than raising.
    """
    cfg = cfg or load_config()
    if role in cfg.roles:
        rc = cfg.role(role)
        model = rc.model or "unknown"
        load = rc.load or {}
    else:
        model, load = "unknown", {}

    parts: dict[str, Any] = {"model": model}
    for key in sorted(load):
        if key not in _NON_OUTPUT_KEYS:
            # namespaced so load["model"] (a checkpoint) doesn't clobber the role model id
            parts[f"load.{key}"] = load[key]
    # roles.yaml role-level keys are salient by default too (e.g. a future role-level knob
    # like a scene_detector threshold); model/backend are identity/infra and classes/actions
    # are handled by the vocab fold below (which also folds in defaults).
    for key in sorted(cfg.roles.get(role, {})):
        if key in ("model", "backend", "classes", "actions") or key in _NON_OUTPUT_KEYS:
            continue
        parts[f"role.{key}"] = cfg.roles[role][key]
    # Motion-episode segments are a function of WHERE motion windows come from,
    # so the scene detector's identity folds in the motion_source role: switching
    # sidecar -> lnr-eventlog (or changing its events_file/host/tz) must read
    # stale — leaving it out is a missed stale, the direction §6-b forbids
    # (WS4.c review). Purely visual scene models don't consume it, so they
    # deliberately keep their fingerprints independent of motion_source.
    if role == "scene_detector" and model == "motion-episodes":
        ms_spec = cfg.roles.get("motion_source") or {}
        parts["motion_source.model"] = ms_spec.get("model") or "sidecar"
        for key in sorted(ms_spec):
            if key in ("model", "backend") or key in _NON_OUTPUT_KEYS:
                continue
            parts[f"motion_source.{key}"] = ms_spec[key]
    # Scored vocab folds in the DEFAULT_INGEST_* fallbacks, so a default-vocab edit is
    # caught even when the role leaves `classes`/`actions` unset (and even unconfigured).
    if role == "object_detector":
        from va.registry import get_ingest_classes

        parts["classes"] = sorted(get_ingest_classes(cfg))
    elif role == "action_recognizer":
        from va.registry import get_ingest_actions

        parts["actions"] = sorted(get_ingest_actions(cfg))

    blob = json.dumps(parts, sort_keys=True, default=str)
    return {"model": model, "fingerprint": hashlib.sha1(blob.encode()).hexdigest()[:16]}
