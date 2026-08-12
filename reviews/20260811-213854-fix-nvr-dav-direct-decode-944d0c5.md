# Agent review — approve

date: 2026-08-11T21:40:56.434033
range: origin/main..HEAD
branch: fix/nvr-dav-direct-decode
findings: 1

- **minor** `src/va/sources/nvr.py:542` — Unaddressed and undisputed round-1 finding: the retry loop still has no payload-validity gate — any .dav with curl rc 0 and size > 2000 bytes is returned on attempt 1, so a large-but-undecodable payload skips the remaining retries and hard-fails the whole pull downstream.
  - scenario: The NVR serves an oversized CGI error body or transient garbage over 2 KB with HTTP 200; _fetch_window returns it immediately, _pull_window raises 'only 0 decodable frames — nothing to verify' without retrying, and the watcher's watermark wedges for a cycle where a retry within the same pull would likely have succeeded. Fix: run a cheap decodability check (e.g. _frame_hashes yielding < 3 decodable frames) inside the retry loop so bad payloads consume retries.

---

## Full review

A pytest run is already live (PID 3624588), so per the repo's lesson I won't add another; I verified the new test's control flow by hand instead (fake curl returns 28 → each of the 4 attempts unlinks and retries → `None` returned, file gone — the assertions hold, and the test would fail against the pre-fix code where the 5000-byte file passed the size gate on attempt 1).

## Review

**Scope:** one commit, `944d0c5` — reads NVR `.dav` pulls directly instead of remuxing to mpegts, adds `+genpts` to both downstream ffmpeg consumers, surfaces curl's exit code, and raises the transfer ceiling to 180 s. This is round 2 of the branch: it fixes round-1 minor 2 (silent `--max-time` truncation) with a real regression test.

**What checks out:**

- **The remux removal is correct and evidence-backed.** Both remaining ffmpeg consumers (`_frame_hashes`, `_trim_encode`) use identical `-fflags +genpts` input flags, so the sampling clock and the trim clock stay mutually consistent — the index→time mapping that the trim bounds depend on survives the format change. The docstrings carry ground-truth measurements (47 MB × 4 reproduction, 28.00 s trim accuracy), satisfying the determinism-is-not-correctness rule.
- **The curl exit-code fix is genuine, not decorative.** The new test constructs its scenario (partial file past the size gate + exit 28) and would fail on the previous commit, meeting the "fail on the old code first" bar. Test renames `window.ts` → `window.dav` are mechanical; nothing weakened.
- **No contract, schema, doc, or combination gaps.** The external surface (cached mp4, `source_key`, catalog schema) is unchanged, so no COORDINATION.md entry is owed; no new env vars/flags/config keys, so no documentation-parity gap. The device layer is real-NVR-only and correctly stubbed in tests. The 180 s value is hardcoded *structure* (a budget) with its sizing derivation in the comment — compliant. Commit subject is provisional `need_agent_review:`, exempt from the clarity rule.

**One finding — a carry-over.** Round-1 minor 1 was neither fixed nor disputed in workflow-trust-plan.md, and it still holds in the current code: `src/va/sources/nvr.py:542` returns any `.dav` with `rc == 0` and size > 2000 bytes on attempt 1. The old remux, whatever its faults, was a payload-validity gate inside the retry loop; now a large-but-undecodable payload (transient device garbage, or an oversized CGI error body served with HTTP 200) skips the remaining `MAX_TRIES − 1` retries and hard-fails downstream at "only 0 decodable frames — nothing to verify". In the watcher that holds the watermark and burns one device pull per cycle until a pull happens to succeed. Safe path: run a cheap decodability probe (e.g. `_frame_hashes` count < 3) inside the retry loop so bad payloads consume retries, keeping the downstream raise as the final backstop. Minor, same severity as round 1 — it narrows retry resilience, it doesn't corrupt footage.

Verdict: **approve** (no critical or major findings).

```json
{"verdict": "approve", "findings": [
  {"severity": "minor", "file": "src/va/sources/nvr.py", "line": 542,
   "issue": "Unaddressed and undisputed round-1 finding: the retry loop still has no payload-validity gate — any .dav with curl rc 0 and size > 2000 bytes is returned on attempt 1, so a large-but-undecodable payload skips the remaining retries and hard-fails the whole pull downstream.",
   "scenario": "The NVR serves an oversized CGI error body or transient garbage over 2 KB with HTTP 200; _fetch_window returns it immediately, _pull_window raises 'only 0 decodable frames — nothing to verify' without retrying, and the watcher's watermark wedges for a cycle where a retry within the same pull would likely have succeeded. Fix: run a cheap decodability check (e.g. _frame_hashes yielding < 3 decodable frames) inside the retry loop so bad payloads consume retries."}
]}
```
