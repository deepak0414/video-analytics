"""WS4.e — staged model execution: the hardware profile's `residency:` knob
(previously documented-but-unconsumed) now gates ModelManager unloads at role
group boundaries in ingest. `keep` (the shipped default) must be a byte-for-byte
no-op; `unload-after-use` clears the cache between groups so no two heavy role
groups are ever co-resident (the §8.1 silent-starvation fix)."""
import shutil
from pathlib import Path

import yaml

from va.runtime.manager import MANAGER

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"


def _config_dir(tmp_path, residency):
    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    prof = cdir / "profiles" / "dgx-spark.yaml"
    doc = yaml.safe_load(prof.read_text())
    doc["residency"] = residency
    prof.write_text(yaml.safe_dump(doc))
    return cdir


def _spy_clear(monkeypatch):
    calls = []
    orig = MANAGER.clear

    def spy():
        calls.append(1)
        orig()

    monkeypatch.setattr(MANAGER, "clear", spy)
    return calls


def _ingest(tmp_path, monkeypatch, residency, workdir):
    from va.media.synth import write_color_video
    from va.pipeline.ingest import ingest

    monkeypatch.setenv("VA_CONFIG_DIR", str(_config_dir(tmp_path / residency,
                                                        residency)))
    clip = write_color_video(tmp_path / f"clip-{residency}.mp4",
                             [("red", (220, 30, 30), 3.0)], fps=10)
    return ingest(str(clip), workdir=str(tmp_path / workdir), fps=1.0)


def test_keep_never_unloads(tmp_path, monkeypatch):
    calls = _spy_clear(monkeypatch)
    result = _ingest(tmp_path, monkeypatch, "keep", ".va-keep")
    assert result.video.ingest_status.value == "done"
    assert calls == []


def test_unload_after_use_stages_between_groups(tmp_path, monkeypatch):
    calls = _spy_clear(monkeypatch)
    result = _ingest(tmp_path, monkeypatch, "unload-after-use", ".va-stage")
    assert result.video.ingest_status.value == "done"
    # one clear per group boundary (captioner / speech / ocr / actions /
    # embed+detect+track) — at least those five fire on a full stub ingest
    assert len(calls) >= 5


def test_staging_does_not_change_ingest_output(tmp_path, monkeypatch):
    keep = _ingest(tmp_path, monkeypatch, "keep", ".va-a")
    stage = _ingest(tmp_path, monkeypatch, "unload-after-use", ".va-b")
    for field in ("frames_indexed", "segments", "captioned_segments",
                  "transcript_lines", "detections", "tracks", "ocr_lines",
                  "action_events", "text_vectors"):
        assert getattr(keep, field) == getattr(stage, field), field


def test_boundary_actually_frees_the_captioner_weights(tmp_path, monkeypatch):
    """Round-2 review: the `del captioner` local-release is the round-1 major's
    fix, and stub adapters never register weights — without this probe the del
    lines are deletable as dead code with the suite green. The check must run
    MID-INGEST (a later role's getter), because locals die at function exit
    regardless."""
    import gc
    import weakref

    import va.pipeline.ingest as ing
    from va.media.synth import write_color_video
    from va.pipeline.ingest import ingest
    from va.runtime.manager import MANAGER

    class FakeWeights:
        pass

    captioner_refs = []
    observed_dead_after_boundary = []

    class FakeCaptioner:
        def __init__(self):
            self._model = MANAGER.get("fake-captioner-weights", FakeWeights)
            captioner_refs.append(weakref.ref(self._model))

        def caption(self, image):
            return "a caption"

    class ProbeOcr:
        # Constructed AFTER the captioner-group boundary: by now BOTH the
        # MANAGER cache entry and the ingest-local `captioner` must be gone,
        # or the weights are still resident into later groups.
        def __init__(self):
            gc.collect()
            observed_dead_after_boundary.append(
                captioner_refs[0]() is None if captioner_refs else None)

        def read(self, video_path):
            return []

    monkeypatch.setattr(ing, "get_vlm_captioner", lambda cfg: FakeCaptioner())
    monkeypatch.setattr(ing, "get_ocr_reader", lambda cfg: ProbeOcr())
    monkeypatch.setenv("VA_CONFIG_DIR", str(_config_dir(tmp_path,
                                                        "unload-after-use")))
    clip = write_color_video(tmp_path / "clip.mp4",
                             [("red", (220, 30, 30), 3.0)], fps=10)
    result = ingest(str(clip), workdir=str(tmp_path / ".va-free"), fps=1.0)
    assert result.video.ingest_status.value == "done"
    assert result.captioned_segments >= 1          # the fake captioner ran
    assert observed_dead_after_boundary == [True]  # weights freed mid-ingest


def test_boundary_actually_frees_embedder_and_detector(tmp_path, monkeypatch):
    """Symmetric probe for the embed/detect group's local release (round-3
    review): the probe runs from the text-index step, which executes after
    that group's boundary."""
    import gc
    import weakref

    import numpy as np

    import va.pipeline.ingest as ing
    from va.media.synth import write_color_video
    from va.pipeline.ingest import ingest
    from va.runtime.manager import MANAGER

    class FakeWeights:
        pass

    refs = []
    observed = []

    class FakeEmbedder:
        def __init__(self):
            self._model = MANAGER.get("fake-embedder-weights", FakeWeights)
            refs.append(weakref.ref(self._model))

        def embed_image(self, images):
            vecs = np.ones((len(images), 8), dtype=np.float32)
            return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)

    def probe_index_text(video_id, video_dir, db, cfg=None):
        gc.collect()
        observed.append(all(r() is None for r in refs) if refs else None)
        return 0

    monkeypatch.setattr(ing, "get_visual_embedder", lambda cfg: FakeEmbedder())
    monkeypatch.setattr(ing, "index_text", probe_index_text)
    monkeypatch.setenv("VA_CONFIG_DIR", str(_config_dir(tmp_path,
                                                        "unload-after-use")))
    clip = write_color_video(tmp_path / "clip.mp4",
                             [("red", (220, 30, 30), 3.0)], fps=10)
    result = ingest(str(clip), workdir=str(tmp_path / ".va-free2"), fps=1.0)
    assert result.video.ingest_status.value == "done"
    assert result.frames_indexed >= 1        # the fake embedder ran
    assert observed == [True]                # weights freed before text index


def test_residency_knob_reaches_config(tmp_path, monkeypatch):
    from va.configuration import load_config

    monkeypatch.setenv(
        "VA_CONFIG_DIR", str(_config_dir(tmp_path, "unload-after-use")))
    assert load_config().profile["residency"] == "unload-after-use"


def test_unknown_residency_fails_at_load(tmp_path, monkeypatch):
    # An ignored typo (underscores) would silently keep every model resident —
    # reproducing the starvation this knob exists to fix. Fail at load, like
    # the footage-profile knobs do.
    import pytest

    from va.configuration import load_config

    monkeypatch.setenv(
        "VA_CONFIG_DIR", str(_config_dir(tmp_path, "unload_after_use")))
    with pytest.raises(ValueError, match="residency"):
        load_config()


def test_failed_ingest_still_stages(tmp_path, monkeypatch):
    # A mid-ingest failure must not leave every model resident into the next
    # clip of a single-process batch (review round 1).
    import pytest

    from va.pipeline.ingest import ingest

    calls = _spy_clear(monkeypatch)
    monkeypatch.setenv("VA_CONFIG_DIR", str(_config_dir(tmp_path,
                                                        "unload-after-use")))
    with pytest.raises(Exception):
        ingest(str(tmp_path / "does-not-exist.mp4"),
               workdir=str(tmp_path / ".va-fail"), fps=1.0)
    # resolve fails before any model loads here, so clear may legitimately not
    # fire — use a mid-pipeline failure instead: a real clip with a poisoned
    # scene detector.
    from va.media.synth import write_color_video

    clip = write_color_video(tmp_path / "clip.mp4",
                             [("red", (220, 30, 30), 3.0)], fps=10)
    import va.pipeline.ingest as ing

    def boom(cfg):
        raise RuntimeError("scene detector exploded")

    monkeypatch.setattr(ing, "get_scene_detector", boom)
    calls.clear()
    with pytest.raises(RuntimeError):
        ingest(str(clip), workdir=str(tmp_path / ".va-fail2"), fps=1.0)
    assert len(calls) >= 1   # the failure path staged
