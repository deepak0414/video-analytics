from va.runtime.manager import ModelManager
from va.runtime.device import resolve_device


def test_manager_builds_once_and_caches():
    mgr = ModelManager()
    calls = {"n": 0}

    def build():
        calls["n"] += 1
        return object()

    a = mgr.get("m", build)
    b = mgr.get("m", build)
    assert a is b  # same instance
    assert calls["n"] == 1  # built only once
    assert mgr.loaded() == ["m"]


def test_manager_unload():
    mgr = ModelManager()
    mgr.get("m", lambda: 123)
    assert mgr.unload("m") is True
    assert mgr.unload("m") is False  # already gone
    assert mgr.loaded() == []


def test_resolve_device_cpu_always_ok():
    assert resolve_device("cpu") == "cpu"
    # cuda resolves to cpu when torch/CUDA absent; never raises
    assert resolve_device("cuda") in ("cpu", "cuda")
