"""Config loading: merge a role's backend choice with the active hardware profile.

`load_config()` reads config/roles.yaml + config/profiles/<active>.yaml and returns
a `Config`. `Config.role(name)` yields a `RoleConfig` with the model's resolved
load params folded in — so adapters get one object describing both *what* to run
and *how* to load it.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel


def _config_dir() -> Path:
    # Override with VA_CONFIG_DIR; otherwise the repo's config/ dir.
    env = os.environ.get("VA_CONFIG_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "config"


class RoleConfig(BaseModel):
    name: str
    backend: str                 # inproc | http | cloud
    model: Optional[str] = None
    endpoint: Optional[str] = None
    # Load params resolved from the active profile (device/dtype/quant/weights/…).
    load: dict[str, Any] = {}


class Config(BaseModel):
    active_profile: str
    profile: dict[str, Any]
    roles: dict[str, dict[str, Any]]

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
            load=load,
        )


def load_config(config_dir: Optional[Path] = None) -> Config:
    cdir = Path(config_dir) if config_dir else _config_dir()
    roles_doc = yaml.safe_load((cdir / "roles.yaml").read_text())
    active = roles_doc.get("active_profile", "dgx-spark")
    profile = yaml.safe_load((cdir / "profiles" / f"{active}.yaml").read_text()) or {}
    return Config(active_profile=active, profile=profile, roles=roles_doc.get("roles", {}))
