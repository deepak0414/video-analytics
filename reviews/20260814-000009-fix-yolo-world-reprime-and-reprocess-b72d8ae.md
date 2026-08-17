# Agent review — approve

date: 2026-08-14T00:02:25.413629
range: origin/main..HEAD
branch: fix/yolo-world-reprime-and-reprocess
findings: 0


---

## Full review

## Review of `origin/main..HEAD` (one commit, `b72d8ae`)

**Verdict: approve — no findings.** The commit primes the shared YOLO-World model's vocabulary once per process (marker on the model object instead of the per-adapter instance) and makes a vocabulary change survivable by evicting and rebuilding the model when re-priming hits the CLIP device mismatch, plus four offline regression tests.

This branch has three prior reviews. The last one (`62cb74d`, approve) carried a single minor finding: the rebuild-recovery premise was verified only against the fake, not on real hardware in one process. The only delta between that reviewed commit and this HEAD is a docstring paragraph recording exactly the requested live validation (2026-08-13, real backend, mismatch reproduced, recovery confirmed with detections before and after two rebuilds). That closes the finding as its safe path specified; the two earlier reviews' findings were already resolved at `62cb74d`.

What I independently verified at this HEAD:

- **Marker semantics are sound.** `_va_primed_classes` is set only after `set_classes` succeeds (both happy path and post-rebuild), so no false-primed state exists; a second `set_classes` failure propagates out of `_prime` with the marker unset.
- **A propagated failure cannot abort ingest.** The only committed caller wraps `detector.detect` in the existing best-effort guard (`src/va/pipeline/ingest.py:571`–592), which sets `det_ok = False`, records the failed roles, and traces — degrade-and-continue, per the best-effort rule. The `reprocess.py` detector wiring is uncommitted worktree content, outside this range.
- **Concurrency and stale-reference paths are unreachable.** The serve ingest queue is single-worker (`src/va/web/jobs.py:1`), adapters are built per video and used serially, and no query path touches the detector — so no interleaved detects race the unlocked marker check, and a stale `self._model` in another live adapter has no reachable trigger.
- **Empty vocabulary can't reach the adapter**: a falsy `classes:` falls back to `DEFAULT_INGEST_CLASSES` (`src/va/registry.py:381`).
- **Test integrity holds.** Test 1 genuinely fails on the pre-fix code (the fake raises on the second prime, which the old per-instance guard would trigger with no handler); test 2 kills a boolean-marker mutant; test 3 walks the full evict→rebuild→re-prime path and asserts both the fresh model's state and the warning log. `MANAGER` is monkeypatched and `device: cpu` keeps everything offline.
- **No contract, schema, config, or doc-surface changes** — `ModelManager.unload` pre-existed with the assumed semantics (`src/va/runtime/manager.py:32`); no new env vars, flags, or config keys. The `need_agent_review:` subject is a provisional-phase artifact, exempt from the finalized-message rule.

The one residual cost worth knowing (not a finding — the docstring states and justifies it): every vocabulary change in a long-lived mixed-profile process is now a full weights reload, an accepted trade recorded in the code.

```json
{"verdict": "approve", "findings": []}
```
