# Agent review — request_changes

date: 2026-07-31T11:57:04.363635
range: origin/main..HEAD
branch: feature/batch-reprocess
findings: 1

- **major** `src/va/cli.py:284` — va reprocess --all-stale executes a corpus-wide destructive overwrite with no confirmation — the only guard is the printed config header, weaker than the multi-line warning on the read-only va stale for the same VA_CONFIG_DIR foot-gun.
  - scenario: User with a real SigLIP corpus forgets VA_CONFIG_DIR and runs `va --workdir .va-shots reprocess --all-stale`: every video reads stale under the stub config, both wired reprocessors overwrite every vectors/text_vectors shard with 64-dim hash-stub embeddings and restamp provenance as current, in one command with no pause between printing the plan and executing it; recovery is a full real-model re-embed. Safe path: require --yes/interactive confirmation on execute, and/or refuse stub-over-real-tagged-shard overwrites behind an explicit override.

---

## Full review

# Review: `origin/main..HEAD` (feature/batch-reprocess, 3 commits)

**Scope reviewed:** `va reprocess` selection front-end (RPRC-3a), executor + `text_embedder` wiring (RPRC-1a), `visual_embedder` wiring (RPRC-1b), the shard write-ordering change in `NumpyFlatVectorStore.persist`, and the accompanying tests/docs. Offline suite verified green: **512 passed, 2 skipped**.

## What I verified and did not report

I chased several suspicions to ground; all dissolved:

- `execute_reprocess` reading `prev[0]["fps"]` is safe — `role_provenance` is a `(video_id, role)` upsert, so `pv.get(vid, role)` returns at most one row.
- The batch-pinned-config restamp is correct in both drift directions (a mid-batch config edit degrades to a safe false-stale, never a missed stale), and the test genuinely pins it via monkeypatch that binds at call time.
- The `.json`-before-`.npz` write/swap ordering claims hold against `sharded.py`'s mtime-keyed cache; the remaining torn-pair window during the visual two-file swap is transient, self-healing, and honestly disclosed to the web agent in COORDINATION.md rather than papered over.
- `reindex_visual`'s payloads match ingest's exactly; the `vectors_rebuild` temp shard can't match the reader glob `*/vectors.npz`; the printed `va reingest ... --fps` pointer matches a real flag.
- Failure-path tests are real tests: they assert the old shard's bytes survive a failed rebuild and that provenance is never restamped on failure — they would fail against the pre-fix code.
- Plan conformance is clean: RPRC-3's XOR scope, rows-then-provenance ordering, and resumability are all implemented; RPRC-1c/RPRC-2 deferrals are recorded in the plan, and deferring dependency invalidation is actually safe here since neither wired role has dependents in the R1→R4/5/6/7, R5→R6, R8→R9 graph.
- Docs parity is complete (CLAUDE.md command, COORDINATION entries incl. the concurrency heads-up, plan status), and the two finalized commit subjects are plainly descriptive.

## Finding

**MAJOR — `src/va/cli.py:255-284` — corpus-wide destructive execution is guarded only by an informational header line, weaker than the guard on its read-only sibling.**

`_cmd_reprocess` prints `_active_config_line()` and the plan, then — unless `--dry-run` — immediately calls `execute_reprocess` with no confirmation step. There is no pause between displaying the plan and executing it, so in execute mode the plan printout cannot function as a review step.

The failure scenario is the repo's own documented #1 trip-up, and the one `_active_config_line`'s docstring itself names: a user with a real corpus (`.va-shots`, SigLIP-embedded) forgets `VA_CONFIG_DIR` and runs `.venv/bin/va --workdir .va-shots reprocess --all-stale`. Under the stub config every video reads stale for every role; the executor then overwrites every video's `vectors` and `text_vectors` shards with 64-dim hash-stub embeddings and restamps their provenance as current-under-stub — one keystroke, whole corpus. Recovery is a full real-model re-embed (hours of GPU). The damage is *detectable* afterward (shard tags make TAG-3 skip the shards under the real config, and `va stale` under the real config re-flags them), but that only converts silent corruption into expensive rework. `va stale` — read-only — carries a multi-line "REINGEST UNDER THIS SAME CONFIG" warning for the same foot-gun (`cli.py:240-247`); the command that actually writes carries less protection than the one that only reports. The mitigation was designed for a report and inherited unchanged by a write path.

**Safe path:** add a confirmation gate on the execute path (e.g. `--yes` to proceed non-interactively, otherwise prompt after printing the plan), and/or refuse to overwrite a shard whose recorded tag is a real model when the active embedder is the stub, behind an explicit override. Either mechanism is hardcoded *structure*, not content, so it's within the repo's conventions; the shard-tag comparison derives the refusal from data already recorded for exactly this purpose.

## Verdict

`request_changes` — one major finding; everything else in the change is careful, well-ordered, and thoroughly tested.

```json
{"verdict": "request_changes", "findings": [{"severity": "major", "file": "src/va/cli.py", "line": 284, "issue": "va reprocess --all-stale executes a corpus-wide destructive overwrite with no confirmation — the only guard is the printed config header, weaker than the multi-line warning on the read-only va stale for the same VA_CONFIG_DIR foot-gun.", "scenario": "User with a real SigLIP corpus forgets VA_CONFIG_DIR and runs `va --workdir .va-shots reprocess --all-stale`: every video reads stale under the stub config, both wired reprocessors overwrite every vectors/text_vectors shard with 64-dim hash-stub embeddings and restamp provenance as current, in one command with no pause between printing the plan and executing it; recovery is a full real-model re-embed. Safe path: require --yes/interactive confirmation on execute, and/or refuse stub-over-real-tagged-shard overwrites behind an explicit override."}]}
```
