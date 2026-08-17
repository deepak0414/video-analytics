# Agent review — approve

date: 2026-08-13T23:53:50.662157
range: origin/main..HEAD
branch: fix/yolo-world-reprime-and-reprocess
findings: 1

- **minor** `src/va/adapters/object_detector/yolo_world_inproc.py:47` — The evict-and-rebuild recovery is silent — the bare `except Exception` discards the original error and the full weights reload emits no log, so rebuild churn and unexpected failure causes are invisible.
  - scenario: A reprocess or watch pass over videos alternating footage profiles (generic vs security vocab) triggers the device-mismatch and a full model rebuild on EVERY vocab change — a per-video weights-reload performance cliff with zero observable signal; likewise a non-device-mismatch set_classes failure (OOM, corrupted weights) is swallowed identically, hiding its real cause. Safe path: log.warning in the except handler including the swallowed exception and the old/new vocabularies before rebuilding.

---

## Full review

The staged-execution/residency interaction is safe: the marker lives on the model object, so an evicted model rebuilds fresh and re-primes naturally. My review is complete.

## Review summary

**Scope:** `origin/main..HEAD` = one commit (`ceb69c1`), changing `src/va/adapters/object_detector/yolo_world_inproc.py` plus a new offline test file `tests/test_yolo_world_reprime.py`. The uncommitted `ingest.py`/`reprocess.py` edits and untracked test files in the worktree are outside the instructed range and were not reviewed.

**What I verified:**

- **The fix's premise holds.** `get_object_detector()` builds a fresh adapter per video while `MANAGER.get` caches one model per weights key, so the old per-instance `self._classes` guard re-primed the shared model every video — the on-model `_va_primed_classes` marker correctly moves the dedup state to where the sharing happens. The marker is set only after `set_classes` succeeds, so there is no false-primed state.
- **The recovery path is sound.** `MANAGER.unload` exists with the expected semantics (`runtime/manager.py:32`), the rebuilt model is reassigned to `self._model` before `predict`, and a second failure propagates into ingest's existing best-effort guard (`ingest.py:571`) — degrade-and-continue, never an aborted ingest, per repo convention. Its premise ("a fresh model primes cleanly") is empirically grounded: every fresh process's first prime worked throughout the `.va-24h` incident.
- **Both findings from the prior review of this branch** (reviews/20260813-234330, commit f99daa3) **are addressed**: the vocab-change crash is now survivable via evict+rebuild, and `test_changed_vocabulary_reprimes` covers the marker's comparison half against a boolean-marker regression.
- **Test integrity:** the regression test genuinely fails on the pre-fix code (fresh second adapter → re-prime → the fake raises, and the old code had no handler), the fake ModelManager mirrors the real `get`/`unload` contract, and the tests are truly offline (`resolve_device("cpu")` short-circuits before importing torch; `MANAGER` is monkeypatched). A pytest run was live in another session, so per the 2026-08-04 lesson I verified by inspection rather than launching a competing run.
- **Dissolved suspicions:** an empty `classes` list would newly call `set_classes([])`, but `get_ingest_classes` (`registry.py:381`) guarantees a non-empty vocab via `or DEFAULT_INGEST_CLASSES`; stale `self._model` after another instance's rebuild has no reachable path (serve's job queue is a single-threaded `SerialQueue`, watch/reprocess are serial, adapters are per-video with a fixed vocab); the residency-knob `unload` between roles composes correctly with the marker (eviction rebuilds fresh and re-primes).
- No contract/schema changes, no new env vars/flags/config keys needing docs, no test deletions/weakenings, and the `need_agent_review:` subject is a provisional-phase artifact exempt from the finalized-message rule.

**One minor finding:** the recovery path is completely silent. The `except Exception` swallows the original error (which may not be the device mismatch — OOM or corrupted weights would look identical) and the rebuild — a full weights reload — leaves no trace. In a reprocess or watch pass over videos that alternate footage profiles, *every* vocab change triggers a silent full rebuild (the just-rebuilt model crashes again on the next re-prime), a per-video performance cliff nobody can see. Safe path: `log.warning` in the except block with the swallowed exception and the old→new vocab, so rebuild churn and non-device-mismatch failures are observable in traces.

Verdict: **approve** (no critical or major findings).

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/adapters/object_detector/yolo_world_inproc.py", "line": 47, "issue": "The evict-and-rebuild recovery is silent — the bare `except Exception` discards the original error and the full weights reload emits no log, so rebuild churn and unexpected failure causes are invisible.", "scenario": "A reprocess or watch pass over videos alternating footage profiles (generic vs security vocab) triggers the device-mismatch and a full model rebuild on EVERY vocab change — a per-video weights-reload performance cliff with zero observable signal; likewise a non-device-mismatch set_classes failure (OOM, corrupted weights) is swallowed identically, hiding its real cause. Safe path: log.warning in the except handler including the swallowed exception and the old/new vocabularies before rebuilding."}]}
```
