# Agent review — approve

date: 2026-07-29T21:06:41.239981
range: origin/main..HEAD
branch: feature/qwen3vl-reasoner
findings: 3

- **minor** `src/va/adapters/reasoner/qwen3vl_inproc.py:57` — An unrecognized profile dtype string silently falls back to bfloat16 even though the profile comment says the value is honored from a fixed set.
  - scenario: An operator sets dtype: fp16 (or any typo) in run-qwen3vl/config/profiles/dgx-spark.yaml to debug memory pressure; the adapter silently loads bfloat16 and the misconfiguration is invisible.
- **minor** `src/va/adapters/reasoner/qwen3vl_inproc.py:21` — The accepted model key qwen3-vl-30b-a3b-thinking is undocumented, untested, and has no local-weights profile entry.
  - scenario: A session sets reasoner.model: qwen3-vl-30b-a3b-thinking discovered from the source; with no weights override, from_pretrained starts a ~58 GB HF download on the box whose HF path is documented at ~0.5 MB/s, stalling the run for days.
- **minor** `video-analytics-model-analysis.md:151` — Revisit trigger 3 instructs swapping vlm_captioner to qwen3 in run-qwen3vl/config, but get_vlm_captioner routes all qwen* models to the Qwen2.5-arch QwenCaptioner, which cannot load Qwen3 MoE weights.
  - scenario: A future session executes the recorded qwen3-as-captioner experiment exactly as documented and the ingest crashes in QwenCaptioner._build after the multi-minute load attempt, because the captioner registry lacks a qwen3-vl branch.

---

## Full review

Review complete. This is the final amended landing of the Qwen3-VL reasoner experiment (adapter, registry branch, `run-qwen3vl/config`, routing test, four doc updates, transformers floor, critical-paths entry). What I verified, and what survived:

**Verified clean:**
- **Adapter correctness** (`src/va/adapters/reasoner/qwen3vl_inproc.py`): the subclass skips `super().__init__` but sets every attribute the inherited `plan()`/`reason()`/`_chat()` methods use (`_model`, `_processor`, `_fallback`, `device`, `max_new_tokens`, `model_key`); the `qwen3vl::` ModelManager key is distinct from the captioner's `qwenvl::` key so there is no accidental sharing; heavy imports stay inside `_build()`, so the module imports offline (pillow/pydantic are core deps). `deep_scan.py`'s duck-typed `_chat` access also works via inheritance.
- **Registry ordering**: `"qwen3-vl-…"` also matches `startswith("qwen")`, so the branch order at `registry.py:305` is load-bearing — the new test stubs both classes and would fail if the branches were swapped. I ran it: 8 passed in 0.12s.
- **Prior-round follow-up**: all three findings from the b8c4ad9 review are actually fixed in this commit — the experiment doc's closing line is now past-tensed, `_build()` honors the profile `dtype` (and the profile/docs now consistently say bf16), and the `[qwenvl]` extra carries `transformers>=5` with the verified-version comment.
- **Config drift**: `run-qwen3vl/config` differs from `run-claude/config` by exactly the reasoner swap plus the profile weights/dtype entry — thresholds and every other role identical, so golden comparability holds.
- **Claims vs. reality**: the "backfill fix merged separately" claim is true (`ask.py:215` is on origin/main); parity is reported alongside the human-verified ground truth per the determinism rule; COORDINATION.md is logged; `run-qwen3vl/config/` was added to critical-paths so the golden-verified attestation is CI-enforced on this PR.

**Three minor findings, none blocking:**

1. **Unrecognized `dtype` silently becomes bfloat16** — `qwen3vl_inproc.py:57` uses `dtypes.get(self.dtype_name, torch.bfloat16)`, while the profile comment (`run-qwen3vl/config/profiles/dgx-spark.yaml:22`) promises the value is "Honored". A typo like `dtype: fp16` or `half` loads bf16 with no signal — the same silent-misconfig class the previous round's dtype finding was about, half-closed. Safe path: raise `ValueError` (or at least log) on a dtype name outside the mapping, since the valid set is documented as exactly three values.
2. **Undocumented model key `qwen3-vl-30b-a3b-thinking`** — `qwen3vl_inproc.py:21` adds a second accepted reasoner value that appears in no config, doc, or test, and has no local-weights profile entry, so selecting it silently starts a ~58 GB HF download on a box whose HF path is documented at ~0.5 MB/s. Safe path: document it next to the backend list (with the "needs its own weights entry" caveat) or drop the mapping until the thinking variant is actually evaluated.
3. **The documented qwen3-as-captioner procedure crashes as written** — the new revisit trigger 3 (`video-analytics-model-analysis.md`, Role 11 decision block) says "swap `vlm_captioner` in `run-qwen3vl/config`", but `get_vlm_captioner` (`registry.py:173`) routes any `qwen*` model to the Qwen2.5-arch `QwenCaptioner`, whose `_build` loads `Qwen2_5_VLForConditionalGeneration` — pointing it at Qwen3 MoE weights fails at load. Loud, not silent, but the recorded procedure leads the next session into a known dead end. Safe path: amend the trigger to note the captioner registry needs a `qwen3-vl` branch first (or add the branch when that experiment runs).

Verdict: **approve** — no critical or major findings.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/adapters/reasoner/qwen3vl_inproc.py", "line": 57, "issue": "An unrecognized profile dtype string silently falls back to bfloat16 even though the profile comment says the value is honored from a fixed set.", "scenario": "An operator sets dtype: fp16 (or any typo) in run-qwen3vl/config/profiles/dgx-spark.yaml to debug memory pressure; the adapter silently loads bfloat16 and the misconfiguration is invisible."}, {"severity": "minor", "file": "src/va/adapters/reasoner/qwen3vl_inproc.py", "line": 21, "issue": "The accepted model key qwen3-vl-30b-a3b-thinking is undocumented, untested, and has no local-weights profile entry.", "scenario": "A session sets reasoner.model: qwen3-vl-30b-a3b-thinking discovered from the source; with no weights override, from_pretrained starts a ~58 GB HF download on the box whose HF path is documented at ~0.5 MB/s, stalling the run for days."}, {"severity": "minor", "file": "video-analytics-model-analysis.md", "line": 151, "issue": "Revisit trigger 3 instructs swapping vlm_captioner to qwen3 in run-qwen3vl/config, but get_vlm_captioner routes all qwen* models to the Qwen2.5-arch QwenCaptioner, which cannot load Qwen3 MoE weights.", "scenario": "A future session executes the recorded qwen3-as-captioner experiment exactly as documented and the ingest crashes in QwenCaptioner._build after the multi-minute load attempt, because the captioner registry lacks a qwen3-vl branch."}]}
```
