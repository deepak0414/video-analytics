"""Config loading: merge a role's backend choice with the active hardware profile.

`load_config()` reads config/roles.yaml + config/profiles/<active>.yaml (the *hardware*
profile: device/dtype/weights) and, as a third layer, an optional *footage* profile
(config/profiles/footage/<name>.yaml: per-role overrides for an input domain — see
architecture-evolution-plan.md WS-2). The footage overlay is merged into the `roles`
dict at load time, so `Config.role(name)` and raw `Config.roles` consumers see the
overridden specs without any call-site change. The default footage profile, `generic`,
is a no-op: behavior is identical to a config with no footage layer at all.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


def _config_dir() -> Path:
    # Override with VA_CONFIG_DIR; otherwise the repo's config/ dir.
    env = os.environ.get("VA_CONFIG_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "config"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class FootageSettings(BaseModel):
    """Profile-WIDE knobs a footage profile may set alongside `roles:` (WS2.d —
    config surface only; the consumers land later: `retention_days` → the Tier-1
    rolling prune (plan §8.2/P7.a), `time_model` → how queries/summaries present
    time (plan §4, WS-3), `deep_scan` → the Role-11 reasoning gate (plan §8.6
    item 1, R11.a)). Defaults reproduce today's behavior exactly. extra='forbid':
    a typo'd knob must fail at load, not silently no-op."""
    model_config = ConfigDict(extra="forbid")

    # Days to keep Tier-1 raw data locally; None = keep forever (A-EV behavior).
    retention_days: Optional[float] = None
    # Which time model queries/summaries PRESENT (storage stays relative, plan §4).
    time_model: Literal["relative", "wall_clock"] = "relative"
    # Role-11 deep-scan gating: "auto" = today's trigger heuristics; "off" = the
    # profile forbids deep scans (the §8.1 A-LSSRVF outfit-hijack fix consumes this).
    deep_scan: Literal["auto", "off"] = "auto"

    @field_validator("retention_days")
    @classmethod
    def _positive(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("retention_days must be positive (or null to disable)")
        return v


def _load_footage_overlay(cdir: Path, name: str) -> tuple[dict[str, Any], FootageSettings]:
    """(per-role override dict, profile-wide settings) from
    config/profiles/footage/<name>.yaml.

    `generic` tolerates a missing file (older config dirs like run-siglip/ predate the
    footage layer); any other missing name is a configuration error.
    """
    path = cdir / "profiles" / "footage" / f"{name}.yaml"
    if not path.exists():
        if name == "generic":
            return {}, FootageSettings()
        raise FileNotFoundError(
            f"footage profile '{name}' not found at {path} "
            "(see config/profiles/footage/generic.yaml for the expected shape)"
        )
    doc = yaml.safe_load(path.read_text()) or {}
    if not isinstance(doc, dict) or (
        doc.get("roles") is not None and not isinstance(doc["roles"], dict)
    ):
        raise ValueError(
            f"footage profile {path}: expected a mapping with a `roles:` mapping "
            "(see config/profiles/footage/generic.yaml for the expected shape)"
        )
    overlay = doc.get("roles") or {}
    for role, spec in overlay.items():
        if spec is not None and not isinstance(spec, dict):
            raise ValueError(
                f"footage profile {path}: role '{role}' must map to a dict of "
                f"overrides (got {type(spec).__name__})"
            )
        if spec and "enabled" in spec and not isinstance(spec["enabled"], bool):
            # A non-bool (quoted "false", null from a half-drafted line) would split
            # the consumers: raw truthiness runs the role while pydantic coercion
            # excludes it from staleness — a missed stale. Reject at load.
            raise ValueError(
                f"footage profile {path}: role '{role}' `enabled:` must be a "
                f"boolean (got {spec['enabled']!r})"
            )
        if spec and spec.get("enabled") is False and role not in GATEABLE_ROLES:
            raise ValueError(
                f"footage profile {path}: role '{role}' is a core role and cannot "
                f"be disabled (gateable roles: {sorted(GATEABLE_ROLES)})"
            )
    try:
        settings = FootageSettings(**{k: v for k, v in doc.items() if k != "roles"})
    except ValidationError as e:
        raise ValueError(f"footage profile {path}: invalid settings — {e}") from e
    return overlay, settings


# Source-derived footage-profile defaults (WS-2). youtube/local (edited video)
# fall through to the no-op `generic`; an NVR chunk is A-LSSRVF footage by
# construction, so it defaults to the `security` profile (WS4.c) — an explicit
# `--profile` still overrides.
_SOURCE_PROFILE_DEFAULTS: dict[str, str] = {"nvr_recorded": "security"}

# The roles a footage profile may disable (`enabled: false`) — the best-effort
# set ingest gates. CORE roles (scene detect, visual/text embedders) are the
# pipeline's spine: ingest runs and stamps them regardless, so letting a profile
# "disable" one would (a) do nothing and (b) exclude it from staleness — a
# missed stale, the direction §6-b forbids. The overlay loader rejects it.
GATEABLE_ROLES = frozenset({
    "vlm_captioner", "speech_to_text", "speaker_diarizer", "ocr",
    "action_recognizer", "object_detector", "object_tracker",
})

# Disabling a parent role implicitly skips its dependents at ingest (no transcript
# to diarize, no detections to track). Staleness must apply the same closure, or a
# dependency-skipped role — unstamped by design — reads stale forever with no
# remedy (reprocess → reingest → skipped again).
GATE_DEPENDENTS: dict[str, frozenset[str]] = {
    "speech_to_text": frozenset({"speaker_diarizer"}),
    "object_detector": frozenset({"object_tracker"}),
}


def default_footage_profile(source_type: str) -> str:
    """Which footage profile an ingest runs under when the caller doesn't say."""
    return _SOURCE_PROFILE_DEFAULTS.get(source_type, "generic")


def config_for(profile: Optional[str], source_type: str) -> "Config":
    """The config a video's roles run (or ran) under: its recorded footage profile,
    falling back to the source-derived default for pre-profile rows (WS-2). The
    read side of the WS2.c record==reality rule — stale/reprocess must compare a
    video against THIS config, not the base one, or every profile-ingested video
    reads permanently stale and a reprocess strips its overrides."""
    return load_config(footage_profile=profile or default_footage_profile(source_type))


class RoleConfig(BaseModel):
    name: str
    backend: str                 # inproc | http | cloud
    model: Optional[str] = None
    endpoint: Optional[str] = None
    # A footage profile can disable a role for its input domain (WS-2, e.g. the
    # security profile skips speech roles — no mic). Ingest honors this for the
    # best-effort roles; core roles (scene detect, visual/text embed) ignore it.
    enabled: bool = True
    # Load params resolved from the active profile (device/dtype/quant/weights/…).
    load: dict[str, Any] = {}


class Config(BaseModel):
    active_profile: str
    profile: dict[str, Any]
    roles: dict[str, dict[str, Any]]
    footage_profile: str = "generic"
    # Profile-wide knobs from the footage yaml's top level (WS2.d).
    footage: FootageSettings = Field(default_factory=FootageSettings)

    def role(self, name: str) -> RoleConfig:
        if name not in self.roles:
            raise KeyError(f"role '{name}' not configured in roles.yaml")
        spec = dict(self.roles[name])
        model = spec.get("model")
        # Fold profile defaults + per-model overrides into one `load` dict.
        load: dict[str, Any] = {
            k: v for k, v in self.profile.items() if k not in ("models",)
        }
        if model:
            load.update((self.profile.get("models") or {}).get(model, {}))
        return RoleConfig(
            name=name,
            backend=spec.get("backend", "inproc"),
            model=model,
            endpoint=spec.get("endpoint"),
            enabled=spec.get("enabled", True),
            load=load,
        )


def load_config(
    config_dir: Optional[Path] = None,
    footage_profile: Optional[str] = None,
) -> Config:
    cdir = Path(config_dir) if config_dir else _config_dir()
    roles_doc = yaml.safe_load((cdir / "roles.yaml").read_text())
    active = roles_doc.get("active_profile", "dgx-spark")
    profile = yaml.safe_load((cdir / "profiles" / f"{active}.yaml").read_text()) or {}
    footage = footage_profile or roles_doc.get("active_footage_profile") or "generic"
    roles: dict[str, dict[str, Any]] = roles_doc.get("roles", {})
    overlay, settings = _load_footage_overlay(cdir, footage)
    if overlay:
        unknown = sorted(set(overlay) - set(roles))
        if unknown:
            raise KeyError(
                f"footage profile '{footage}' overrides role(s) not in roles.yaml: "
                f"{unknown} (known: {sorted(roles)})"
            )
        roles = {
            name: _deep_merge(spec, overlay.get(name) or {})
            for name, spec in roles.items()
        }
    return Config(
        active_profile=active, profile=profile, roles=roles,
        footage_profile=footage, footage=settings,
    )
