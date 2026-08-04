# Agent review — approve

date: 2026-08-03T19:27:01.292510
range: origin/main..HEAD
branch: loop/ws2c-profile-gates-roles
findings: 1

- **minor** `src/va/cli.py:309` — Profile-disabled roles routed to `skipped` print the capability-gap message ('no in-place reprocess yet') instead of the policy reason, misstating why the role was skipped.
  - scenario: Ingest under a profile, edit it to disable vlm_captioner, run `va reprocess --all-stale --yes`: the CLI prints 'vlm_captioner: skipped — no in-place reprocess yet' even though the captioner reprocessor exists and was withheld because the profile forbids the role; an operator concludes the feature is missing and may expect an in-place rebuild that must never run under the profile. Fix: carry a skip reason in the tuple and print 'profile disables this role — `va reingest` purges its rows' for the disabled route.

---

## Full review

All three round-7 findings are fixed at HEAD with real tests, and the full suite is green. Final verification pass complete — here is the report.

## Review — round 8 of `loop/ws2c-profile-gates-roles` (HEAD `31f33de`)

**Verdict: approve** — one minor finding.

**Round-7 findings verified fixed, each with a reproducing test:**

1. **Reprocess convergence (major)** — `execute_reprocess` now computes the profile-disabled set per item (with the `GATE_DEPENDENTS` closure, against the per-profile pinned config) and routes those roles to `skipped` before the `_REPROCESSORS` dispatch (`src/va/pipeline/reprocess.py:373-375`). `test_reprocess_never_reruns_a_profile_disabled_role` reproduces the exact round-7 scenario (ingest under `evolving`, edit to disable `vlm_captioner`, run reprocess) and asserts the captioner is never re-run.
2. **Zero-frame tracker stamp (minor)** — the tracker gate is now evaluated unconditionally (`tracker_on = _enabled("object_tracker")` at `src/va/pipeline/ingest.py:411-415`, mirroring `diarizer_on`), covered by `test_zero_frame_ingest_still_registers_a_disabled_tracker_skip`, which patches `sample_frames` to empty and asserts no `object_tracker` stamp.
3. **COORDINATION.md drift (minor)** — a corrective append-only entry records the corrected staleness semantics (stamped-and-disabled reads stale), the non-bool `enabled:` load rejection, and the reprocess `skipped` routing.

**Other checks:** schema v3 migration present and tested (fresh-DB + pre-profile-NULL paths); all registry getters accept optional `cfg`; test-double widenings follow the CLAUDE.md lambda-doubles lesson without weakening assertions (the reprocess pin test got *stronger* — it now asserts per-profile memoization); footage yamls identical across all four config dirs; WS2.c "Done when" satisfied (security → 0 transcript rows with retry-purge, vocab override proven end-to-end, generic unchanged) plus the carried WS2.a shape-validation fix; no hardcoded content (security vocab lives in the yaml, cited to plan §3.2); embedder-override caveat documented in CLAUDE.md. Full offline suite: **565 passed / 2 skipped**, matching the COORDINATION entry's expected count. Commit subjects are provisional `need_agent_review:` and exempt from the clarity rule.

**The one new finding (minor):** the CLI prints the wrong *reason* for a profile-disabled skip. `_cmd_reprocess` renders every `skipped` tuple as "skipped — no in-place reprocess yet; run `va reingest …`" (`src/va/cli.py:309`), but a profile-disabled role (e.g. `vlm_captioner`, which *has* a wired in-place reprocessor) is skipped by policy, not capability. The remedy printed is correct and converges, so this is informational only — but round 7's fix explicitly asked for a "profile disables this role" message, and an operator reading "no in-place reprocess yet" for the captioner will conclude the reprocessor is missing and may expect a future in-place rebuild that must never happen under the profile. Safe path: carry a reason in the skipped tuple (or a parallel list) and print "profile disables this role — reingest purges its rows" for the disabled route.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/cli.py", "line": 309, "issue": "Profile-disabled roles routed to `skipped` print the capability-gap message ('no in-place reprocess yet') instead of the policy reason, misstating why the role was skipped.", "scenario": "Ingest under a profile, edit it to disable vlm_captioner, run `va reprocess --all-stale --yes`: the CLI prints 'vlm_captioner: skipped — no in-place reprocess yet' even though the captioner reprocessor exists and was withheld because the profile forbids the role; an operator concludes the feature is missing and may expect an in-place rebuild that must never run under the profile. Fix: carry a skip reason in the tuple and print 'profile disables this role — `va reingest` purges its rows' for the disabled route."}]}
```
