from va.configuration import load_config


def test_loads_and_merges_profile_into_role():
    cfg = load_config()
    assert cfg.active_profile == "dgx-spark"
    rc = cfg.role("visual_embedder")
    assert rc.backend == "inproc"
    assert rc.model == "hash"
    # hash model overrides device to cpu in the profile
    assert rc.load.get("device") == "cpu"
    # profile-level default (residency) is folded in too
    assert rc.load.get("residency") == "keep"


def test_unknown_role_raises():
    cfg = load_config()
    try:
        cfg.role("nope")
    except KeyError:
        return
    raise AssertionError("expected KeyError")
