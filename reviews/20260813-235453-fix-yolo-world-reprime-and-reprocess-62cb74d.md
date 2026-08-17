# Agent review — approve

date: 2026-08-13T23:57:38.829895
range: origin/main..HEAD
branch: fix/yolo-world-reprime-and-reprocess
findings: 1

- **minor** `src/va/adapters/object_detector/yolo_world_inproc.py:43` — The rebuild-recovery premise ('a fresh model primes cleanly in the same process') is verified only against the fake; the cited incident evidence covers fresh processes, not a rebuilt model in a process whose CLIP text-encode state already triggered the device mismatch.
  - scenario: If ultralytics caches CLIP text-encoder state process-globally, the rebuilt model's set_classes raises the same mismatch, the retry propagates, and Roles 5+6 are best-effort-skipped (logged) for every mixed-profile vocabulary change — the fix's headline recovery never actually recovers on real hardware. Safe path: run one live two-vocab check (real yolo-world backend, generic then security classes in one process) and record the outcome beside the docstring's incident citation.

---

## Full review

Verification complete. Here is my review of `origin/main..HEAD` (one commit, `62cb74d`: the YOLO-World prime-once/survive-re-prime fix plus its offline regression tests).

## What I verified

- **The fix's premise holds.** `get_object_detector()` builds a fresh adapter per video while `MANAGER.get` caches one model per weights key (`src/va/runtime/manager.py:22`), so the pre-fix per-instance `self._classes = ()` guard re-primed the shared model on every video — moving the marker onto the model object (`_va_primed_classes`) puts the dedup state where the sharing actually happens. The marker is set only after `set_classes` succeeds, so no false-primed state exists on either the happy path or the recovery path (an exception in the retry propagates before line 60).
- **Recovery path is sound and correctly non-fatal.** `MANAGER.unload` exists with the assumed semantics (`manager.py:32`), the rebuilt model is reassigned to `self._model` before `predict`, and a second `set_classes` failure propagates into ingest's existing best-effort guard (`ingest.py:571–592` on main), which sets `det_ok = False`, records the failed roles, and traces the exception — degrade-and-continue, never an aborted ingest.
- **Both prior reviews of this branch are fully addressed at this HEAD**: review f99daa3's two findings (vocab-change crash made survivable via evict+rebuild; `test_changed_vocabulary_reprimes` covers the marker's comparison half) and review ceb69c1's finding (the recovery now logs a warning carrying the swallowed exception and the old→new vocab; `test_reprime_device_crash_is_recovered_by_rebuild` asserts the log).
- **Test integrity.** Test 1 genuinely fails on the pre-fix code (a fresh second adapter would call the fake's raising `set_classes` with no handler), test 2 kills a boolean-marker mutant, test 3 walks the full evict→rebuild→re-prime→log path and asserts the fresh model's calls and marker. All offline: `MANAGER` is monkeypatched and `resolve_device("cpu")` avoids torch.
- **Dissolved suspicions:** empty `classes` can't reach the adapter in this range — the only caller is ingest, and `get_ingest_classes` falls back to `DEFAULT_INGEST_CLASSES` (`registry.py:381`); reprocess on main runs no detector (that wiring is uncommitted worktree content, outside this range). A stale `self._model` in another live adapter after a rebuild has no reachable path (adapters are per-video and serial in ingest/serve/watch). No contract/schema changes, no new env vars/flags/config keys, no test deletions or weakenings, and the `need_agent_review:` subject is a provisional-phase artifact exempt from the finalized-message rule.

## One remaining finding (minor)

The recovery's load-bearing premise — "a fresh model primes cleanly *in the same process*" — is validated only against the fake. The `.va-24h` incident evidence shows fresh **processes** prime cleanly; it doesn't establish that a rebuilt model in a process whose CLIP text-encode state already triggered the mismatch primes cleanly (if ultralytics caches CLIP state process-globally rather than per-model, the rebuilt model could raise identically). The failure mode if the premise is wrong is benign (logged best-effort skip of Roles 5+6, i.e. the pre-fix behavior plus visibility), which keeps this minor — but a one-time live check (real yolo-world backend, two vocabs in one process, e.g. generic then security profile) would convert the recovery claim from plausible to measured, per the repo's determinism-is-not-correctness rule. Record the result next to the docstring's incident citation.

Verdict: **approve** — no critical or major findings.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/adapters/object_detector/yolo_world_inproc.py", "line": 43, "issue": "The rebuild-recovery premise ('a fresh model primes cleanly in the same process') is verified only against the fake; the cited incident evidence covers fresh processes, not a rebuilt model in a process whose CLIP text-encode state already triggered the device mismatch.", "scenario": "If ultralytics caches CLIP text-encoder state process-globally, the rebuilt model's set_classes raises the same mismatch, the retry propagates, and Roles 5+6 are best-effort-skipped (logged) for every mixed-profile vocabulary change — the fix's headline recovery never actually recovers on real hardware. Safe path: run one live two-vocab check (real yolo-world backend, generic then security classes in one process) and record the outcome beside the docstring's incident citation."}]}
```
