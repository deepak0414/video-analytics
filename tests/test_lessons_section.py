"""The pruning rule as a test (workflow-trust-plan.md WT.8).

CLAUDE.md's Lessons section exists so corrections are never re-learned — but a
bloated CLAUDE.md gets skimmed and then ignored, which would silently disable the
whole advisory layer. The plan states a pruning rule in prose; prose decays, so
this makes it mechanical: the suite goes red when the list outgrows its budget,
forcing a fold into prose or a hook.
"""

import datetime
import re
from pathlib import Path

import pytest

CLAUDE_MD = Path(__file__).resolve().parents[1] / "CLAUDE.md"
MAX_ENTRIES = 20  # the plan's "~20 lines" budget


def lessons_block():
    text = CLAUDE_MD.read_text()
    start = text.find("## Lessons")
    assert start != -1, "CLAUDE.md has no '## Lessons' section (WT.8 deliverable)"
    end = text.find("\n## ", start + 1)
    return text[start:end if end != -1 else len(text)]


def entries():
    """Whole entries, with wrapped continuation lines joined. Reading only the
    first physical line made the length budget unenforceable: CLAUDE.md wraps at
    ~90 columns, so no first line could ever approach the limit."""
    out, cur = [], None
    for ln in lessons_block().splitlines():
        if re.match(r"^- \d{4}-\d{2}-\d{2}: ", ln):
            if cur:
                out.append(cur)
            cur = ln.strip()
        elif cur is not None and ln.startswith("  ") and ln.strip():
            cur += " " + ln.strip()
        elif cur is not None and not ln.strip():
            out.append(cur)
            cur = None
    if cur:
        out.append(cur)
    return out


def test_lessons_section_exists_and_documents_pruning():
    block = lessons_block()
    assert "/lesson" in block, "the section must name the command that appends to it"
    assert "Pruning rule" in block, "the pruning rule must travel with the list"


def test_lessons_are_dated_one_liners():
    found = entries()
    assert found, "no dated lessons found — expected '- YYYY-MM-DD: <lesson>' entries"
    for ln in found:
        assert len(ln) < 400, f"lesson too long, fold it into prose instead:\n{ln}"


def test_every_lesson_has_a_plausible_date():
    for ln in entries():
        stamp = ln[2:12]
        try:
            datetime.date.fromisoformat(stamp)
        except ValueError:  # pragma: no cover - the assert reports it
            pytest.fail(f"unparseable date in lesson: {ln}")


def test_no_duplicate_lessons():
    bodies = [ln.split(": ", 1)[1].lower() for ln in entries()]
    assert len(bodies) == len(set(bodies)), "duplicate lesson text — update, don't append"


def test_section_within_pruning_budget():
    """The pruning rule, enforced. When this fails the fix is NOT to raise the
    budget: fold stable lessons into the relevant prose section or convert them
    to hooks, then delete them here."""
    found = entries()
    assert len(found) <= MAX_ENTRIES, (
        f"{len(found)} lessons exceeds the {MAX_ENTRIES}-entry budget — fold the "
        f"stable ones into prose or into a hook and delete them from the list "
        f"(raising MAX_ENTRIES defeats the rule this test exists to enforce)"
    )

def test_every_bullet_conforms_to_the_entry_format():
    """A malformed bullet was invisible to every other test here — budget, length,
    date and dedup all iterate over CONFORMING entries only, so an undated lesson
    silently escaped enforcement. Assert the section contains nothing else."""
    # Any bullet SHAPE counts, not just "- " at column 0: an indented or `*`
    # bullet was invisible to all seven checks, so the section could grow past
    # its budget with the suite green.
    bullets = [ln for ln in lessons_block().splitlines()
               if re.match(r"^\s*[-*+]\s", ln)]
    bad = [ln for ln in bullets if not re.match(r"^- \d{4}-\d{2}-\d{2}: ", ln)]
    assert not bad, (
        "lesson bullets must be '- YYYY-MM-DD: <lesson>' so the budget, length, "
        f"date and dedup checks can see them; malformed: {bad}"
    )
