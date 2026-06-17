"""SR.6 — VLM verifier role + the verify_visual_hits / verify_object_presence
integration. Offline/deterministic: the no-op stub plus a fake verifier whose
verdict depends only on the claim string (never loads a model)."""
import json

from PIL import Image

from va.adapters.speech_to_text.sidecar_inproc import sidecar_path
from va.adapters.vlm_verifier.passthrough_inproc import PassthroughVerifier
from va.adapters.vlm_verifier.qwen_inproc import _NEGATIVE, _POSITIVE
from va.media.synth import write_color_video
from va.pipeline.ingest import ingest
from va.pipeline.query import query
from va.pipeline.verify import (
    verify_object_presence,
    verify_scene_presence,
    verify_visual_hits,
)
from va.registry import get_vlm_verifier
from va.roles.vlm_verifier import Verdict


class _Fake:
    """Enabled verifier whose verdict depends only on the claim (image ignored),
    so tests are deterministic without a VLM."""
    enabled = True

    def __init__(self, reject_if=None, accept_if=None):
        self.reject_if, self.accept_if = reject_if, accept_if

    def verify(self, image, claim):
        c = claim.lower()
        if self.accept_if is not None:
            return Verdict("yes" if self.accept_if in c else "no", claim)
        return Verdict("no" if self.reject_if in c else "yes", claim)


def test_default_verifier_is_passthrough_noop():
    v = get_vlm_verifier()
    assert isinstance(v, PassthroughVerifier)
    assert v.enabled is False
    assert v.verify(Image.new("RGB", (8, 8)), "anything").accept is True


def test_parse_is_three_way():
    # clear leading negative -> "no" (drop on rerank, absent on presence)
    assert _NEGATIVE.match("NO. The car is red.") and not _POSITIVE.match("NO. ...")
    assert _NEGATIVE.match("Not a snake")
    # clear leading positive -> "yes"
    assert _POSITIVE.match("YES. Red sports car.") and not _NEGATIVE.match("YES. ...")
    # hedge -> neither -> "unsure" (kept on rerank, NOT counted on presence)
    assert not _NEGATIVE.match("It is a snake") and not _POSITIVE.match("It is a snake")
    assert not _NEGATIVE.match("Unclear, possibly") and not _POSITIVE.match("Unclear, possibly")


def _ingest_red(tmp_path):
    video = write_color_video(tmp_path / "clip.mp4", [("red", (220, 30, 30), 4.0)], fps=10)
    sidecar_path(str(video)).write_text(json.dumps({"lines": []}))
    wd = str(tmp_path / ".va")
    ingest(str(video), workdir=wd, fps=1.0)
    return wd


def test_verify_visual_passthrough_returns_input_unchanged(tmp_path):
    wd = _ingest_red(tmp_path)
    hits = query("red", workdir=wd, k=10)
    assert hits
    # default (stub) verifier is no-op -> identical list, no frame I/O
    assert verify_visual_hits(hits, "red", workdir=wd) == hits


def test_verify_visual_drops_only_above_floor_on_reject(tmp_path):
    wd = _ingest_red(tmp_path)
    hits = query("red", workdir=wd, k=10)
    floor = 0.05
    # a verifier that rejects everything: every ABOVE-floor hit must be removed,
    # below-floor hits pass through untouched (conservative budget).
    kept = verify_visual_hits(hits, "red", workdir=wd, floor=floor,
                              verifier=_Fake(reject_if="red"))
    assert all(h.score < floor for h in kept)
    assert len(kept) < len(hits)            # something above-floor existed and was dropped


def test_verify_visual_claim_sensitive(tmp_path):
    wd = _ingest_red(tmp_path)
    hits = query("red", workdir=wd, k=10)
    f = _Fake(reject_if="blue")             # rejects only claims mentioning blue
    assert verify_visual_hits(hits, "a red car", workdir=wd, verifier=f) == hits
    dropped = verify_visual_hits(hits, "a blue car", workdir=wd, floor=0.0, verifier=f)
    assert len(dropped) < len(hits)


def test_verify_object_presence_counts(tmp_path):
    wd = _ingest_red(tmp_path)
    vid = query("red", workdir=wd, k=1)[0].video_id
    assert verify_object_presence("snake", vid, workdir=wd) == 0          # stub -> 0
    n = verify_object_presence("snake", vid, workdir=wd, verifier=_Fake(accept_if="snake"))
    assert n >= 1                                                          # fake says yes


def test_verify_scene_presence_window(tmp_path):
    wd = _ingest_red(tmp_path)
    vid = query("red", workdir=wd, k=1)[0].video_id
    assert verify_scene_presence("a wedding", vid, workdir=wd) == 0       # stub -> 0
    # windowed sampling + a fake that confirms only "red" claims
    n = verify_scene_presence("a red scene", vid, workdir=wd, window=[0.0, 3.0],
                              verifier=_Fake(accept_if="red"))
    assert n >= 1


def test_retrieve_visual_verification_flag_noop_under_stub(tmp_path):
    from va.contracts.query_plan import QueryPlan
    from va.pipeline.retrieval import retrieve

    wd = _ingest_red(tmp_path)
    # flag set, but the stub verifier is a no-op -> visual evidence still present
    ev = retrieve(QueryPlan(query="red", needs_visual_verification=True), workdir=wd, k=5)
    assert any(i.modality == "visual" for i in ev.items)
