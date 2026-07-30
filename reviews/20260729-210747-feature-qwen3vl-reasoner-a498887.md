# Agent review — approve

date: 2026-07-29T21:11:43.310452
range: origin/main..HEAD
branch: feature/qwen3vl-reasoner
findings: 1

- **minor** `src/va/adapters/reasoner/qwen3vl_inproc.py:1` — Adapter docstring and the registry.py:306 comment still label the backend 'EXPERIMENTAL / Experiment only' although this same commit records the experiment as CLOSED/Accepted at golden-set parity in video-analytics-model-analysis.md and COORDINATION.md.
  - scenario: A future agent session reading only the code concludes the qwen3-vl backend is unvalidated scratch from an open experiment and removes or deprioritizes it, contradicting the accepted Role-11 decision; reword to point at the decision block in video-analytics-model-analysis.md.

---

## Full review

The full offline suite is green (436 passed / 2 skipped, including the two new routing tests), and the diff introduces no new env vars or CLI flags. I've now verified everything I flagged as suspicious during reading; here is the review.

## Review: `origin/main..HEAD` (a498887, Qwen3-VL reasoner landing)

**What I verified (suspicions that dissolved):**

- **Routing order** — `qwen3-vl` must be checked before the generic `startswith("qwen")` branch in `registry.py:305`; it is, and the new test `test_registry_routes_qwen3_before_generic_qwen_prefix` genuinely fails if the branches are swapped (`"qwen3-vl-…"` matches `"qwen"`). The stubbing via monkeypatch is sound because `get_reasoner` imports the class at call time.
- **Config merge** — `configuration.py` folds top-level profile keys first, then per-model overrides, so the model-level `dtype: bfloat16` correctly wins over the profile's top-level `dtype: float16`. The profile comment ("bfloat16|float16|float32; cpu forces float32") matches the adapter code exactly, including the loud `ValueError` on a typo'd dtype.
- **Config-dir drift** — `run-qwen3vl/config` is byte-identical to `run-claude/config` except the reasoner model line and the added `qwen3-vl-30b-a3b` weights/dtype entry, so golden comparisons between the two configs differ only in the variable under test.
- **Contract/coordination** — COORDINATION.md has the dated log entry; the registry branch is additive; no schema or contract files change in this range (the modified `schema.py`/`test_trace_link.py` in the working tree are uncommitted, outside this range).
- **Gates** — `run-qwen3vl/config/` was added to `critical_paths.txt` under `golden-verified`, and the golden-run evidence (84 pass / 1 xfail, ask 2/2 after the separately-merged backfill fix, re-validated in 447 s) is recorded in the experiment doc and model-analysis decision block. The counts reconcile with CLAUDE.md's 83+1+2 = 86 fixtures.
- **Docs parity** — CLAUDE.md documents the new backend and `VA_CONFIG_DIR=run-qwen3vl/config`; the `transformers>=5` floor is explained in `pyproject.toml` with the verified version. The known foot-guns (HF-download fallback if `weights:` is absent, one-golden-run-at-a-time memory ceiling) are flagged in comments/docs rather than silent.

**One finding (minor):** the adapter docstring and registry comment still describe this backend as "EXPERIMENTAL / Experiment only: evaluate whether…" (`qwen3vl_inproc.py:1-8`, `registry.py:306`), while this same commit closes the experiment as **Accepted at parity** in `video-analytics-model-analysis.md` and COORDINATION.md. A future session reading only the code could treat a decided, golden-verified backend as unvalidated scratch and deprioritize or remove it. Safe path: reword the docstring/comment to "validated local backend; decision + revisit triggers in video-analytics-model-analysis.md" in the finalize amend.

The `need_agent_review:` subject is exempt from the commit-clarity rule as a conversation-phase artifact.

**Verdict: approve** — no critical or major findings.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/adapters/reasoner/qwen3vl_inproc.py", "line": 1, "issue": "Adapter docstring and the registry.py:306 comment still label the backend 'EXPERIMENTAL / Experiment only' although this same commit records the experiment as CLOSED/Accepted at golden-set parity in video-analytics-model-analysis.md and COORDINATION.md.", "scenario": "A future agent session reading only the code concludes the qwen3-vl backend is unvalidated scratch from an open experiment and removes or deprioritizes it, contradicting the accepted Role-11 decision; reword to point at the decision block in video-analytics-model-analysis.md."}]}
```
