# Agent review — request_changes

date: 2026-08-06T21:53:39.300051
range: origin/main..HEAD
branch: loop/ws6a-durable-jobs
findings: 2

- **major** `src/va/web/jobs.py:171` — Auto-resume re-runs ingest() against partial on-disk state, but the frame and appearance vector shards load-and-append on re-run (NumpyFlatVectorStore._load + add), so the advertised exactly-once resume can silently duplicate every frame embedding.
  - scenario: Server is killed after store.persist() (ingest.py:569) but before set_status(done) (ingest.py:678) — e.g. during the real-model text-index pass; on restart IngestQueue._resume re-enqueues the job, ingest reloads the persisted vectors.npz and appends a second full copy of all frame vectors; va query and web search then return duplicate hits with no warning. Fix: build the per-video shards to a temp path and swap_shard them in (the index_text pattern), or start them empty when the catalog row is not done.
- **minor** `tests/test_jobs_durable.py:52` — The kill-mid-job oracle fabricates only the jobs-table 'running' row into an empty workdir — no partial catalog row or persisted shard — so the plan's 'idempotency asserted' is verified only at ingest-call-count level, not against the partial state a real mid-job kill leaves.
  - scenario: The current test stays green while a resume after a real kill (partial shard persisted, catalog row 'processing') doubles the vector shard; extend the test to pre-seed that state and assert vector count/search hits are not duplicated — it would currently fail, exposing the major finding.

---

## Full review

Review complete. The change is well-structured (schema v7 migration is done by the book, COORDINATION.md is logged with the cross-boundary flag, the API surface really is unchanged, and the degraded-mode design is sound), but the headline guarantee has a hole.

## Findings

**1. Major — `src/va/web/jobs.py:171` (root cause `src/va/pipeline/ingest.py:498`): auto-resume re-runs `ingest()` against partial on-disk state, and the per-video vector shards are append-only on re-run, so a resumed job can silently duplicate every frame vector.**

The exactly-once claim rests on "ingest() is the idempotency point." That holds for the catalog row (`done` dedups) and for the role tables (`replace_segments`/`replace_transcripts`/etc. and `index_text`'s temp+`swap_shard` are all rebuild-style). It does **not** hold for the two numpy shards: `NumpyFlatVectorStore.__init__` calls `_load()` (numpy_flat.py:40) and `add()` appends to whatever is already on disk (numpy_flat.py:100-106). Concrete failure: the server is killed after `store.persist()` (ingest.py:569) but before `set_status(done)` (ingest.py:678) — the window spans text indexing (a real-model embed pass) and provenance, so it's real on the run-siglip/run-claude configs. On restart the new `IngestQueue._resume()` automatically re-enqueues the job, ingest re-runs, loads the persisted shard, and appends a second full copy of every frame embedding — `va query` and the web search then return duplicate hits with no warning anywhere. The appearance shard (`_capture_appearance`, ingest.py:124) has the same append behavior with a slightly wider window (it persists before the frame shard), accumulating entries whose `appearance_ref`s dangle after `replace_tracks` reassigns track ids. Pre-WS6.a this corruption needed a human to manually resubmit a crashed ingest; this change makes it happen automatically on every restart, which is exactly the advertised path. Safe path: make the shard write idempotent for a not-`done` video — build the frame/appearance shards to a temp path and `swap_shard` them in (the pattern `index_text` already uses), or have ingest start these per-video shards empty when the catalog row is not `done`. Alternatively resume via the reingest/purge path instead of plain `ingest()`.

**2. Minor — `tests/test_jobs_durable.py:52`: the "kill-mid-job" oracle fabricates only the jobs-table artifact, not the mid-job state, so the plan's "idempotency asserted" is asserted against an empty workdir.**

WS6.a's Done-when is "a test kills a worker mid-job and a restarted worker resumes it exactly once (idempotency asserted)." The test writes a `running` row directly via `JobStore` into a workdir where ingest never started — no catalog row, no partial shard — so it verifies resume-runs-once at the call-count level but cannot detect data-level non-idempotency. A worker genuinely killed mid-job always leaves partial catalog/shard state, which is precisely what finding 1 exploits. Safe path: extend the test to simulate a crash *after* the shard persist (pre-seed the video dir with a persisted shard + a `processing` catalog row, then resume and assert the vector count and search hits are not doubled). Written that way, the test fails on the current code and pins the fix for finding 1.

Things I checked that dissolved: the monkeypatch in the resume test does intercept (`_process` imports `ingest` at call time and the signature matches); `AskQueue._resume` runs synchronously before the lifespan serves requests, so the pending→fail_pending sequence has no race in practice; the degraded-mode `_persist` correctly catches a `_store()` that raises; mixed timestamp formats can't occur because `record()` always supplies `_now()`; schema versioning, migration idempotence, and the `MIGRATIONS`-count assert are all consistent; CLAUDE.md and COORDINATION.md document the new surface; the provisional `need_agent_review:` subject is exempt from the clarity rule.

```json
{"verdict": "request_changes", "findings": [
  {"severity": "major", "file": "src/va/web/jobs.py", "line": 171,
   "issue": "Auto-resume re-runs ingest() against partial on-disk state, but the frame and appearance vector shards load-and-append on re-run (NumpyFlatVectorStore._load + add), so the advertised exactly-once resume can silently duplicate every frame embedding.",
   "scenario": "Server is killed after store.persist() (ingest.py:569) but before set_status(done) (ingest.py:678) — e.g. during the real-model text-index pass; on restart IngestQueue._resume re-enqueues the job, ingest reloads the persisted vectors.npz and appends a second full copy of all frame vectors; va query and web search then return duplicate hits with no warning. Fix: build the per-video shards to a temp path and swap_shard them in (the index_text pattern), or start them empty when the catalog row is not done."},
  {"severity": "minor", "file": "tests/test_jobs_durable.py", "line": 52,
   "issue": "The kill-mid-job oracle fabricates only the jobs-table 'running' row into an empty workdir — no partial catalog row or persisted shard — so the plan's 'idempotency asserted' is verified only at ingest-call-count level, not against the partial state a real mid-job kill leaves.",
   "scenario": "The current test stays green while a resume after a real kill (partial shard persisted, catalog row 'processing') doubles the vector shard; extend the test to pre-seed that state and assert vector count/search hits are not duplicated — it would currently fail, exposing the major finding."}
]}
```
