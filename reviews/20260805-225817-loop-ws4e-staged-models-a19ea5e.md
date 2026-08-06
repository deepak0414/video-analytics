# Agent review — approve

date: 2026-08-05T23:01:35.920604
range: origin/main..HEAD
branch: loop/ws4e-staged-models
findings: 0


---

## Full review

A pytest run is already live (the committing session's gate suite), so per the repo's lesson I won't launch another — execution evidence lives in that channel; my verification of the new test is static.

Final static check on the new probe's monkeypatch targets: `ingest.py` imports both `get_visual_embedder` (line 36) and `index_text` (line 21) into its own module namespace, and `_ingest_impl` calls them as bare names — so patching `ing.get_visual_embedder` / `ing.index_text` intercepts the real call sites. The probe fires from `index_text`, which runs after the line-641 boundary (`del embedder, detector; _stage_models(cfg)` at `ingest.py:638-641`), so the weakref can only be dead if both the MANAGER clear and the local release happened — exactly the invariant round 3 asked to pin.

## Review summary — round 4, WS4.e staged model execution

This is the fourth review of this commit (amended each round). The only delta since the round-3 approval (`1f3efea`) is the new `test_boundary_actually_frees_embedder_and_detector` probe — a 46-line test-only addition that closes round 3's single minor finding. I re-verified the full range fresh rather than only the delta.

**Round-3 minor: closed.** The new probe replicates the captioner-group pattern for the embed/detect group: a fake visual embedder registers sentinel weights via `MANAGER.get` and binds them as `self._model`; a monkeypatched `index_text` (which executes after the group's boundary) asserts via weakref + `gc.collect()` that the weights are dead. Deleting the `embedder = None; detector = None; del embedder, detector` block at `ingest.py:638-640` — round 3's exact scenario — now fails this test. The probe is deterministic (single ref, CPython refcounting plus an explicit collect; no other retainer of the fake survives the `del` — `_capture_appearance` takes `embedder` only as a parameter, and the vector store holds numpy arrays, not the adapter). The detector local isn't independently probed (the stub detector registers nothing in MANAGER), but it shares the same `del` statement, so the plausible refactor hazard — removing the block as dead code — is pinned.

**All earlier-round findings remain fixed:** the round-1 major (locals pinning weights past `MANAGER.clear()`) is fixed at both sites and now both are test-pinned; unknown `residency` values fail at `load_config` (`configuration.py:208-213`, underscore-typo test present); boundaries exist after the text-index group and on the failure path (the swallowed pre-`cfg` `NameError` there is benign — no model loads before `cfg` exists). I swept the remaining role groups again for pinning locals: Whisper, pyannote, RapidOCR, X-CLIP, and the tracker are all invoked as temporaries that die at expression end.

**Also clean:** `keep` is a genuine no-op (guard short-circuits; pinned by `test_keep_never_unloads`); output invariance across residency modes is pinned field-by-field; all four config-dir profile copies carry identical comments with the shipped value unchanged (`keep`); the WS4.e "Done when" is satisfied with measured ground-truth numbers (22/22 vs the 1/22 baseline) logged in `architecture-evolution-loop.md`; COORDINATION.md logs the contract including the local-release rule for future roles; the knob is documented where operators edit it (profile yamls) plus the golden README batch-ingest note; the commit subject is a provisional `need_agent_review:` and exempt from the clarity rule. No tests were deleted or weakened anywhere in the range — the range is purely additive on the test side.

No new findings.

```json
{"verdict": "approve", "findings": []}
```
