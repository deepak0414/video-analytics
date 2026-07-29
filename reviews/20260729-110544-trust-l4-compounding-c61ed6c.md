# Agent review — approve

date: 2026-07-29T11:09:18.377410
range: origin/main..HEAD
branch: trust/l4-compounding
findings: 1

- **minor** `tests/test_lessons_section.py:90` — The conformance test only examines lines starting exactly '- ' at column 0, so an indented ('  - YYYY-MM-DD: ...') or '* '-style bullet escapes all seven checks — entries() drops it or merges it into the prior entry, and nothing asserts its absence.
  - scenario: A hand-edited indented lesson bullet after a blank line is invisible to the budget, length, date, dedup, and conformance checks; the section can grow past the 20-entry budget while the suite stays green.

---

## Full review

Review complete. This is the WT.8 deliverable (the `/lesson` command, the CLAUDE.md Lessons section with seven seeded lessons, `tests/test_lessons_section.py`, and the plan's as-built/descope record), reviewed after the prior round's request_changes.

**Both prior findings are verified fixed at HEAD.** The four corrupted citations (rows 100, 102, 103, 104 in the PR-4 label-guard narrative) are restored and I confirmed each target row still exists in the §8 matrix (`workflow-trust-plan.md:2433-2437`). The conformance test the prior round asked for (`test_every_bullet_conforms_to_the_entry_format`) is present, and by inspection it does catch the undated-bullet case from that finding (an undated `- ` bullet fails its regex). The descope is clean: the only gate-machinery change in the range is the new `.claude/commands/lesson.md` (the deliverable itself), no loop-guard hook code or orphaned tests remain, and the WT.9 deferral row plus the CLAUDE.md lesson's "advisory until then" note are mutually consistent. WT.8's "Done when" (dated one-liner appended, diff shown) is evidenced by the seeded section and the recorded first-use of the dedup rule. Full offline suite: **433 passed, 2 skipped, 0 failed** — the new test file integrates cleanly. No tests were deleted or weakened; the uncommitted working-tree modifications (`registry.py`, `schema.py`, `test_trace_link.py`) are outside the reviewed range.

One residual minor, in the same family the last two rounds worked through:

- **minor** — `tests/test_lessons_section.py:90`: the conformance backstop only inspects lines that start exactly `"- "` at column 0, so a bullet that is indented (`  - 2026-07-30: …` placed after a blank line) or spelled `* ` escapes *every* check — `entries()` either drops it or silently concatenates it into the previous entry, and the conformance test never sees it. Scenario: a hand-edited indented lesson grows the section past the 20-entry budget while the suite stays green. This is a narrower slice of the gap already fixed (the realistic accident — an undated flat bullet — is now caught), so it does not block.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "tests/test_lessons_section.py", "line": 90, "issue": "The conformance test only examines lines starting exactly '- ' at column 0, so an indented ('  - YYYY-MM-DD: ...') or '* '-style bullet escapes all seven checks — entries() drops it or merges it into the prior entry, and nothing asserts its absence.", "scenario": "A hand-edited indented lesson bullet after a blank line is invisible to the budget, length, date, dedup, and conformance checks; the section can grow past the 20-entry budget while the suite stays green."}]}
```
