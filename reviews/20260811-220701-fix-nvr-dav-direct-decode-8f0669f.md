# Agent review — approve

date: 2026-08-11T22:09:42.979589
range: origin/main..HEAD
branch: fix/nvr-dav-direct-decode
findings: 0


---

## Full review

The sandbox denied the pytest run, so I verified the test by reading it rather than executing it (noted below). I've now covered the full diff, the round-1 review, the dispute record, critical paths, and COORDINATION.md. Here is my review.

## Review — `origin/main..HEAD` (1 commit, `fix/nvr-dav-direct-decode`)

**What the change does:** removes the `-c copy` mpegts remux from the NVR window fetch (it rejected valid `.dav` footage — measured 47 MB downloaded four times and discarded), reads the `.dav` directly with `-fflags +genpts` in both downstream ffmpeg consumers, surfaces curl's exit code, and discards-and-retries a nonzero-exit (partial) download instead of accepting it past the size gate.

**Verdict: approve.** No critical, major, or minor findings survived verification. Detail on the checks that mattered:

- **Round-1 minor 2 (silent truncation on curl exit 28) is properly fixed.** `_curl` now returns the returncode (`src/va/sources/nvr.py:344`), `_fetch_window` discards the partial and consumes a retry (`nvr.py:534-541`), and the new regression test (`tests/test_nvr_source.py:668`) genuinely constructs the failure: a 5000-byte body past the 2000-byte gate with rc 28. Against the pre-fix code on this branch that payload would have been returned on attempt 1, so the test fails on the old code — it satisfies the "reproduce the original failure first" lesson. It also asserts all 4 attempts retry and the partial is cleaned up. Caveat: the Bash sandbox denied my `pytest tests/test_nvr_source.py` run, so this is a code-read verification, not an executed one; the Stop gate / CI `offline-tests` will execute it.
- **Round-1 minor 1 (lost in-loop payload-validity gate) — dispute re-judged on the merits: accepted.** The dispute in `workflow-trust-plan.md:2787` is reasoned, not reflexive: the removed "gate" was the very component that rejected valid footage; an in-loop decode probe would re-download ~65 MB per attempt against a failure mode (corrupt-on-disk recording) a retry cannot fix; the downstream failure is loud ("only N decodable frames"); and the backfill driver's end-of-pass retry covered all three observed occurrences. One pressure point worth stating: the occurrence log already records 3 real hits in ~350 pulls, and the working hypothesis (HTML error body, rc 0) is a *transient* cause that an in-loop retry likely would remedy — so the dispute's own "if observed in practice, add the cheap dhav-magic sniff" condition is arguably already met. But the record sets a sharper retraction trigger (end-of-pass retries failing on the same windows) and commits to the sniff if it fires. That is a monitored, bounded residual with no wrong-data risk, so I do not re-report it.
- **Correctness of the retry loop:** the rc gate correctly precedes the size gate; `continue` preserves the per-attempt `_stop_load` + settle-sleep discipline; `_stop_load` (max_time=15) and `_snapshot_hash` (max_time=20) legitimately ignore the returncode as documented best-effort callers. The 180 s ceiling is sized against measured throughput and window cap, not guessed, and worst case (4 × ~185 s ≈ 13 min per failed window) stays bounded by `MAX_TRIES`.
- **Ground-truth discipline (CLAUDE.md):** the "seek on .dav is exact" claim carries its measurement (`-ss 2 -to 30` → 28.00 s), and the remux-removal rationale carries the 2026-08-11 47 MB reproduction. Determinism is not being presented as correctness here.
- **Contracts / combinations:** the change is entirely inside the NVR source's device layer — cached-mp4 output, `source_key`, schema, and the `_fetch_window`/`_frame_hashes` seams tests stub are all shape-unchanged (the two `.ts`→`.dav` test edits are mechanical renames, weakening nothing). `src/va/sources/` is not in `scripts/critical_paths.txt`, so no attestation label is due; the affected combination is the real-NVR path only, which cannot be exercised offline and is covered by the recorded live measurements.
- **Documentation parity:** no new env vars, flags, or config keys (the 180 s is an internal constant with its rationale in-code). The dispute record lands in `workflow-trust-plan.md`, the sanctioned ledger for disputes.
- **Commit message:** provisional `need_agent_review:` subject — exempt; the finalize amend must still describe the change plainly (the current subject already does).

```json
{"verdict": "approve", "findings": []}
```
