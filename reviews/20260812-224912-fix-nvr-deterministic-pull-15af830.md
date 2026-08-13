# Agent review — approve

date: 2026-08-12T22:54:22.369735
range: origin/main..HEAD
branch: fix/nvr-deterministic-pull
findings: 1

- **minor** `src/va/sources/nvr.py:307` — A ring-edge padded pull with a 0-4 s head shortfall passes the duration gate as a clip shifted late by up to ~4 s against videos.start_epoch, so the aligned exact-window fallback never engages for exactly the case it exists to fix (documented as a residual caveat, but detectable and avoidable today).
  - scenario: Outage backfill where the oldest surviving footage lands 2-4 s before `start`: the padded fetch is head-short by that much, the cut [10, 10+len] measures within ±2.0 s and is accepted shifted ~2-4 s late, and motion-episode segments (security profile pad_s ~2 s) map to the wrong relative times — the event can fall outside its stored segment. Safe path: probe the raw padded .dav's duration against the requested padded span and, on a head shortfall, skip to the exact-window phase (or subtract the shortfall from the cut offset), keeping the documented caveat only for what remains.

---

## Full review

## Review: `origin/main..HEAD` (15af830, deterministic NVR pull — pad + PTS-cut)

**Verdict: approve.** This is the amended follow-up to the round-1 review of add1ee5, and its one major finding (ring-edge pre-pad wedging the camera's watermark) was fixed exactly along the recommended safe path: a second EXACT-WINDOW phase with no pad, engaged only after the padded phase exhausts, raising only when both fail. The watermark-wedge interaction is re-documented (docstring, CLAUDE.md, COORDINATION.md) as narrowed rather than gone, matching what `watch.py:207-248` actually does.

What I verified and it held up:

- **Two-phase loop logic** (`_pull_window`): phase iteration, per-phase retry, `break` on `raw is None` (correct — `_fetch_window` already retried MAX_TRIES internally), atomic `os.replace`, temp-dir cleanup and `_stop_load` in `finally`, `_conn()` fail-fast before any work. Fail-closed arithmetic checks out: a window whose `[start,end]` is partially expired fails both phases' duration gates and raises.
- **`_probe_cut`**: decode-to-null with duration from the decoder clock, `Duration:` fallback, size gate — and it's tested against a *real* clip plus real garbage (`test_probe_cut_measures_a_real_clip_and_rejects_garbage`), so the ffmpeg stderr parsing is validated against the bundled binary, not assumed.
- **Determinism-vs-correctness rule**: the branch does this right — the deterministic claim is backed by reported ground-truth measurements (7/7 windows across lighting modes, byte-identical repeat pulls, a 20.0 s measured cut), and the removed perceptual verification is justified by a concrete false-refusal incident (11 dusk windows).
- **Test integrity**: all 20 removed tests covered the deleted dHash/ReferenceLibrary subsystem; the one orthogonal test (truncated-download discard) was preserved. The new flow is pinned end to end: padded bounds, cut offsets, retry, both-phases-exhausted raise, fallback engagement and its `(0.0, window_len)` cut, atomic landing. I traced the `_det_harness` probe-sequencing by hand against every parametrization — the assertions match the code's actual call counts. (The sandbox blocked running pytest here; I'm relying on the recorded 724 passed / 2 skipped plus static tracing.)
- **Contract/docs**: the `_pull_window` signature change (dropped `refs`) and wholesale deletions are logged in COORDINATION.md with the full symbol list; CLAUDE.md's nvr:// and `va watch` blocks were rewritten in the same change and match the code (I recomputed the "shift up to ~PAD_POST+DURATION_TOL_S" claim: head shortfall S ≤ 2 s shifts by S at full duration; 2 < S ≤ 4 s passes the gate short-and-shifted; S > 4 s fails to the fallback — the documented 4 s bound is exact). No remaining code references to deleted symbols (only prose/notes). No new env vars or CLI flags. Commit subject is a provisional `need_agent_review:` — exempt.

One residual worth recording, minor because it is explicitly documented in three places and was part of the round-1 adjudication (the reviewer offered fallback *or* offset-correction; fallback was implemented): in the 0–4 s head-shortfall band the padded phase **succeeds** with a clip shifted late by up to ~4 s against `videos.start_epoch`, so the exact-window fallback — which would have produced an aligned clip — never engages. That misalignment can exceed the security profile's motion-episode padding and mis-map segments. It's detectable without new machinery: probe the raw padded `.dav`'s duration and compare to the requested padded span; on a head shortfall, prefer the exact-window phase (or correct the cut offset). That's the natural shape for the backlogged "PTS-accurate alignment" item.

Not reported (verified and dissolved, or sub-minor): the nested MAX_TRIES loops don't multiply (a fetch failure breaks the phase); the "no decodable frames" failure label also covers an unparseable probe; retrying a byte-identical deterministic pull within a phase is wasted work but harmless; stale `ReferenceLibrary` prose in `architecture-evolution-loop.md` is an untracked local notes file outside this range.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/sources/nvr.py", "line": 307, "issue": "A ring-edge padded pull with a 0-4 s head shortfall passes the duration gate as a clip shifted late by up to ~4 s against videos.start_epoch, so the aligned exact-window fallback never engages for exactly the case it exists to fix (documented as a residual caveat, but detectable and avoidable today).", "scenario": "Outage backfill where the oldest surviving footage lands 2-4 s before `start`: the padded fetch is head-short by that much, the cut [10, 10+len] measures within ±2.0 s and is accepted shifted ~2-4 s late, and motion-episode segments (security profile pad_s ~2 s) map to the wrong relative times — the event can fall outside its stored segment. Safe path: probe the raw padded .dav's duration against the requested padded span and, on a head shortfall, skip to the exact-window phase (or subtract the shortfall from the cut offset), keeping the documented caveat only for what remains."}]}
```
