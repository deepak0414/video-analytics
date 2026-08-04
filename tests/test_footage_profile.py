"""WS2.a — the footage-profile third config layer.

Done-conditions from architecture-evolution-loop.md: (i) the `generic` footage profile
yields a RoleConfig identical to a config with no footage layer at all; (ii) an override
profile changes exactly the overridden keys and nothing else.
"""
from pathlib import Path

import pytest
import yaml

from va.configuration import load_config

ROLES_DOC = {
    "active_profile": "testprof",
    "roles": {
        "visual_embedder": {"backend": "inproc", "model": "hash", "params": {"fps": 1}},
        "speech_to_text": {"backend": "inproc", "model": "sidecar"},
    },
}
PROFILE_DOC = {"device": "cpu", "models": {"hash": {"dim": 64}}}


def write_config(
    tmp_path: Path, footage: dict | None = None, footage_name: str = "security"
) -> Path:
    cdir = tmp_path / "config"
    (cdir / "profiles").mkdir(parents=True)
    (cdir / "roles.yaml").write_text(yaml.safe_dump(ROLES_DOC))
    (cdir / "profiles" / "testprof.yaml").write_text(yaml.safe_dump(PROFILE_DOC))
    if footage is not None:
        fdir = cdir / "profiles" / "footage"
        fdir.mkdir()
        (fdir / f"{footage_name}.yaml").write_text(yaml.safe_dump(footage))
    return cdir


def role_dumps(cfg):
    return {name: cfg.role(name).model_dump() for name in cfg.roles}


def test_generic_is_a_noop(tmp_path):
    # Three ways of getting `generic` — no footage dir at all, an explicit empty
    # generic.yaml, and asking for it by name — must all be byte-for-byte identical.
    bare = load_config(write_config(tmp_path / "a"))
    with_file = load_config(
        write_config(tmp_path / "b", footage={"roles": {}}, footage_name="generic")
    )
    by_name = load_config(write_config(tmp_path / "c"), footage_profile="generic")

    assert bare.footage_profile == "generic"
    assert role_dumps(bare) == role_dumps(with_file) == role_dumps(by_name)
    assert bare.roles == with_file.roles == by_name.roles


def test_override_changes_exactly_the_overridden_keys(tmp_path):
    overlay = {"roles": {"visual_embedder": {"model": "siglip", "params": {"fps": 2}}}}
    cdir = write_config(tmp_path, footage=overlay)
    base = load_config(cdir)
    overridden = load_config(cdir, footage_profile="security")

    assert overridden.footage_profile == "security"
    # Overridden keys changed…
    assert overridden.role("visual_embedder").model == "siglip"
    assert overridden.roles["visual_embedder"]["params"]["fps"] == 2
    # …non-overridden keys of the same role survive the deep-merge…
    assert overridden.roles["visual_embedder"]["backend"] == "inproc"
    # …and untouched roles are byte-for-byte identical.
    assert overridden.roles["speech_to_text"] == base.roles["speech_to_text"]
    assert (
        overridden.role("speech_to_text").model_dump()
        == base.role("speech_to_text").model_dump()
    )


def test_missing_named_footage_profile_raises(tmp_path):
    cdir = write_config(tmp_path)
    with pytest.raises(FileNotFoundError, match="footage profile 'nvr'"):
        load_config(cdir, footage_profile="nvr")


def test_empty_role_body_is_tolerated(tmp_path):
    # A half-drafted profile ('visual_embedder:' with everything commented out)
    # parses as None — must behave like an empty override, not crash.
    cdir = write_config(tmp_path, footage={"roles": {"visual_embedder": None}})
    cfg = load_config(cdir, footage_profile="security")
    assert cfg.roles["visual_embedder"] == ROLES_DOC["roles"]["visual_embedder"]


def test_unknown_role_in_overlay_raises(tmp_path):
    # A typo'd role name must fail loudly, not silently never apply.
    cdir = write_config(tmp_path, footage={"roles": {"visual_embeder": {"model": "x"}}})
    with pytest.raises(KeyError, match="visual_embeder"):
        load_config(cdir, footage_profile="security")


def test_non_dict_role_override_raises(tmp_path):
    cdir = write_config(tmp_path, footage={"roles": {"visual_embedder": "siglip"}})
    with pytest.raises(ValueError, match="must map to a dict"):
        load_config(cdir, footage_profile="security")


def test_top_level_roles_as_list_raises_named_error(tmp_path):
    # Regression (WS2.a review carry-over): `roles:` written as a YAML LIST used to
    # crash with a bare AttributeError; it must be a ValueError naming the file.
    cdir = write_config(tmp_path)
    fdir = cdir / "profiles" / "footage"
    fdir.mkdir()
    (fdir / "listy.yaml").write_text(
        "roles:\n  - visual_embedder:\n      model: siglip\n"
    )
    with pytest.raises(ValueError, match="listy.yaml"):
        load_config(cdir, footage_profile="listy")


def test_whole_document_as_list_raises_named_error(tmp_path):
    cdir = write_config(tmp_path)
    fdir = cdir / "profiles" / "footage"
    fdir.mkdir()
    (fdir / "docly.yaml").write_text("- roles\n- other\n")
    with pytest.raises(ValueError, match="docly.yaml"):
        load_config(cdir, footage_profile="docly")


def test_roles_yaml_can_select_the_footage_profile(tmp_path):
    overlay = {"roles": {"visual_embedder": {"model": "siglip"}}}
    cdir = write_config(tmp_path, footage=overlay)
    doc = dict(ROLES_DOC, active_footage_profile="security")
    (cdir / "roles.yaml").write_text(yaml.safe_dump(doc))

    cfg = load_config(cdir)
    assert cfg.footage_profile == "security"
    assert cfg.role("visual_embedder").model == "siglip"
    # An explicit argument outranks the roles.yaml selection.
    assert load_config(cdir, footage_profile="generic").footage_profile == "generic"


def test_repo_config_loads_with_generic_default():
    cfg = load_config()
    assert cfg.footage_profile == "generic"
    assert "visual_embedder" in cfg.roles
