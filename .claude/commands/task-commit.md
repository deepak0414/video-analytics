---
description: Drive a completed task through the review lifecycle — scope check, provisional commit, review loop, digest, human approval, finalize
---

You are the committer. Walk these steps IN ORDER. If anything fails or surprises
you at any step, stop and report — never improvise around a gate.
(Full spec: workflow-trust-plan.md. The hooks enforce the skeleton; this file is
the craft that the hooks cannot check.)

1. **Scope check.** `git status --short`. Stage ONLY files you changed for THIS
   task — other sessions' dirty files and unrelated edits are untouchable (stage
   surgically; never `git add -A` on a shared tree). One task = one logical
   unit; if the work mixes structural reshaping with behavioral change, split it
   into separate task-commits (structural first).

2. **Self-review the diff** (`git diff --staged`) before the reviewer sees it:
   leftovers, debug prints, accidental deletions, anything you can't explain.

3. **Combination check.** This repo is a matrix of backends (roles ×
   stub/real × config dirs × footage profiles). Name the cells your change can
   affect. The offline suite covers every stub path — run the affected
   combinations' specific tests beyond that, and note whether a real-backend
   combination needs the golden gate. "Default stub path only" is a valid
   answer, but say it explicitly and say why.

4. **Documentation check.** New env var, flag, config key, harness mode, setup
   step, or gotcha? Documenting it in the right file is part of THIS task
   (CLAUDE.md for session-critical facts; the owning plan doc for design;
   READMEs for local conventions; COORDINATION.md for cross-agent contracts).
   Unsure whether/where → carry it into the digest as an open question. Never
   guess, never skip silently.

5. **Provisional commit:** subject `need_agent_review: <short description>`.
   The post-commit hook runs the fresh reviewer (minutes — run long commits in
   the background). On `request_changes`: read the newest `reviews/` ledger,
   fix the critical/major findings, `git commit --amend` keeping the subject —
   the review re-fires. To dispute a finding, record the reasoned dispute in
   workflow-trust-plan.md (agents cannot write `reviews/`).

6. **Digest to the human** — all four sections, every time:
   - **Verdict & findings** — per round, with ledger paths.
   - **Affected combinations & tests run** — from step 3, with evidence
     (pasted output, never "tests pass").
   - **Documentation added / open doc questions** — from step 4.
   - **Proposed final commit message** — see step 8's language rule.
   Then STOP. The human approves by creating `.commit-approved` (human-only —
   never create it, never work around it).

7. **Finalize:** `git add reviews/` then `git commit --amend` with the real
   subject. The sentinel is consumed; the ledger ships inside the commit.

8. **Commit-message language rule.** Shorthand IDs (WT.x, RI.x, matrix rows,
   plan-card names) are conversation vocabulary — fine in digests and sessions,
   banned as the carrier of meaning in final commit messages and PR text. An
   uninformed reader must understand the change from the description alone;
   plan IDs may follow in parentheses after a self-sufficient description.
   `feat(trust): assemble the reviewer prompt from the agent file (plan: WT.11/RI.1)` — good.
   `feat: implement RI.1` — incomplete. The finalize amend is where conversation
   vocabulary becomes reader-facing history.

Related: `/verify` generates the Evidence block for PR bodies. `/lesson` records
corrections after mistakes; step 4 documents new surface at creation time — same
compounding philosophy, opposite directions, don't conflate them.
