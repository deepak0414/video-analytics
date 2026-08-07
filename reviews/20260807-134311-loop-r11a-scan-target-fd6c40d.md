# Agent review — approve

date: 2026-08-07T13:50:14.404880
range: origin/main..HEAD
branch: loop/r11a-scan-target
findings: 1

- **minor** `src/va/pipeline/ask.py:219` — The new `or derive_scan_target(question)` backfill branch has no test coverage — every existing test reaches the rule-floor (:206) or self-escalation (:258) call site instead, so the LLM-planner path this change was written for is exercised only by the real-model golden run on run-claude/config.
  - scenario: A qwen3-vl or claude plan sets needs_deep_scan=True with params dropped by JSON salvage on a query the closed `_DEEP_SCAN` regex misses (e.g. "track each appearance of the delivery van"): this line is the only thing that supplies a target, and since R11.a a missing target means the sweep is silently skipped rather than run on a wrong subject. A refactor of derive_scan_target or of the rule_plan lookup could break it with the offline suite fully green. Safe path: add a planner double returning QueryPlan(query=q, needs_deep_scan=True) with empty params on a rule-regex-missing query, and assert plan.params['scan_target'] == derive_scan_target(q) and that a deep_scan_count item lands; note in the digest that run-qwen3vl/config was not re-run for this branch.

---

## Full review

I reviewed `origin/main..HEAD` (one commit, `fd6c40d`) — the R11.a change: `DEFAULT_TARGET` deletion, `derive_scan_target()`, the footage-profile deep-scan veto, per-cause skip reasons, the `canonical_key` all-noise hash, and the vetoed-escalation no-op guard.

**What I verified rather than assumed:**

- **`derive_scan_target` behavior** — exercised it directly: the `_SUBJECT_NOUNS` fallback only fires when the plain filter empties, so `"count the number of visits"` → `"the visits"` (no counting-word injection) while `"how many times does the color change…"` → `"the color"` and pronoun-only → `None`. No canned content survives anywhere; every target is built from the user's own tokens.
- **`canonical_key` collision fix** — confirmed `"the color"`/`"the wearing"` now hash to distinct buckets while `"the girl dress"`/`"dress the girl"` still share `dre-girl`. The trade (miss > merge) is the correct direction and the previously-cached `"the main person outfit"` key is unchanged at `outfit`.
- **Profile gate reach across configs** — `deep_scan: "off"` is present in `security.yaml` in *all four* config dirs (`config/`, `run-siglip`, `run-claude`, `run-qwen3vl`), so the veto isn't inert under the real-model combinations. `config_for(video.profile, …)` is the same record==reality seam `stale.py`/`text_index.py`/`reprocess.py` already use.
- **No golden regression from the gate** — only `birdfeeder_0413_1405.yaml` and `eiLeBJUf1iE.yaml` carry `ask_questions:`, both A-EV/generic; the nine `nvr0801_*` fixtures have none, so the security veto can't turn a golden ask red.
- **Contract break is contained** — `run_deep_scan`'s new `(result, skip_reason)` tuple has exactly one caller (`ask.py:71`), and it's logged in COORDINATION.md.
- **No test deletion or weakening** — `--numstat` shows `tests/test_deep_scan.py 100/0`; the pre-existing escalation tests still assert `calls["reason"] == 2` on the happy path, so the new no-op guard didn't relax them.
- **`ran`/`skipped` trace and the re-reason guard** — `deep_scan_count` is produced only by `deep_scan_video`, never by `retrieve()`, so `any(i.modality == "deep_scan_count" …)` is a sound proxy for "the sweep actually happened" at both `ask.py:231` and `:266`. No consumer of `plan.needs_deep_scan` assumes "ran".
- **Plan conformance** — R11.a's Done-when ("under `security` the outfit target can no longer fire; A-EV golden-ask harness still green") is met and the golden evidence is recorded in-branch (COORDINATION.md: dresses-ask-01 330 s, bird-ask-01 on retry).

I did **not** run the suite: another session has `.venv/bin/pytest -q` live (PID 497891, amending this same commit), and piling a second run on it is the exact failure CLAUDE.md's 2026-08-04 lesson records.

One suspicion dissolved on reading: I expected `config_for` to raise for a recorded profile absent from an alternate `VA_CONFIG_DIR`, but all config dirs carry both profiles, and `_deep_scan_into`'s `except Exception` degrades it to a note regardless.

**One minor finding** — the `or derive_scan_target(question)` backfill added at `ask.py:219-220` is the only new branch with zero coverage, and it's precisely the LLM-planner combination the change exists for. Existing tests reach the rule-floor site (`:206`) and the self-escalation site (`:258`), never this one.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/ask.py", "line": 219, "issue": "The new `or derive_scan_target(question)` backfill branch has no test coverage — every existing test reaches the rule-floor (:206) or self-escalation (:258) call site instead, so the LLM-planner path this change was written for is exercised only by the real-model golden run on run-claude/config.", "scenario": "A qwen3-vl or claude plan sets needs_deep_scan=True with params dropped by JSON salvage on a query the closed `_DEEP_SCAN` regex misses (e.g. \"track each appearance of the delivery van\"): this line is the only thing that supplies a target, and since R11.a a missing target means the sweep is silently skipped rather than run on a wrong subject. A refactor of derive_scan_target or of the rule_plan lookup could break it with the offline suite fully green. Safe path: add a planner double returning QueryPlan(query=q, needs_deep_scan=True) with empty params on a rule-regex-missing query, and assert plan.params['scan_target'] == derive_scan_target(q) and that a deep_scan_count item lands; note in the digest that run-qwen3vl/config was not re-run for this branch."}]}
```
