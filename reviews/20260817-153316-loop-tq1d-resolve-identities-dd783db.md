# Agent review — approve

date: 2026-08-17T15:37:25.717287
range: origin/main..HEAD
branch: loop/tq1d-resolve-identities
findings: 1

- **minor** `typed-query-tier-plan.md:195` — Plan §5.2 still declares the resolve_identities seam contract as '(list[Track], mode) -> list[Entity]', but the as-built seam (committed in this same range) is 'resolve_identities(tracks, mode, min_frames=2) -> IdentityResolution'; §5.1 got its 'as built' correction, §5.2 did not.
  - scenario: A future Role-12 session reads the plan's 'stable signature' line and implements the ReID body returning a bare list[Entity], breaking callers that consume IdentityResolution.dedup_mode/dedup_source/caveats — the exact drift the seam contract exists to prevent.

---

## Full review

Static review is complete. The sandbox denied the pytest run, so I verified behavior by hand instead: I re-derived every epoch constant in the test worksheets (2026-08-11 00:00 PDT = 1786431600, noon = 1786474800; winter PST case = 1768118400 — all correct), traced the fixture's in/out membership for both the local and UTC windows (the asserted sets match), confirmed `resolve_category` is byte-identical logic to the old `_classes` body, and confirmed `resolve_identities`'s flicker filter (`frame_count >= min_frames`) matches `distinct_counts`'s SQL predicate. The loop doc records the suite green at 779 passed as of TQ1.c, with r1/r2 reviews and their fix dispositions logged per commit.

What I checked and found sound:

- **TQ1.a contracts** — tz validation order (field validator before the model validator, so `epoch_bounds()` never sees an unvalidated tz), the PEP-495 round-trip gap detection (correct for nonexistent times, correctly accepts ambiguous fall-back times), aware-datetime offset precedence, and the evolution idiom.
- **TQ1.b seam** — pure structural promotion, no synonym content, parity pinned by hand-written literals rather than the tautological equality alone (fixed in r1, verified fixed).
- **TQ1.c** — half-open window semantics, NULL-epoch skip, `cameras=[]` vs `None` distinction, TEXT-bound TypeError guard, and the strftime false-0 regression pin all behave as documented and are each covered by a test that constructs its scenario.
- **TQ1.d** — honest-provenance fallback (`dedup_mode` reports "raw" even when "instance" was requested), unknown-mode rejection, and DB-fixture parity with `distinct_counts`. All four TQ1.d Done-when conditions are met.
- COORDINATION.md logs all four surface additions; no new env vars, CLI flags, or config keys exist to document; commit subjects are all `need_agent_review:` (exempt from the clarity rule).

One thing survived verification:

**Minor — stale seam signature in the plan doc committed in this same range.** `typed-query-tier-plan.md:195` still states the §5.2 stable contract as `(list[Track], mode) -> list[Entity]`, but the shipped seam is `resolve_identities(tracks, mode, min_frames=2) -> IdentityResolution` (entities + provenance + caveats). §5.1 received exactly this kind of "as built in TQ1.b" parenthetical when its signature drifted (a TQ1.b review fix), and TQ1.a's r1 fixed the analogous "instance-reid" string drift — §5.2 was not given the same treatment when TQ1.d landed. Since the plan sells this line as the fixed contract Role-12 will code against, a future session reading the plan would build against the wrong return shape. Safe path: update §5.2's contract line to the as-built signature (mirroring the §5.1 parenthetical) on the next stacked branch.

I also confirmed the TQ1.c r2 carried minor (start-only window membership caveat) is dispositioned inside this range as promised: plan §11 bullet 2 is present; the `count_objects` caveat emission is correctly deferred to TQ1.e.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "typed-query-tier-plan.md", "line": 195, "issue": "Plan §5.2 still declares the resolve_identities seam contract as '(list[Track], mode) -> list[Entity]', but the as-built seam (committed in this same range) is 'resolve_identities(tracks, mode, min_frames=2) -> IdentityResolution'; §5.1 got its 'as built' correction, §5.2 did not.", "scenario": "A future Role-12 session reads the plan's 'stable signature' line and implements the ReID body returning a bare list[Entity], breaking callers that consume IdentityResolution.dedup_mode/dedup_source/caveats — the exact drift the seam contract exists to prevent."}]}
```
