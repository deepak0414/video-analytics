# Agent review — approve

date: 2026-07-30T18:46:31.734657
range: origin/main..HEAD
branch: feature/provenance-identity
findings: 0


---

## Full review

Review complete. I read the full diff (4 commits: PROV-1 fingerprint helper, PROV-2 table + v2 migration + store, PROV-3 ingest stamping + deep-scan cache fold, PROV-4 `va stale`), then verified every suspicion against the surrounding source. All of them dissolved:

**Correctness — checked and clean.**
- The "conservative by exclusion" fingerprint claims hold against `Config.role()`'s actual merge: profile top-level keys and per-model overrides both land in `load`, so the `weights`/checkpoint/`conf`/`min_speakers` salience tests test what really happens. The excluded set (`device`, `dtype`, `residency`, credentials) matches the recorded D1 decision, and the failure direction everywhere is false-stale (safe), never missed-stale.
- The pinned-config race reasoning in `ingest.py:169` is sound: roles self-load config, so a mid-ingest `roles.yaml` edit can only make the stamp look older than the run, degrading to a false stale.
- I cross-checked every best-effort `except` block in `_ingest_impl` against the `failed` set: vlm_captioner, speech_to_text + speaker_diarizer (both the outer and inner paths), ocr, action_recognizer, object_detector/tracker (all three failure points: load, per-batch detect, track), text_embedder — all covered; scene detection and embedding are correctly critical (abort, nothing stamped).
- Deep-scan's `map_key` fingerprints the *config* reasoner while a caller-passed `reasoner` object does the work — but the only live caller (`ask.py:185`) gets its reasoner from the same config, and rule/None reasoners return an empty mapping which is never cached (`if mapping:`), so no cache poisoning is possible.
- `stale_report`'s edge cases (unstamped video → stale everywhere, non-done skipped, unknown role raises, mixed-fps → None) are each deliberate and each tested. The `va reingest --fps` flag the remedy text points at does exist (`cli.py:437`).

**Contracts, migration, docs, tests — clean.** The schema change follows the file's own 3-step rule (base DDL + idempotent `_m2` + version bump); `test_migrations.py` was generalized to be migration-count-robust, not weakened — it still asserts atomic rollback. `va remove` purges the new table (tested). COORDINATION.md logs the v2 migration, the stamping, and — notably — the one-time deep-scan cache invalidation the web agent will see on `.va-shots`. The PROV-4 scope cut (`va provenance <video>` deferred) is recorded in the plan rather than silent. `va stale` is in CLAUDE.md. Commit messages are self-sufficient prose with plan IDs trailing; HEAD's `need_agent_review:` subject is the exempt provisional form.

**Combination coverage** is reasonable for a metadata feature: the fingerprint tests exercise real-config shapes (siglip weights, whisper checkpoint, pyannote bounds, yolo conf, xclip vocab) directly via constructed `Config`s, and the stub-vs-real cache-invalidation consequence is documented for the shared real workdir.

**Gates:** full offline suite run here — 488 passed, 2 skipped, 0 failed.

No findings survive verification.

```json
{"verdict": "approve", "findings": []}
```
