# Agent review — request_changes

date: 2026-08-05T22:06:33.880659
range: origin/main..HEAD
branch: loop/ws4e-staged-models
findings: 3

- **major** `src/va/pipeline/ingest.py:395` — The captioner-group unload is defeated: the `captioner` local (line 384) holds the adapter whose __init__ bound self._model, so MANAGER.clear() removes only the cache entry while the ~15GB Qwen weights stay strongly referenced for the entire rest of ingest; same pattern for embedder/detector past line 629.
  - scenario: On a memory-constrained profile (the '24GB profile' the knob comment targets) with residency: unload-after-use, Qwen2.5-VL-7B remains resident through the SigLIP+YOLO group, reproducing the exact silent YOLO starvation WS4.e was built to fix, while the trace falsely reports 'unloaded 1 model(s)'. Fix: del captioner (and keyframes) before the boundary; del detector/embedder before line 629; re-measure memory at boundaries and correct the COORDINATION.md claim.
- **minor** `src/va/pipeline/ingest.py:152` — An unrecognized residency value silently falls through to keep-resident behavior with no warning, contrary to the repo's fail-at-load convention for unknown config knob values.
  - scenario: An operator sets `residency: unload_after_use` (underscore typo) in the hardware profile for a 22-clip batch; staging never engages, YOLO starves to 1/22 detections again, and nothing anywhere signals the knob was ignored. Fix: validate the value at config load or warn on unknown values in _stage_models.
- **minor** `src/va/pipeline/ingest.py:636` — No stage boundary after the SR.2 text-index group and none on the exception path, so under unload-after-use the text embedder (bge-m3, ~2GB) — and after a failed ingest, every model — stays resident into the next clip of a single-process batch.
  - scenario: A single-process batch ingest (the feature's stated target) runs clip after clip with bge-m3 permanently co-resident with each clip's captioner group; one mid-ingest failure leaves all models loaded for every subsequent clip. Fix: add a final _stage_models after index_text or run staging in a finally block.

---

## Full review

Review complete. The feature's mechanism has a real hole that the stub tests structurally cannot catch.

## Findings

**1. Major — the captioner unload is defeated by a surviving local reference (`src/va/pipeline/ingest.py:395`).**
Every real adapter binds its model in `__init__` (`self._model = MANAGER.get(...)` — e.g. `qwen_inproc.py:31-33`). `captioner` at `ingest.py:384` is a function-scope local in `_ingest_impl`, and Python locals live until function exit. So `_stage_models(cfg)` at line 395 pops the MANAGER cache entry and runs `gc.collect()`/`empty_cache()`, but the Qwen2.5-VL-7B weights (~15 GB fp16) stay strongly referenced by `captioner._model` through the *entire rest of ingest* — the speech, OCR, action, and SigLIP+YOLO groups. The docstring's claim ("drop the finished role group's models") and the COORDINATION.md contract entry are false for the heaviest model in the pipeline. The measured 22/22 is genuine but partially accidental: the temp-constructed adapters (Whisper, X-CLIP, OCR, tracker) really do free, and Qwen+SigLIP+YOLO happen to co-fit on the Spark's unified memory — but the "24GB profile would use unload-after-use" scenario the knob comment itself advertises would starve exactly as before, silently, with the trace even reporting "unloaded 1 model(s)". Same pattern at line 629: `embedder`/`detector` outlive that boundary into `index_text` (smaller window — they die at function return). Safe path: `del captioner` (and the `keyframes` list) before the line-395 boundary, and `del detector, embedder` before line 629; then re-verify on the real batch that memory actually drops (e.g. `torch.cuda.memory_allocated` traced at each boundary), and correct the COORDINATION entry if the mechanism was only partially responsible for the 22/22.

**2. Minor — an unrecognized `residency:` value silently behaves as `keep` (`src/va/pipeline/ingest.py:152`).**
`(cfg.profile.get("residency") or "keep") == "unload-after-use"` means a typo (`unload_after_use`, `Unload-After-Use`) no-ops with zero signal — the operator believes staging is on and gets the 1/22 starvation back. The repo's own convention for footage knobs is fail-at-load on unknown values. Safe path: validate `residency ∈ {keep, unload-after-use}` at config load (or at minimum warn in `_stage_models`).

**3. Minor — no boundary after the text-index group and none on the failure path (`src/va/pipeline/ingest.py:636`).**
Under `unload-after-use` in a single-process batch (the feature's stated target), the SR.2 text embedder (`BAAI/bge-m3` in run-siglip, ~2 GB) loads after the last `_stage_models` and stays MANAGER-resident into the next clip's captioner group; likewise an ingest that raises mid-way skips all remaining boundaries and leaves every model resident for the next batch item. Safe path: one more `_stage_models(cfg)` after `index_text`, or move staging into a `finally`.

Checked and clean: `keep` no-op is genuinely byte-identical (guard short-circuits before touching MANAGER); provenance correctly excludes `residency` as a speed-only knob (`test_provenance.py:111` pins it); WS4.e "Done when" (22/22 vs 1/22 baseline, numbers logged in the loop doc) is satisfied; COORDINATION.md is logged; the knob is documented in all four profile copies + golden README; the commit subject is a provisional `need_agent_review:` and exempt from the clarity rule.

```json
{"verdict": "request_changes", "findings": [
{"severity": "major", "file": "src/va/pipeline/ingest.py", "line": 395, "issue": "The captioner-group unload is defeated: the `captioner` local (line 384) holds the adapter whose __init__ bound self._model, so MANAGER.clear() removes only the cache entry while the ~15GB Qwen weights stay strongly referenced for the entire rest of ingest; same pattern for embedder/detector past line 629.", "scenario": "On a memory-constrained profile (the '24GB profile' the knob comment targets) with residency: unload-after-use, Qwen2.5-VL-7B remains resident through the SigLIP+YOLO group, reproducing the exact silent YOLO starvation WS4.e was built to fix, while the trace falsely reports 'unloaded 1 model(s)'. Fix: del captioner (and keyframes) before the boundary; del detector/embedder before line 629; re-measure memory at boundaries and correct the COORDINATION.md claim."},
{"severity": "minor", "file": "src/va/pipeline/ingest.py", "line": 152, "issue": "An unrecognized residency value silently falls through to keep-resident behavior with no warning, contrary to the repo's fail-at-load convention for unknown config knob values.", "scenario": "An operator sets `residency: unload_after_use` (underscore typo) in the hardware profile for a 22-clip batch; staging never engages, YOLO starves to 1/22 detections again, and nothing anywhere signals the knob was ignored. Fix: validate the value at config load or warn on unknown values in _stage_models."},
{"severity": "minor", "file": "src/va/pipeline/ingest.py", "line": 636, "issue": "No stage boundary after the SR.2 text-index group and none on the exception path, so under unload-after-use the text embedder (bge-m3, ~2GB) — and after a failed ingest, every model — stays resident into the next clip of a single-process batch.", "scenario": "A single-process batch ingest (the feature's stated target) runs clip after clip with bge-m3 permanently co-resident with each clip's captioner group; one mid-ingest failure leaves all models loaded for every subsequent clip. Fix: add a final _stage_models after index_text or run staging in a finally block."}
]}
```
