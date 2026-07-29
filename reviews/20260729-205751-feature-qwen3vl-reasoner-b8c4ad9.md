# Agent review — approve

date: 2026-07-29T21:01:25.138880
range: origin/main..HEAD
branch: feature/qwen3vl-reasoner
findings: 3

- **minor** `qwen3vl-reasoner-experiment.md:208` — The closing line still asserts 'Nothing committed to main' in present tense after this commit lands the artifacts on main; the amended section heading qualifies only the table, not this sentence.
  - scenario: A future session reads the file on main, hits this un-qualified sentence, and concludes the adapter/config are uncommitted experiment leftovers eligible for cleanup or re-landing.
- **minor** `run-qwen3vl/config/profiles/dgx-spark.yaml:5` — The profile's dtype: float16 is folded into the reasoner's load dict but qwen3vl_inproc.py:45 ignores it and hard-codes bfloat16, while CLAUDE.md/COORDINATION.md/model-analysis all say FP16.
  - scenario: An operator edits dtype in the profile to change reasoner precision (e.g. debugging memory pressure) and gets a silent no-op; docs mis-describe the loaded precision.
- **minor** `src/va/adapters/reasoner/qwen3vl_inproc.py:44` — Qwen3VLMoeForConditionalGeneration requires a recent transformers, but the [qwenvl] extra has no minimum version pin and no doc states which extra/version the new backend needs.
  - scenario: A fresh clone installs .[qwenvl] with an older transformers that satisfies pyproject, selects run-qwen3vl/config, and the first va ask dies with an ImportError inside the ~6-minute model build path.

---

## Full review

Review complete. This commit lands the Qwen3-VL reasoner experiment (adapter, registry routing, `run-qwen3vl/config`, routing test, docs), and it holds up well. What I verified:

- **Adapter correctness** (`src/va/adapters/reasoner/qwen3vl_inproc.py`): the subclass sets every attribute the inherited `plan()`/`reason()`/`_chat()` use (`_model`, `_processor`, `_fallback`, `device`, `max_new_tokens`); the `qwen3vl::` ModelManager key is distinct from the Role-4 captioner's `qwenvl::` key, so no accidental model sharing; heavy imports stay inside `_build()`, keeping offline tests off transformers.
- **Registry ordering**: `qwen3-vl-*` also matches `startswith("qwen")`, so branch order at `src/va/registry.py:305` is load-bearing — the new test in `tests/test_reasoner_rule.py` stubs the 58 GB class and fails if the branches are swapped (it would get the Qwen2.5 fake). I ran the file: 8 passed. The monkeypatch approach is sound because the registry imports at call time.
- **Parity-claim dependency**: the docs' claim that the `scan_target` backfill fix "merged separately" is real — it's on origin/main (PR #12, `ask.py:215-216`), and this range touches no pipeline code.
- **Config drift**: `run-qwen3vl/config` differs from `run-claude/config` by exactly the reasoner swap plus the weights entry — thresholds, vocab, and every other role are identical.
- **Process hygiene**: COORDINATION.md logged, CLAUDE.md and the model-analysis decision block updated in the same change, `run-qwen3vl/config/` added to critical-paths (golden-verified), and the ground-truth re-validation (bird-ask-01, 1 passed in 447 s) is reported alongside the parity verdict per the determinism rule.
- **Prior review follow-up**: the previous review's stale "Additive + uncommitted" header was fixed in this commit — but one sentence of that finding survived (below).

Three minor findings, none blocking:

1. **Residual stale claim** — `qwen3vl-reasoner-experiment.md:208` still asserts "Nothing committed to `main`" in present tense. The section heading above it was amended to "(LANDED on main 2026-07-29 — table kept as the experiment-time record)", but that qualifier names only the table; the prose line below it survived un-qualified and directly contradicts the landing. Safe path: past-tense the sentence ("At experiment time, nothing was committed…") so the file is self-consistent for a reader on main.
2. **Dead config key + precision mismatch** — `run-qwen3vl/config/profiles/dgx-spark.yaml:5` declares `dtype: float16`, which `Config.role()` folds into the adapter's `load` dict, but `qwen3vl_inproc.py:45` ignores it and hard-codes bfloat16; meanwhile CLAUDE.md, COORDINATION.md, and the model-analysis decision block all describe the setup as "FP16". An operator editing the profile's `dtype` to change reasoner precision gets a silent no-op, and the three docs mis-describe what actually loads. Safe path: either read `load["dtype"]` in `_build()` or note in the profile/docs that the reasoner is fixed at bf16.
3. **Undocumented/unpinned dependency floor** — `qwen3vl_inproc.py:44` imports `Qwen3VLMoeForConditionalGeneration`, which only exists in recent transformers, but the `[qwenvl]` extra (`pyproject.toml:41`) has no minimum version and no doc names which extra the new backend needs. A fresh clone that installs `[qwenvl]` with an older-but-satisfying transformers gets an ImportError at first `va ask` under `run-qwen3vl/config`. Safe path: pin a transformers floor on the extra (or document the required version next to the backend in CLAUDE.md).

Verdict: approve — no critical or major findings.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "qwen3vl-reasoner-experiment.md", "line": 208, "issue": "The closing line still asserts 'Nothing committed to main' in present tense after this commit lands the artifacts on main; the amended section heading qualifies only the table, not this sentence.", "scenario": "A future session reads the file on main, hits this un-qualified sentence, and concludes the adapter/config are uncommitted experiment leftovers eligible for cleanup or re-landing."}, {"severity": "minor", "file": "run-qwen3vl/config/profiles/dgx-spark.yaml", "line": 5, "issue": "The profile's dtype: float16 is folded into the reasoner's load dict but qwen3vl_inproc.py:45 ignores it and hard-codes bfloat16, while CLAUDE.md/COORDINATION.md/model-analysis all say FP16.", "scenario": "An operator edits dtype in the profile to change reasoner precision (e.g. debugging memory pressure) and gets a silent no-op; docs mis-describe the loaded precision."}, {"severity": "minor", "file": "src/va/adapters/reasoner/qwen3vl_inproc.py", "line": 44, "issue": "Qwen3VLMoeForConditionalGeneration requires a recent transformers, but the [qwenvl] extra has no minimum version pin and no doc states which extra/version the new backend needs.", "scenario": "A fresh clone installs .[qwenvl] with an older transformers that satisfies pyproject, selects run-qwen3vl/config, and the first va ask dies with an ImportError inside the ~6-minute model build path."}]}
```
