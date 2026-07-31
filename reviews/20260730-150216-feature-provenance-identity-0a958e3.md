# Agent review — request_changes

date: 2026-07-30T15:05:13.150705
range: origin/main..HEAD
branch: feature/provenance-identity
findings: 3

- **major** `src/va/provenance.py:30` — _SALIENT_LOAD_KEYS omits the load['model'] checkpoint key, so whisper base→large-v3 (and pyannote pipeline changes) do not change the role fingerprint
  - scenario: Profiles pin models.whisper.model: base (COORDINATION.md 2026-06-10, large-v3 upgrade queued); after upgrading, role_fingerprint('speech_to_text') is unchanged because rc.model is 'whisper' and load['model'] is never hashed, so PROV-4 'va stale' reports all base-transcribed videos as current and the batch reprocess (B) skips them
- **minor** `src/va/provenance.py:13` — Docstring claims all non-salient load params affect only speed/placement, but conf (yolo_world_inproc.py:24), min_confidence (xclip_inproc.py:39, rapidocr_inproc.py:37), ocr lang/fps, and text_embedder pooling change stored rows/vectors
  - scenario: A user tightens object_detector conf 0.25→0.5 and reprocesses one video; the fingerprint is identical, so provenance can never distinguish the two detection sets and va stale reports the corpus uniform when it is not
- **minor** `tests/test_provenance.py:24` — No test covers a load['model'] checkpoint change (the knob real profiles actually use for whisper), so the suite stays green on the major finding
  - scenario: The salient-key fix regresses or is never applied; tests pass while Role-8 provenance identity is insensitive to the transcription model size

---

## Full review

Test execution was blocked in this session, so this review is static-only (the new tests read as sound against the `Config` API — I verified the `profile.models.<name>` fold path they rely on).

## Review summary

The change (PROV-1) adds `src/va/provenance.py::role_fingerprint(role, cfg)` — a per-role `{model, fingerprint}` identity over model id + `weights` override + detector/action vocab — plus 8 offline tests and a plan-status update. The structure is good: vocab is order-normalized, device/dtype exclusion is tested, the unconfigured-role path degrades instead of raising, and the plan doc was updated in the same commit. But I found one major gap that defeats the feature's purpose for exactly the model upgrade already queued in this repo.

### Major: the checkpoint-in-`load["model"]` pattern escapes the fingerprint

`_SALIENT_LOAD_KEYS = ("weights",)` assumes a checkpoint override always arrives under the `weights` key. Several adapters select their checkpoint from `load["model"]` instead:

- `src/va/adapters/speech_to_text/whisper_inproc.py:21` — `self.model_size = load.get("model", "large-v3")`. Every real profile pins `models.whisper.model: base` (the interim relief logged in COORDINATION.md 2026-06-10, with the large-v3 upgrade explicitly queued for when the download completes). Flipping `base` → `large-v3` radically changes stored transcripts, yet `role_fingerprint("speech_to_text")` stays identical: `rc.model` is the string `"whisper"` and `load["model"]` is never hashed. PROV-4's `va stale` would then report every base-transcribed video as current after the upgrade — the one concrete model bump this corpus is already waiting on.
- Same pattern: `speaker_diarizer/pyannote_inproc.py:31` (`load.get("model", _DEFAULT_MODEL)` selects the pyannote pipeline), and `reranker/cross_encoder_inproc.py:24` / `text_embedder/transformers_inproc.py:30` accept `load.get("model")` as an alternate weights key.

Safe path: add `"model"` to `_SALIENT_LOAD_KEYS` (one-line, safe — no current profile sets a top-level `model` key, so nothing gets spuriously stale) and add a test that a `models.whisper.model` change alters the Role-8 fingerprint.

### Minor: the "everything else is speed/placement" claim is false for several load keys

The docstring (src/va/provenance.py:13-16) asserts all non-salient load params change only speed or placement. Adapters contradict this: `object_detector.conf` (yolo_world_inproc.py:24, gates which detections are stored), `action_recognizer.min_confidence` (xclip_inproc.py:39, gates stored events), `ocr.lang`/`fps`/`min_confidence` (rapidocr_inproc.py:35-37), `text_embedder.pooling` (transformers_inproc.py:33, changes the vectors), pyannote's `num_speakers` bounds. D1 locked exclusion of device/dtype/batch only; these are output-affecting and silently uncovered. This is D1's "fingerprint proves too coarse" revisit trigger firing before PROV-2 lands — safe path: extend the salient set (or per-role salient key lists), or record the exclusion decision explicitly in D1 so it's a documented gap rather than an accidental one.

### Minor: no test for the `load["model"]` checkpoint dimension

`tests/test_provenance.py` covers weights, vocab, and device invariance, but not the one config knob every real profile actually uses to pick a checkpoint (`models.whisper.model`) — the test suite would stay green on the major finding above. Extend alongside the fix.

Checks that came back clean: no contract surface touched (COORDINATION.md entry correctly deferred to PROV-2's table), no schema change, no new env/CLI surface needing docs, plan status updated in-commit, vocab derivation comes from config/data rather than hardcoded content, the `need_agent_review:` subject is exempt from the plain-description rule, and cross-role fingerprint collisions (e.g. captioner/reasoner sharing qwen2.5-vl-7b) are harmless because PROV-2 keys on `(video_id, role)`.

Verdict: request_changes (one major finding).

```json
{"verdict": "request_changes", "findings": [
{"severity": "major", "file": "src/va/provenance.py", "line": 30, "issue": "_SALIENT_LOAD_KEYS omits the load['model'] checkpoint key, so whisper base→large-v3 (and pyannote pipeline changes) do not change the role fingerprint", "scenario": "Profiles pin models.whisper.model: base (COORDINATION.md 2026-06-10, large-v3 upgrade queued); after upgrading, role_fingerprint('speech_to_text') is unchanged because rc.model is 'whisper' and load['model'] is never hashed, so PROV-4 'va stale' reports all base-transcribed videos as current and the batch reprocess (B) skips them"},
{"severity": "minor", "file": "src/va/provenance.py", "line": 13, "issue": "Docstring claims all non-salient load params affect only speed/placement, but conf (yolo_world_inproc.py:24), min_confidence (xclip_inproc.py:39, rapidocr_inproc.py:37), ocr lang/fps, and text_embedder pooling change stored rows/vectors", "scenario": "A user tightens object_detector conf 0.25→0.5 and reprocesses one video; the fingerprint is identical, so provenance can never distinguish the two detection sets and va stale reports the corpus uniform when it is not"},
{"severity": "minor", "file": "tests/test_provenance.py", "line": 24, "issue": "No test covers a load['model'] checkpoint change (the knob real profiles actually use for whisper), so the suite stays green on the major finding", "scenario": "The salient-key fix regresses or is never applied; tests pass while Role-8 provenance identity is insensitive to the transcription model size"}
]}
```
