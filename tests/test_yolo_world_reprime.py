"""YOLO-World must prime its class vocabulary correctly across the fresh adapter instances that
get_object_detector() builds per video while ModelManager reuses ONE shared model. Three
behaviors, all offline (no GPU) via a fake model that stands in for the CLIP text-encode CPU/CUDA
device mismatch that only strikes on a re-prime:

  1. SAME vocabulary across instances primes exactly once (the original 231-window .va-24h gap:
     a per-instance guard re-primed the shared model every video and crashed).
  2. A CHANGED vocabulary DOES re-prime (guards against a boolean marker that would freeze the
     vocabulary and silently detect with the wrong classes).
  3. A re-prime that hits the device mismatch is RECOVERED by evicting + rebuilding the model
     (mixed footage profiles in one va serve / watch / reprocess process are a supported path).
"""
from __future__ import annotations

import pytest
from PIL import Image

from va.adapters.object_detector import yolo_world_inproc
from va.adapters.object_detector.yolo_world_inproc import YoloWorldDetector

_IMG = Image.new("RGB", (16, 16))


class _FakeResult:
    names: dict = {}
    boxes: list = []


class _FakeYolo:
    """Stand-in for the shared ultralytics model. Records each set_classes() vocabulary; when
    `raise_on_reprime`, the SECOND (and later) prime raises — mimicking the device mismatch that
    only triggers once the model has been primed/run before."""

    def __init__(self, raise_on_reprime: bool = True):
        self.calls: list[list] = []
        self.raise_on_reprime = raise_on_reprime

    def set_classes(self, classes):
        self.calls.append(list(classes))
        if self.raise_on_reprime and len(self.calls) > 1:
            raise RuntimeError(
                "Expected all tensors to be on the same device, but got index is on cpu, "
                "different from other tensors on cuda:0 (re-prime device mismatch)")

    def predict(self, images, **kw):
        return [_FakeResult() for _ in images]


class _FakeManager:
    """Mimics ModelManager: caches one model per key, and `unload` evicts it so the next `get`
    rebuilds a fresh one (a fresh model primes cleanly)."""

    def __init__(self, factory):
        self._factory = factory
        self._cache: dict = {}

    def get(self, key, build):
        if key not in self._cache:
            self._cache[key] = self._factory()
        return self._cache[key]

    def unload(self, key):
        return self._cache.pop(key, None) is not None


def _install(monkeypatch, factory):
    mgr = _FakeManager(factory)
    monkeypatch.setattr(yolo_world_inproc, "MANAGER", mgr)
    return mgr


def test_same_vocabulary_primes_once_across_instances(monkeypatch):
    """Two videos = two fresh adapters over one shared model. The second detect() with the SAME
    vocab must NOT re-prime. Fails on the pre-fix adapter (which re-primed and raised)."""
    mgr = _install(monkeypatch, lambda: _FakeYolo(raise_on_reprime=True))
    classes = ["car", "person"]
    YoloWorldDetector(load={"device": "cpu"}).detect([_IMG], classes)
    YoloWorldDetector(load={"device": "cpu"}).detect([_IMG], classes)  # different instance
    model = mgr._cache["yoloworld::yolov8s-world.pt"]
    assert model.calls == [["car", "person"]]   # primed exactly once


def test_changed_vocabulary_reprimes(monkeypatch):
    """A DIFFERENT vocabulary must re-prime with the new classes — guards the marker's comparison
    half (a boolean marker would freeze the vocabulary after the first prime)."""
    mgr = _install(monkeypatch, lambda: _FakeYolo(raise_on_reprime=False))
    det = YoloWorldDetector(load={"device": "cpu"})
    det.detect([_IMG], ["car"])
    det.detect([_IMG], ["boat"])
    model = mgr._cache["yoloworld::yolov8s-world.pt"]
    assert model.calls == [["car"], ["boat"]]   # re-primed with the new vocab


def test_reprime_device_crash_is_recovered_by_rebuild(monkeypatch, caplog):
    """A vocab change whose re-prime hits the device mismatch must evict + rebuild the model and
    prime the fresh one — not propagate, not silently skip detection — and log the rebuild."""
    import logging
    built: list = []

    def factory():
        m = _FakeYolo(raise_on_reprime=True)
        built.append(m)
        return m

    _install(monkeypatch, factory)
    det1 = YoloWorldDetector(load={"device": "cpu"})
    det1.detect([_IMG], ["car"])                 # primes model #1
    det2 = YoloWorldDetector(load={"device": "cpu"})
    with caplog.at_level(logging.WARNING):
        det2.detect([_IMG], ["boat", "truck"])   # re-prime raises -> rebuild -> prime fresh
    assert len(built) == 2                        # model was rebuilt once
    assert built[1].calls == [["boat", "truck"]]  # the fresh model was primed with the new vocab
    assert getattr(built[1], "_va_primed_classes") == ("boat", "truck")
    # the rebuild is observable, not silent (the swallowed cause is surfaced)
    assert any("re-prime failed" in r.getMessage() for r in caplog.records)


def test_reprime_rebuild_frees_old_model_before_reload(monkeypatch):
    """On the recovery rebuild the evicted model's weights must be COLLECTABLE before the replacement
    loads — else both copies are resident and a memory-pressure re-prime (the OTHER failure this
    handler catches) can re-OOM. TWO things can pin the old model: a rebuild done INSIDE the `except`
    (the live traceback's set_classes frame `self`), and logging the exception OBJECT (a retaining
    handler keeps the record, whose args hold that traceback). We install our own retaining handler
    (so this doesn't depend on pytest's log capture) and assert model #1 is actually DEAD (weakref)
    once unload evicts it — this fails on EITHER regression, where a `det._model is None` proxy check
    would not."""
    import gc
    import logging
    import weakref

    keep: list = []   # a retaining handler, like pytest caplog / a MemoryHandler / a breadcrumb log

    class _Keep(logging.Handler):
        def emit(self, record):
            keep.append(record)

    lg = logging.getLogger("va.adapters.object_detector.yolo_world_inproc")
    handler = _Keep()
    lg.addHandler(handler)
    try:
        mgr = _install(monkeypatch, lambda: _FakeYolo(raise_on_reprime=True))
        det = YoloWorldDetector(load={"device": "cpu"})
        det.detect([_IMG], ["car"])              # primes model #1
        ref1 = weakref.ref(mgr._cache["yoloworld::yolov8s-world.pt"])

        seen: dict = {}
        real_unload = mgr.unload

        def spy_unload(key):
            alive = real_unload(key)             # evict model #1 from the cache FIRST...
            gc.collect()
            seen["old_alive"] = ref1() is not None   # ...then: still pinned (traceback), or freed?
            return alive

        monkeypatch.setattr(mgr, "unload", spy_unload)
        det.detect([_IMG], ["boat"])             # re-prime raises -> recovery rebuild
        assert keep, "precondition: the re-prime warning was logged and retained"
        assert seen["old_alive"] is False         # collected before reload (fails in-except OR log(e))
    finally:
        lg.removeHandler(handler)


def test_detect_returns_one_list_per_image(monkeypatch):
    """Sanity: detect() returns one detection list per input image."""
    _install(monkeypatch, lambda: _FakeYolo(raise_on_reprime=False))
    out = YoloWorldDetector(load={"device": "cpu"}).detect([_IMG, _IMG], ["car"])
    assert isinstance(out, list) and len(out) == 2 and all(isinstance(x, list) for x in out)
