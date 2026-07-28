# Agent review — approve

date: 2026-07-27T22:57:02.279946
range: origin/main..HEAD
branch: trust/l0-session-guards
findings: 4

- **minor** `tests/test_trust_guards.py:258` — run_stop_gate copies os.environ, so the five stop_gate tests inherit VA_AGENT_REVIEW and fail whenever it is set (e.g. inside the agent-review reviewer session).
  - scenario: With VA_AGENT_REVIEW=1 in the environment the stop-gate recursion guard exits 0 before running pytest, so all five stop_gate tests assert returncode 2 but get 0: reproduced 5 failed/115 passed with it set vs 120 passed with it unset.
- **minor** `.claude/hooks/bash_guard.py:118` — Force/delete-push logic only catches the ':main' refspec form, so `git push --delete origin main` and `-d origin main` pass bash_guard, contradicting validation-matrix row 50's 'blocked in bash_guard AND pre-push' claim.
  - scenario: `git push --delete origin main` exits 0 from bash_guard (verified); the deletion is still stopped at pre-push (refs/heads/main deletion), so the invariant holds but the in-session layer and the matrix overclaim coverage.
- **minor** `.claude/hooks/bash_guard.py:31` — The commit -n rule scans the entire command including the quoted commit message, so a message containing ' -n' is falsely blocked.
  - scenario: `git commit -am 'wip: handle -n flag'` matches `commit\b[^|;&\n]*\s-[a-zA-Z]*n` and is blocked despite being a legitimate commit; workaround is to reword.
- **minor** `.guard-override:1` — The human maintenance sentinel is still present in the working tree at finalize, relaxing machinery-write and core.hooksPath rules for every session on this machine.
  - scenario: While .guard-override exists both guards drop their MAINTENANCE rules, so any agent session can rewrite .githooks/, .claude/ and the trust scripts; flagged in the prior ledger and not yet cleared.

---

## Full review

## Review — `origin/main..HEAD` (2 commits, WT.3 session guards)

I reviewed the committed diff empirically, feeding PreToolUse-style payloads to the committed hooks rather than reading the regexes alone, and ran the test suite in both environments.

**The four majors from the previous ledger (`…-dae6436.md`) are all genuinely fixed at this HEAD:**
- `sed --in-place` / `install` now match `WRITE_VERBS` — verified blocked.
- `.git/config` and `.git/hooks/` are in `path_guard` `ALWAYS_PROTECTED` — verified `.git/config` → exit 2.
- `git push --force --all/--branches`, `--mirror`, and `:main` deletion are blocked in `bash_guard` — all verified.
- `config/` is in the stop-gate cache scope.

I could not break the force-push, quoted-refspec, `git -c/-C` prefix, clustered-flag, or protected-artifact rules — those all hold. What remains is minor.

**Findings**

1. **Non-hermetic guard tests (test integrity).** `tests/test_trust_guards.py:run_stop_gate` does `env = os.environ.copy()`, so the five `stop_gate` tests inherit `VA_AGENT_REVIEW` from the ambient environment. When that variable is set — which is exactly the state inside the `agent-review.sh` headless reviewer session (this session has `VA_AGENT_REVIEW=1`) — the stop-gate recursion guard fires and every one of those tests fails. Reproduced: `5 failed, 115 passed` with the var set; `120 passed` with it popped. Fail-safe (false red, and the enforced gate paths — pre-push Gate 1, CI — don't set the var), but the helper should scrub `VA_AGENT_REVIEW` so the trust anchor's own tests are deterministic regardless of ambient env.

2. **Flag-form deletion of remote main slips past `bash_guard`.** The push logic only catches the `:main` refspec form; `git push --delete origin main` and `git push -d origin main` return exit 0 (verified). The invariant still holds because `.githooks/pre-push:10-14` blocks deletion of `refs/heads/main`, but validation-matrix row 50 claims this is blocked "in bash_guard AND pre-push," which overstates the in-session layer.

3. **`commit -n` rule false-positives on the commit message.** The rule `commit\b[^|;&\n]*\s-[a-zA-Z]*n` scans the whole command, so a legitimate commit whose message contains ` -n` (e.g. `git commit -am 'wip: handle -n flag'`) is blocked. Same acknowledged "prose trips the guard" class the plan already documents for heredocs; trivial reword workaround.

4. **`.guard-override` is live in the working tree** (untracked, created 21:30). While present it relaxes the machinery-write and `core.hooksPath` rules for every session on this machine. Out of the committed diff's scope (gitignored), but the prior ledger flagged it and it's still there at finalize — the human should remove it.

No critical or major defects survived verification; the previous round's majors are resolved and the offline suite is green in the canonical environment (126 passed).

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "tests/test_trust_guards.py", "line": 258, "issue": "run_stop_gate copies os.environ, so the five stop_gate tests inherit VA_AGENT_REVIEW and fail whenever it is set (e.g. inside the agent-review reviewer session).", "scenario": "With VA_AGENT_REVIEW=1 in the environment the stop-gate recursion guard exits 0 before running pytest, so all five stop_gate tests assert returncode 2 but get 0: reproduced 5 failed/115 passed with it set vs 120 passed with it unset."},
{"severity": "minor", "file": ".claude/hooks/bash_guard.py", "line": 118, "issue": "Force/delete-push logic only catches the ':main' refspec form, so `git push --delete origin main` and `-d origin main` pass bash_guard, contradicting validation-matrix row 50's 'blocked in bash_guard AND pre-push' claim.", "scenario": "`git push --delete origin main` exits 0 from bash_guard (verified); the deletion is still stopped at pre-push (refs/heads/main deletion), so the invariant holds but the in-session layer and the matrix overclaim coverage."},
{"severity": "minor", "file": ".claude/hooks/bash_guard.py", "line": 31, "issue": "The commit -n rule scans the entire command including the quoted commit message, so a message containing ' -n' is falsely blocked.", "scenario": "`git commit -am 'wip: handle -n flag'` matches `commit\\b[^|;&\\n]*\\s-[a-zA-Z]*n` and is blocked despite being a legitimate commit; workaround is to reword."},
{"severity": "minor", "file": ".guard-override", "line": 1, "issue": "The human maintenance sentinel is still present in the working tree at finalize, relaxing machinery-write and core.hooksPath rules for every session on this machine.", "scenario": "While .guard-override exists both guards drop their MAINTENANCE rules, so any agent session can rewrite .githooks/, .claude/ and the trust scripts; flagged in the prior ledger and not yet cleared."}
]}
```
