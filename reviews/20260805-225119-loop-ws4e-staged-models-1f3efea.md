# Agent review — approve

date: 2026-08-05T22:55:44.653944
range: origin/main..HEAD
branch: loop/ws4e-staged-models
findings: 1

- **minor** `src/va/pipeline/ingest.py:638` — The new weakref probe pins only the captioner group's local release — the embed/detect group's `del embedder, detector` (lines 638-640) has no covering test, so deleting those lines keeps the full suite green while its unload becomes cosmetic.
  - scenario: A future refactor removes the `embedder = None; detector = None; del embedder, detector` block as apparent dead code; every test passes. Under residency: unload-after-use, SigLIP and YOLO then stay pinned through the text-index group and into the next clip of a single-process batch, co-resident with that clip's captioner — partially recreating the measured 1/22 starvation while the trace still reports models unloaded. Fix: replicate the captioner probe for this group — a fake visual embedder registering a MANAGER.get sentinel, weakref asserted dead in a monkeypatched text-embedder getter after the line-641 boundary.

---

## Full review

Review complete. This is round 3 on the WS4.e staged-model-execution commit. The only delta since the round-2 approval (386d282) is the new `test_boundary_actually_frees_the_captioner_weights` probe, added to close round 2's single minor. I verified the whole range fresh and the new test in depth; the dispute ledger has no entries touching this work.

**What I verified and found clean:**

- **The new probe test is a real regression test, not decoration.** The weakref to the fake captioner's `MANAGER.get`-registered weights can only be dead at OCR-getter time if *both* halves of the fix hold: the group boundary cleared the MANAGER cache *and* the `finally` released the ingest-local `captioner`. Removing either the `del` lines or the boundary leaves the weakref alive and fails `observed_dead_after_boundary == [True]`. The probe deliberately fires mid-ingest (locals die at function exit regardless), which is exactly right. It would fail on the pre-fix code, satisfying the CLAUDE.md regression-test lesson.
- Round-1/round-2 fixes remain in place and correct: `finally`-based local release is safe when `get_vlm_captioner` itself raises (both names assigned `None` before `del`); unknown `residency` values fail at `load_config` with a test for the underscore typo; the failure path stages best-effort (the swallowed pre-`cfg` `NameError` is benign — no models load before `cfg` exists); the failure-path test poisons the scene detector mid-pipeline rather than relying on the early resolve failure.
- `keep` is a genuine no-op pinned by `test_keep_never_unloads`; output invariance across residency modes is pinned field-by-field; all four config-dir profile copies carry identical updated comments with values unchanged; COORDINATION.md logged the contract; the WS4.e "Done when" is satisfied with measured 22/22-vs-1/22 ground-truth numbers in the loop doc; commit subject is a provisional `need_agent_review:` and exempt from the clarity rule.
- I could not execute the suite here (sandbox denies pytest, and the committing session already has the full round-3 suite live — its green/red gates the amend, so execution evidence exists in that channel).

**One residual finding (minor).** The new probe pins only the *captioner* group's local release. The embed/detect group's release — `embedder = None; detector = None; del embedder, detector` at `src/va/pipeline/ingest.py:638-640` — is still covered by no test: delete those three lines and the entire suite stays green, while under `residency: unload-after-use` SigLIP and YOLO stay pinned through the text-index group and, in a single-process batch driver, into the next clip's captioner group — partially recreating the co-residency the knob exists to prevent. The test's docstring ("without this probe the del lines are deletable as dead code") overstates its coverage: it makes one of the two del sites non-deletable. Safe path: reuse the exact probe pattern — a fake visual embedder that registers a sentinel via `MANAGER.get`, with the weakref checked in a monkeypatched text-embedder path (e.g. patch `index_text` or the text-embedder getter) after the line-641 boundary. Round 2 rated the wholly-untested version of this a minor; the half-closed version stays a minor.

Verdict: **approve** — no critical or major findings.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/ingest.py", "line": 638, "issue": "The new weakref probe pins only the captioner group's local release — the embed/detect group's `del embedder, detector` (lines 638-640) has no covering test, so deleting those lines keeps the full suite green while its unload becomes cosmetic.", "scenario": "A future refactor removes the `embedder = None; detector = None; del embedder, detector` block as apparent dead code; every test passes. Under residency: unload-after-use, SigLIP and YOLO then stay pinned through the text-index group and into the next clip of a single-process batch, co-resident with that clip's captioner — partially recreating the measured 1/22 starvation while the trace still reports models unloaded. Fix: replicate the captioner probe for this group — a fake visual embedder registering a MANAGER.get sentinel, weakref asserted dead in a monkeypatched text-embedder getter after the line-641 boundary."}]}
```
