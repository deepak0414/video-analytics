"""Config -> client factory (Principle P2).

Callers ask the registry for a role client and get back something satisfying the
role Protocol, regardless of whether it's in-process, http, or cloud. Today only
Role 2 (visual_embedder) is wired; others slot in the same way.
"""
from __future__ import annotations

from typing import Optional

from va.configuration import Config, load_config
from va.roles.action_recognizer import ActionRecognizer
from va.roles.diarizer import SpeakerDiarizer
from va.roles.object_detector import ObjectDetector
from va.roles.object_tracker import ObjectTracker
from va.roles.ocr import OcrReader
from va.roles.reasoner import Reasoner
from va.roles.reranker import Reranker
from va.roles.scene_detector import SceneDetector
from va.roles.speech_to_text import SpeechToText
from va.roles.text_embedder import TextEmbedder
from va.roles.visual_embedder import VisualEmbedder
from va.roles.vlm_captioner import VLMCaptioner
from va.roles.vlm_verifier import VlmVerifier


def get_visual_embedder(cfg: Optional[Config] = None) -> VisualEmbedder:
    cfg = cfg or load_config()
    rc = cfg.role("visual_embedder")

    if rc.backend == "inproc":
        if rc.model == "hash":
            from va.adapters.visual_embedder.hash_inproc import HashEmbedder

            return HashEmbedder()
        if rc.model == "siglip":
            from va.adapters.visual_embedder.siglip_inproc import SiglipEmbedder

            return SiglipEmbedder(rc.load)
        raise ValueError(f"unknown visual_embedder model: {rc.model!r}")

    # backend == 'http' / 'cloud' would construct the corresponding client here.
    raise NotImplementedError(f"visual_embedder backend not yet wired: {rc.backend!r}")


def get_text_embedder(cfg: Optional[Config] = None) -> TextEmbedder:
    """Retrieval Layer (SR.1): semantic text-text embedder. Default = hash stub."""
    cfg = cfg or load_config()
    try:
        rc = cfg.role("text_embedder")
        backend, model, load = rc.backend, rc.model, rc.load
    except KeyError:
        backend, model, load = "inproc", "hash", {}

    if backend == "inproc":
        if model in (None, "hash"):
            from va.adapters.text_embedder.hash_inproc import HashTextEmbedder

            return HashTextEmbedder()
        # any other model id = a transformers (BGE/E5) HF weights name
        from va.adapters.text_embedder.transformers_inproc import HFTextEmbedder

        return HFTextEmbedder({**load, "model": model})

    raise NotImplementedError(f"text_embedder backend not yet wired: {backend!r}")


def get_reranker(cfg: Optional[Config] = None) -> Reranker:
    """Retrieval Layer (SR.3): cross-encoder reranker. Default = word-overlap stub."""
    cfg = cfg or load_config()
    try:
        rc = cfg.role("reranker")
        backend, model, load = rc.backend, rc.model, rc.load
    except KeyError:
        backend, model, load = "inproc", "word-overlap", {}

    if backend == "inproc":
        if model in (None, "word-overlap"):
            from va.adapters.reranker.wordoverlap_inproc import WordOverlapReranker

            return WordOverlapReranker()
        # any other model id = a cross-encoder HF weights name
        from va.adapters.reranker.cross_encoder_inproc import CrossEncoderReranker

        return CrossEncoderReranker({**load, "model": model})

    raise NotImplementedError(f"reranker backend not yet wired: {backend!r}")


def get_vlm_verifier(cfg: Optional[Config] = None) -> VlmVerifier:
    """Retrieval Layer (SR.6): query-time VLM verifier. Default = no-op passthrough
    (so the stub pipeline is unchanged). qwen* shares the Role-4 Qwen bundle."""
    cfg = cfg or load_config()
    try:
        rc = cfg.role("vlm_verifier")
        backend, model, load = rc.backend, rc.model, rc.load
    except KeyError:
        backend, model, load = "inproc", "passthrough", {}

    if backend == "inproc":
        if model in (None, "passthrough"):
            from va.adapters.vlm_verifier.passthrough_inproc import PassthroughVerifier

            return PassthroughVerifier()
        if model and model.startswith("qwen"):
            from va.adapters.vlm_verifier.qwen_inproc import QwenVerifier

            return QwenVerifier({**load, "model": model})
        raise ValueError(f"unknown vlm_verifier model: {model!r}")

    raise NotImplementedError(f"vlm_verifier backend not yet wired: {backend!r}")


def get_scene_detector(cfg: Optional[Config] = None) -> SceneDetector:
    cfg = cfg or load_config()
    # Scene detection is optional infra; default to the histogram backend even if
    # a config omits it, so ingest works without explicit configuration.
    try:
        rc = cfg.role("scene_detector")
        backend, model = rc.backend, rc.model
    except KeyError:
        backend, model = "inproc", "histogram"

    if backend == "inproc":
        if model in (None, "histogram"):
            from va.adapters.scene_detector.histogram_inproc import HistogramSceneDetector

            return HistogramSceneDetector()
        if model == "pyscenedetect":
            from va.adapters.scene_detector.pyscenedetect_inproc import PySceneDetectDetector

            return PySceneDetectDetector()
        raise ValueError(f"unknown scene_detector model: {model!r}")

    raise NotImplementedError(f"scene_detector backend not yet wired: {backend!r}")


def get_speech_to_text(cfg: Optional[Config] = None) -> SpeechToText:
    cfg = cfg or load_config()
    try:
        rc = cfg.role("speech_to_text")
        backend, model, load = rc.backend, rc.model, rc.load
    except KeyError:
        backend, model, load = "inproc", "sidecar", {}

    if backend == "inproc":
        if model in (None, "sidecar"):
            from va.adapters.speech_to_text.sidecar_inproc import SidecarSTT

            return SidecarSTT()
        if model == "whisper":
            from va.adapters.speech_to_text.whisper_inproc import WhisperSTT

            return WhisperSTT(load)
        raise ValueError(f"unknown speech_to_text model: {model!r}")

    raise NotImplementedError(f"speech_to_text backend not yet wired: {backend!r}")


def get_vlm_captioner(cfg: Optional[Config] = None) -> VLMCaptioner:
    cfg = cfg or load_config()
    try:
        rc = cfg.role("vlm_captioner")
        backend, model, load = rc.backend, rc.model, rc.load
    except KeyError:
        backend, model, load = "inproc", "color", {}

    if backend == "inproc":
        if model in (None, "color"):
            from va.adapters.vlm_captioner.color_inproc import ColorCaptioner

            return ColorCaptioner()
        if model and model.startswith("qwen"):
            from va.adapters.vlm_captioner.qwen_inproc import QwenCaptioner

            return QwenCaptioner({**load, "model": model})
        raise ValueError(f"unknown vlm_captioner model: {model!r}")

    raise NotImplementedError(f"vlm_captioner backend not yet wired: {backend!r}")


def get_object_detector(cfg: Optional[Config] = None) -> ObjectDetector:
    cfg = cfg or load_config()
    try:
        rc = cfg.role("object_detector")
        backend, model, load = rc.backend, rc.model, rc.load
    except KeyError:
        backend, model, load = "inproc", "color", {}

    if backend == "inproc":
        if model in (None, "color"):
            from va.adapters.object_detector.color_inproc import ColorDetector

            return ColorDetector()
        if model == "yolo-world":
            from va.adapters.object_detector.yolo_world_inproc import YoloWorldDetector

            return YoloWorldDetector(load)
        raise ValueError(f"unknown object_detector model: {model!r}")

    raise NotImplementedError(f"object_detector backend not yet wired: {backend!r}")


def get_object_tracker(cfg: Optional[Config] = None) -> ObjectTracker:
    cfg = cfg or load_config()
    try:
        rc = cfg.role("object_tracker")
        backend, model, load = rc.backend, rc.model, rc.load
    except KeyError:
        backend, model, load = "inproc", "iou", {}

    if backend == "inproc":
        if model in (None, "iou"):
            from va.adapters.object_tracker.iou_inproc import IouTracker

            return IouTracker()
        if model == "bytetrack":
            from va.adapters.object_tracker.bytetrack_inproc import ByteTrackTracker

            return ByteTrackTracker(load)
        raise ValueError(f"unknown object_tracker model: {model!r}")

    raise NotImplementedError(f"object_tracker backend not yet wired: {backend!r}")


def get_speaker_diarizer(cfg: Optional[Config] = None) -> SpeakerDiarizer:
    cfg = cfg or load_config()
    try:
        rc = cfg.role("speaker_diarizer")
        backend, model, load = rc.backend, rc.model, rc.load
    except KeyError:
        backend, model, load = "inproc", "sidecar", {}

    if backend == "inproc":
        if model in (None, "sidecar"):
            from va.adapters.speaker_diarizer.sidecar_inproc import SidecarDiarizer

            return SidecarDiarizer()
        if model == "pyannote":
            from va.adapters.speaker_diarizer.pyannote_inproc import PyannoteDiarizer

            return PyannoteDiarizer(load)
        raise ValueError(f"unknown speaker_diarizer model: {model!r}")

    raise NotImplementedError(f"speaker_diarizer backend not yet wired: {backend!r}")


def get_action_recognizer(cfg: Optional[Config] = None) -> ActionRecognizer:
    cfg = cfg or load_config()
    try:
        rc = cfg.role("action_recognizer")
        backend, model, load = rc.backend, rc.model, rc.load
    except KeyError:
        backend, model, load = "inproc", "motion", {}

    if backend == "inproc":
        if model in (None, "motion"):
            from va.adapters.action_recognizer.motion_inproc import MotionRecognizer

            return MotionRecognizer()
        if model == "xclip":
            from va.adapters.action_recognizer.xclip_inproc import XClipRecognizer

            return XClipRecognizer(load)
        raise ValueError(f"unknown action_recognizer model: {model!r}")

    raise NotImplementedError(f"action_recognizer backend not yet wired: {backend!r}")


def get_ocr_reader(cfg: Optional[Config] = None) -> OcrReader:
    cfg = cfg or load_config()
    try:
        rc = cfg.role("ocr")
        backend, model, load = rc.backend, rc.model, rc.load
    except KeyError:
        backend, model, load = "inproc", "sidecar", {}

    if backend == "inproc":
        if model in (None, "sidecar"):
            from va.adapters.ocr.sidecar_inproc import SidecarOCR

            return SidecarOCR()
        if model == "rapidocr":
            from va.adapters.ocr.rapidocr_inproc import RapidOCRReader

            return RapidOCRReader(load)
        raise ValueError(f"unknown ocr model: {model!r}")

    raise NotImplementedError(f"ocr backend not yet wired: {backend!r}")


def get_reasoner(cfg: Optional[Config] = None) -> Reasoner:
    cfg = cfg or load_config()
    try:
        rc = cfg.role("reasoner")
        backend, model, load = rc.backend, rc.model, rc.load
    except KeyError:
        backend, model, load = "inproc", "rule", {}

    if backend in ("inproc", "cloud"):
        if model in (None, "rule"):
            from va.adapters.reasoner.rule_inproc import RuleReasoner

            return RuleReasoner()
        if model and model.startswith("qwen"):
            from va.adapters.reasoner.qwen_inproc import QwenReasoner

            return QwenReasoner({**load, "model": model})
        if model == "claude-code":
            from va.adapters.reasoner.claude_cli_inproc import ClaudeCliReasoner

            return ClaudeCliReasoner(load)
        if model == "claude-api":
            from va.adapters.reasoner.claude_api_inproc import ClaudeApiReasoner

            return ClaudeApiReasoner(load)  # placeholder: raises with guidance
        raise ValueError(f"unknown reasoner model: {model!r}")

    raise NotImplementedError(f"reasoner backend not yet wired: {backend!r}")


def get_ingest_classes(cfg: Optional[Config] = None) -> list[str]:
    """Class vocabulary detected at ingest (roles.yaml `classes:` override)."""
    from va.roles.object_detector import DEFAULT_INGEST_CLASSES

    cfg = cfg or load_config()
    spec = cfg.roles.get("object_detector") or {}
    return list(spec.get("classes") or DEFAULT_INGEST_CLASSES)


def get_ingest_actions(cfg: Optional[Config] = None) -> list[str]:
    """Action vocabulary scored at ingest (roles.yaml `actions:` override)."""
    from va.roles.action_recognizer import DEFAULT_INGEST_ACTIONS

    cfg = cfg or load_config()
    spec = cfg.roles.get("action_recognizer") or {}
    return list(spec.get("actions") or DEFAULT_INGEST_ACTIONS)
