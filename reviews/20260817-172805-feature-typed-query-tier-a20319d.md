# Agent review — request_changes

date: 2026-08-17T17:33:27.275929
range: origin/main..HEAD
branch: feature/typed-query-tier
findings: 1

- **major** `src/va/pipeline/aggregate.py:259` — A windowed count silently excludes all tracks on NULL-start_epoch (A-EV) videos with no caveat, note, or CLI/doc disclosure, so a pure A-EV workdir yields a confident 'CODE-COUNTED: 0' — the false-zero class the tier's own contracts and plan (§4 'a count must always disclose what it did NOT do') exist to prevent; the mixed-workdir caveat only fires with >=2 footage domains and covers nothing in a single-domain A-EV workdir.
  - scenario: va aggregate count 'car' --from 2026-08-11T00:00 --to 2026-08-11T12:00 --tz America/Los_Angeles on the default .va workdir containing the Ferrari clip (car tracks present, start_epoch NULL) prints 'total 0' with caveats that never mention the exclusion; via an LLM planner that sets needs_aggregation on such a workdir, ask() leads the rendered answer with '[CODE-COUNTED: 0 ...]'. Fix: detect matched-category tracks existing only on unplaced videos (or zero epoch-placed done videos) and append an explicit caveat or degrade to an honest 'not run' note; document the exclusion in the CLI help and CLAUDE.md command block.

---

## Full review

Review complete. I read the full diff (one commit, `a20319d`, the entire TQ1.a–TQ1.h stack squashed into one review range), the plan and loop docs, the prior review dispositions, and the surrounding code (`ask.py`, `retrieval.py`, `catalog_sqlite.py`, the new test files). I could not execute the test suite in this session (pytest invocation was not permitted), so I relied on reading the tests plus the loop log's recorded runs (833 passed / 2 skipped at `464eea3`).

## What I verified and did not report

- **Time math**: the fixture epochs (Aug-11 2026 00:00/12:00 PDT → 1786431600 / 1786474800) check out by hand; half-open membership, DST spring-forward diagnosis, the strftime-TEXT trap guard, and the `math.ceil` bucket allocation are all correct and each is pinned by a test that constructs its scenario (boundary tracks at exactly W0 and W1−10/W1+10, a NULL-epoch video, TEXT bounds raising).
- **No contract breaks**: everything is additive (new module, new `QueryPlan` field with default, new modality string, new `TrackStore` method), and each piece is logged in COORDINATION.md with the ⚠ shared-file markers.
- **Prompt/registry drift**: `PLANNER_PROMPT` is built from `AGGREGATION_TOOLS` at import time, brace-escaped, and the drift test actually calls `.format()`. No circular import (`aggregate` → storage/contracts only).
- **The `ask()` lead guard** keys on the specific aggregate item's content rather than a generic "CODE-COUNTED" substring, with a regression test for the quoting-narrator case.
- **Degrade paths**: 11 parametrized bad-argument shapes all produce a note and no number, including the `cameras=[]` falsy-guard and null/list `limit` — the earlier review rounds' fixes are genuinely present and tested.
- The `hand-sql-crosscheck` golden provenance label honestly discloses that it pins tracker output, not footage truth — this satisfies the determinism≠correctness rule rather than violating it.

## The one finding

**Major — silent exclusion of unplaced (A-EV) footage can produce a confident, undisclosed false zero.** `select_placed` skips videos with NULL `start_epoch` "by construction" (deliberate, tested at `tests/test_aggregate_select_tracks.py:131`), but no runtime surface ever discloses the exclusion: the caveat assembly at `src/va/pipeline/aggregate.py:259` covers raw-upper-bound, parked, and start-membership, and the mixed-workdir caveat fires only when ≥2 footage domains are present. So on a pure A-EV workdir (one domain — e.g. the default `.va` with the Ferrari clip, which has car tracks), `va aggregate count "car" --from … --to … --tz …` prints `total 0` with provenance and caveats that never mention that every matching track sat on a video with no wall-clock placement; through the planner path, `ask()` *leads* the rendered answer with `[CODE-COUNTED: 0 'car' track(s) …]`. That is exactly the confident-false-zero class this tier's own contracts docstring names as "the exact bug this tier exists to prevent," and it violates the plan's §4 rule that a count must always disclose what it did NOT do. The CLAUDE.md command block and the CLI help also omit the exclusion, so nothing warns the operator either. Safe path: in `count_objects` (or `select_tracks`), detect when matched-category tracks exist only on NULL-`start_epoch` videos (one cheap extra query) — or when the workdir has zero epoch-placed `done` videos — and append an explicit caveat (or degrade to a "not run: no wall-clock-placed footage" note in `dispatch_aggregation`); document the exclusion in the CLI help and the CLAUDE.md block in the same change. This was not raised in review rounds r1–r5 and no dispute in workflow-trust-plan.md covers it.

Everything else in the stack is solid work — the seam discipline, the honesty plumbing, and the test construction quality are all real. But the gap above is on the tier's central promise, so per the verdict rule:

```json
{"verdict": "request_changes", "findings": [{"severity": "major", "file": "src/va/pipeline/aggregate.py", "line": 259, "issue": "A windowed count silently excludes all tracks on NULL-start_epoch (A-EV) videos with no caveat, note, or CLI/doc disclosure, so a pure A-EV workdir yields a confident 'CODE-COUNTED: 0' — the false-zero class the tier's own contracts and plan (§4 'a count must always disclose what it did NOT do') exist to prevent; the mixed-workdir caveat only fires with >=2 footage domains and covers nothing in a single-domain A-EV workdir.", "scenario": "va aggregate count 'car' --from 2026-08-11T00:00 --to 2026-08-11T12:00 --tz America/Los_Angeles on the default .va workdir containing the Ferrari clip (car tracks present, start_epoch NULL) prints 'total 0' with caveats that never mention the exclusion; via an LLM planner that sets needs_aggregation on such a workdir, ask() leads the rendered answer with '[CODE-COUNTED: 0 ...]'. Fix: detect matched-category tracks existing only on unplaced videos (or zero epoch-placed done videos) and append an explicit caveat or degrade to an honest 'not run' note; document the exclusion in the CLI help and CLAUDE.md command block."}]}
```
