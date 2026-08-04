"""WS2.d — profile-wide knobs (retention / time model / deep-scan gate).

Done-conditions from architecture-evolution-loop.md: the knobs parse, validate,
and land on the loaded config with defaults matching today's behavior. (They land
on `Config.footage`, not per-role `RoleConfig` — they are profile-wide, and the
consumers (P7.a prune, WS-3 presentation, R11.a gate) read the config object.)
"""
from pathlib import Path

import pytest
import yaml

from va.configuration import FootageSettings, load_config

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"


def _config_with(tmp_path, name, text):
    import shutil

    cdir = tmp_path / "config"
    if not cdir.exists():
        shutil.copytree(REPO_CONFIG, cdir)
    (cdir / "profiles" / "footage" / f"{name}.yaml").write_text(text)
    return cdir


def test_defaults_match_today():
    s = FootageSettings()
    assert s.retention_days is None
    assert s.time_model == "relative"
    assert s.deep_scan == "auto"
    # A profile-less load carries the defaults.
    assert load_config(REPO_CONFIG).footage == s


def test_security_profile_carries_the_plan_decisions():
    f = load_config(REPO_CONFIG, footage_profile="security").footage
    assert f.retention_days == 14        # plan §8.2 (locked)
    assert f.time_model == "wall_clock"  # plan §4
    assert f.deep_scan == "off"          # plan §8.1 / R11.a


def test_knobs_parse_from_a_custom_profile(tmp_path):
    cdir = _config_with(tmp_path, "knobs",
                        "retention_days: 7\ntime_model: relative\nroles: {}\n")
    f = load_config(cdir, footage_profile="knobs").footage
    assert f.retention_days == 7 and f.deep_scan == "auto"


@pytest.mark.parametrize("body,match", [
    ("retention_days: -3\nroles: {}\n", "positive"),
    ("time_model: absolute\nroles: {}\n", "time_model"),
    ("deep_scan: off\nroles: {}\n", "deep_scan"),   # bare off = YAML False, not "off"
    ("retention_dyas: 14\nroles: {}\n", "retention_dyas"),  # typo'd knob must not no-op
])
def test_invalid_settings_raise_naming_the_file(tmp_path, body, match):
    cdir = _config_with(tmp_path, "bad", body)
    with pytest.raises(ValueError, match="bad.yaml") as e:
        load_config(cdir, footage_profile="bad")
    assert match in str(e.value)


def test_all_shipped_profiles_parse():
    for d in ("config", "run-siglip/config", "run-claude/config", "run-qwen3vl/config"):
        root = Path(__file__).resolve().parents[1] / d
        for prof in ("generic", "security"):
            cfg = load_config(root, footage_profile=prof)
            assert cfg.footage is not None
