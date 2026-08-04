# Agent review — approve

date: 2026-08-03T19:37:48.147435
range: origin/main..HEAD
branch: loop/ws2d-profile-knobs
findings: 2

- **minor** `config/profiles/footage/generic.yaml:20` — Comment block claims '(defaults shown)' but lists retention_days: 14, whose real default is null (keep forever) — a mislabeled foot-gun replicated in all four config dirs.
  - scenario: An operator copies the 'defaults' block into a new footage profile expecting no behavior change; once P7.a's prune consumes retention_days, that profile silently deletes Tier-1 data older than 14 days. Fix: show retention_days: null, or retitle the block 'example values'.
- **minor** `src/va/configuration.py:38` — WS2.d Done-when says the knobs 'land in RoleConfig' but they land on Config.footage; the deviation is reasoned and documented in COORDINATION.md/test docstring, yet the loop item's Done-when text was never amended.
  - scenario: A later conformance check against architecture-evolution-loop.md line 97 reads WS2.d as unfinished (knobs absent from RoleConfig) even though the intent is met; amend the loop entry to record the placement decision as was done for the stacking deviation.

---

## Full review

## Review — `loop/ws2d-profile-knobs` (origin/main..HEAD, 4 commits)

**Verdict: approve** — two minor findings, no correctness defects found.

### Scope note

The first three commits (WS2.a/b/c) reached this HEAD's parent (`31f33de`) already carrying eight review rounds ending in approve; I re-checked their surface rather than re-litigating, and focused adversarial depth on the new WS2.d commit (`52efffd`). The one carried round-8 finding (CLI printing "no in-place reprocess yet" for profile-disabled skips) **is fixed in WS2.d**: `execute_reprocess` now returns 3-tuple `(video_id, role, reason)` skips, the CLI prints the real reason, and both reasons have direct test assertions (`test_reprocess_never_reruns_a_profile_disabled_role` asserts "profile disables"; `test_execute_skips_role_without_in_place_reprocess` asserts "no in-place reprocess yet").

### What I verified

- **WS2.d correctness:** `FootageSettings` defaults reproduce today's behavior (None/relative/auto), `extra="forbid"` makes a typo'd knob fail at load naming the yaml, the bare-`off`-is-YAML-false trap is both documented and tested, and the negative-retention validator works. Settings extraction (`doc` minus `roles`) is correct for empty/missing files.
- **Shape change:** the `skipped` 3-tuple is a breaking return-shape change, but the CLI is its only consumer in `src/`, both are updated in the same commit, and COORDINATION.md flags it with ⚠. No stale 2-tuple unpacking remains.
- **Config plumbing (spot re-check of WS2.c):** the ingest pin, purge-on-skip, dependency closure (STT→diarizer, detector→tracker), unconditional gate evaluation for zero-frame/silent inputs, stamped-and-disabled staleness, and per-profile config pinning in stale/reprocess all hold up under the failure scenarios I traced (retry of a failed ingest, profile edited after ingest, broken/renamed profile yaml mid-batch, minimal roles.yaml). I attempted to construct a live-rows leak through the tracker-purge path and could not reach it without a prior attempt that already violates its own profile — not reportable.
- **Combinations:** footage yamls are byte-identical across all four config dirs (verified by diff) and `test_all_shipped_profiles_parse` loads generic+security in each. The embedder-override query-blindness caveat is documented in CLAUDE.md with the loop-backlog pointer.
- **Docs:** `--profile` flags, `videos.profile`, schema v3, the knobs, and the quote-"off" gotcha are all in CLAUDE.md/COORDINATION.md within the range.
- **Suite:** full offline suite run just now: **573 passed / 2 skipped**, matching the COORDINATION entry's claimed count. Commit subjects are provisional `need_agent_review:` and exempt from the clarity rule.

### Findings

**1. minor — `config/profiles/footage/generic.yaml:18-20` (and its three run-*/ copies):** the comment block says "(defaults shown)" but lists `retention_days: 14`, whose actual default is `null` (keep forever). Scenario: an operator authoring a new profile copies the "defaults" block verbatim, believing it changes nothing; once P7.a's rolling prune consumes `retention_days`, that profile silently deletes Tier-1 data older than 14 days on footage that was meant to keep A-EV keep-forever behavior. Safe path: show `retention_days: null` in the defaults block (or retitle the block "example values" and state the defaults inline).

**2. minor — plan conformance, `src/va/configuration.py:38` vs `architecture-evolution-loop.md:97`:** WS2.d's Done-when says the knobs "land in `RoleConfig`", but they land on `Config.footage` instead. The deviation is reasoned (the knobs are profile-wide, not per-role) and documented in the test docstring and COORDINATION.md — but the loop item's Done-when text itself still states the unmet condition, so a later verification pass against the loop file reads WS2.d as unfinished. Safe path: amend the WS2.d loop entry (or its `[R]` log line) to record the placement decision, the same way the stacking deviation was recorded.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "config/profiles/footage/generic.yaml", "line": 20, "issue": "Comment block claims '(defaults shown)' but lists retention_days: 14, whose real default is null (keep forever) — a mislabeled foot-gun replicated in all four config dirs.", "scenario": "An operator copies the 'defaults' block into a new footage profile expecting no behavior change; once P7.a's prune consumes retention_days, that profile silently deletes Tier-1 data older than 14 days. Fix: show retention_days: null, or retitle the block 'example values'."}, {"severity": "minor", "file": "src/va/configuration.py", "line": 38, "issue": "WS2.d Done-when says the knobs 'land in RoleConfig' but they land on Config.footage; the deviation is reasoned and documented in COORDINATION.md/test docstring, yet the loop item's Done-when text was never amended.", "scenario": "A later conformance check against architecture-evolution-loop.md line 97 reads WS2.d as unfinished (knobs absent from RoleConfig) even though the intent is met; amend the loop entry to record the placement decision as was done for the stacking deviation."}]}
```
