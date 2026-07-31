"""Provenance identity fingerprints (WS-1 §6-b, PROV-1): a stable, output-only
fingerprint per role — model / weights / scored-vocab change it; device / dtype don't.
"""
from va.configuration import Config
from va.provenance import role_fingerprint


def _cfg(roles, profile=None):
    return Config(active_profile="p", profile=profile or {}, roles=roles)


def test_fingerprint_is_stable_and_shaped():
    cfg = _cfg({"visual_embedder": {"backend": "inproc", "model": "siglip"}})
    a = role_fingerprint("visual_embedder", cfg)
    b = role_fingerprint("visual_embedder", cfg)
    assert a == b
    assert a["model"] == "siglip"
    assert len(a["fingerprint"]) == 16


def test_model_change_changes_fingerprint():
    c1 = _cfg({"visual_embedder": {"model": "siglip"}})
    c2 = _cfg({"visual_embedder": {"model": "hash"}})
    assert (role_fingerprint("visual_embedder", c1)["fingerprint"]
            != role_fingerprint("visual_embedder", c2)["fingerprint"])


def test_weights_override_changes_fingerprint():
    base = {"visual_embedder": {"model": "siglip"}}
    c1 = _cfg(base, profile={"models": {"siglip": {"weights": "checkpoint-A"}}})
    c2 = _cfg(base, profile={"models": {"siglip": {"weights": "checkpoint-B"}}})
    assert (role_fingerprint("visual_embedder", c1)["fingerprint"]
            != role_fingerprint("visual_embedder", c2)["fingerprint"])


def test_device_and_dtype_do_not_change_fingerprint():
    # speed/placement params must NOT mark a corpus stale
    base = {"visual_embedder": {"model": "siglip"}}
    c1 = _cfg(base, profile={"device": "cuda", "dtype": "bf16"})
    c2 = _cfg(base, profile={"device": "cpu", "dtype": "fp32", "batch_size": 8})
    assert (role_fingerprint("visual_embedder", c1)["fingerprint"]
            == role_fingerprint("visual_embedder", c2)["fingerprint"])


def test_action_vocabulary_changes_fingerprint():
    c1 = _cfg({"action_recognizer": {"model": "xclip", "actions": ["driving", "walking"]}})
    c2 = _cfg({"action_recognizer": {"model": "xclip", "actions": ["driving", "dancing"]}})
    assert (role_fingerprint("action_recognizer", c1)["fingerprint"]
            != role_fingerprint("action_recognizer", c2)["fingerprint"])


def test_action_vocabulary_order_independent():
    c1 = _cfg({"action_recognizer": {"model": "xclip", "actions": ["driving", "walking"]}})
    c2 = _cfg({"action_recognizer": {"model": "xclip", "actions": ["walking", "driving"]}})
    assert (role_fingerprint("action_recognizer", c1)["fingerprint"]
            == role_fingerprint("action_recognizer", c2)["fingerprint"])


def test_detector_classes_change_fingerprint():
    c1 = _cfg({"object_detector": {"model": "yolo-world", "classes": ["car", "person"]}})
    c2 = _cfg({"object_detector": {"model": "yolo-world", "classes": ["car", "dog"]}})
    assert (role_fingerprint("object_detector", c1)["fingerprint"]
            != role_fingerprint("object_detector", c2)["fingerprint"])


def test_unconfigured_role_is_unknown_not_error():
    fp = role_fingerprint("speech_to_text", _cfg({}))
    assert fp["model"] == "unknown"
    assert len(fp["fingerprint"]) == 16


def test_checkpoint_in_load_model_changes_fingerprint():
    # whisper/pyannote select their checkpoint via models.<name>.model, NOT `weights`;
    # the queued whisper base->large-v3 upgrade must change the Role-8 fingerprint.
    base = {"speech_to_text": {"model": "whisper"}}
    c1 = _cfg(base, profile={"models": {"whisper": {"model": "base"}}})
    c2 = _cfg(base, profile={"models": {"whisper": {"model": "large-v3"}}})
    a, b = role_fingerprint("speech_to_text", c1), role_fingerprint("speech_to_text", c2)
    assert a["model"] == b["model"] == "whisper"       # role id unchanged...
    assert a["fingerprint"] != b["fingerprint"]         # ...but the checkpoint moved


def test_output_gating_threshold_changes_fingerprint():
    # a detection-gating threshold changes WHICH rows are stored -> must be salient
    base = {"object_detector": {"model": "yolo-world"}}
    c1 = _cfg(base, profile={"models": {"yolo-world": {"conf": 0.25}}})
    c2 = _cfg(base, profile={"models": {"yolo-world": {"conf": 0.5}}})
    assert (role_fingerprint("object_detector", c1)["fingerprint"]
            != role_fingerprint("object_detector", c2)["fingerprint"])


def test_speaker_bounds_change_fingerprint():
    # pyannote min/max_speakers gate diarization output -> salient by default (no enum)
    base = {"speaker_diarizer": {"model": "pyannote"}}
    c1 = _cfg(base, profile={"models": {"pyannote": {"min_speakers": 1}}})
    c2 = _cfg(base, profile={"models": {"pyannote": {"min_speakers": 2}}})
    assert (role_fingerprint("speaker_diarizer", c1)["fingerprint"]
            != role_fingerprint("speaker_diarizer", c2)["fingerprint"])


def test_any_non_speed_load_key_is_salient_by_default():
    # conservative-by-exclusion: an arbitrary knob (a captioner max_new_tokens that
    # truncates stored captions) changes the fingerprint with NO per-adapter enumeration.
    base = {"vlm_captioner": {"model": "qwen2.5-vl-7b"}}
    c1 = _cfg(base, profile={"models": {"qwen2.5-vl-7b": {"max_new_tokens": 96}}})
    c2 = _cfg(base, profile={"models": {"qwen2.5-vl-7b": {"max_new_tokens": 32}}})
    assert (role_fingerprint("vlm_captioner", c1)["fingerprint"]
            != role_fingerprint("vlm_captioner", c2)["fingerprint"])


def test_residency_is_speed_only_not_salient():
    # a profile-global infra key (keep vs unload-after-use) must NOT mark a corpus stale
    base = {"visual_embedder": {"model": "siglip"}}
    c1 = _cfg(base, profile={"residency": "keep"})
    c2 = _cfg(base, profile={"residency": "unload-after-use"})
    assert (role_fingerprint("visual_embedder", c1)["fingerprint"]
            == role_fingerprint("visual_embedder", c2)["fingerprint"])


def test_role_level_key_is_salient():
    # a roles.yaml ROLE-LEVEL knob (e.g. a future scene_detector threshold) is salient too,
    # not just profile `load` keys
    c1 = _cfg({"scene_detector": {"model": "pyscenedetect", "threshold": 27.0}})
    c2 = _cfg({"scene_detector": {"model": "pyscenedetect", "threshold": 40.0}})
    assert (role_fingerprint("scene_detector", c1)["fingerprint"]
            != role_fingerprint("scene_detector", c2)["fingerprint"])


def test_credentials_are_not_salient():
    # rotating an HF token must NOT mark the corpus stale (nor land in persisted provenance)
    base = {"speaker_diarizer": {"model": "pyannote"}}
    c1 = _cfg(base, profile={"models": {"pyannote": {"hf_token": "tok-old"}}})
    c2 = _cfg(base, profile={"models": {"pyannote": {"hf_token": "tok-new"}}})
    assert (role_fingerprint("speaker_diarizer", c1)["fingerprint"]
            == role_fingerprint("speaker_diarizer", c2)["fingerprint"])


def test_unset_vocab_folds_in_defaults():
    # an unset vocab hashes the DEFAULT_INGEST_* list, so a default-vocab edit is caught:
    # unset == explicitly-set-to-the-default
    from va.roles.action_recognizer import DEFAULT_INGEST_ACTIONS

    unset = _cfg({"action_recognizer": {"model": "xclip"}})
    explicit = _cfg({"action_recognizer": {"model": "xclip",
                                           "actions": list(DEFAULT_INGEST_ACTIONS)}})
    assert (role_fingerprint("action_recognizer", unset)["fingerprint"]
            == role_fingerprint("action_recognizer", explicit)["fingerprint"])
