---
description: Record a correction in CLAUDE.md's Lessons section so it is never re-learned
allowed-tools: ["Read", "Edit", "Bash"]
---

Take the argument text as a lesson learned — usually something the user just corrected,
or a mistake a review caught.

1. **Decide where it belongs first.** If the lesson is a *mechanical invariant*
   ("always/never do X", "never run Y"), say so and propose the hook that would enforce
   it (`.claude/hooks/` for session actions, `.githooks/` for git actions, a test for
   code invariants). Instructions decay; hooks don't. Offer the prose line as the
   fallback, not the default.
2. Otherwise rewrite it as ONE imperative line, ≤2 sentences, that **includes the why** —
   a rule without its reason gets discarded by the next reader who thinks it's arbitrary.
   Prefer the concrete failure ("the SIGPIPE test wrote 14 KB against a 64 KB buffer")
   over the abstraction ("test your tests").
3. Append it under `## Lessons` in CLAUDE.md as `- YYYY-MM-DD: <lesson>` (get today's
   date with `date +%F`; newest at the bottom). If a materially identical lesson already
   exists, UPDATE that line instead of adding a near-duplicate.
4. Show the diff.
5. If the section now exceeds ~20 entries, say so and propose which stable entries to
   fold into the relevant prose section above (or into a hook) and delete from the list —
   `tests/test_lessons_section.py` enforces this, so an oversized list fails the suite.

Never silently reword an existing lesson's meaning: correcting a lesson is itself a
lesson, and the reason it changed is the part worth keeping.
