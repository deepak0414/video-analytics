# Agent review — request_changes

date: 2026-07-29T15:32:04.164731
range: origin/main..HEAD
branch: feature/qwen3vl-reasoner
findings: 4

- **major** `scripts/critical_paths.txt:37` — New real-model config dir run-qwen3vl/config/ is not added to the golden-verified critical-path list, unlike its siblings run-siglip/config/ and run-claude/config/.
  - scenario: A future PR tweaks run-qwen3vl/config (captioner swap per revisit trigger, retriever floors) and merges green with no golden-gate attestation, silently changing real-model behavior that the identical edit in run-siglip/config would have gated.
- **minor** `run-qwen3vl/config/roles.yaml:2` — Header comment copied from the baseline says 'Select via VA_CONFIG_DIR=run-siglip/config', which is wrong for this directory.
  - scenario: An operator follows the comment, exports VA_CONFIG_DIR=run-siglip/config, and runs the qwen2.5-vl-7b reasoner while believing they are exercising the Qwen3-VL backend.
- **minor** `CLAUDE.md:97` — The operative 'Reasoner backends:' list and the config-dir parenthetical are not updated with qwen3-vl-30b-a3b / run-qwen3vl/config, though COORDINATION.md and the model-analysis doc are.
  - scenario: A future session reads CLAUDE.md's backend enumeration, concludes the only local reasoner is qwen2.5-vl-7b, and never discovers or maintains the accepted Qwen3-VL path.
- **minor** `src/va/registry.py:305` — The new qwen3-vl routing branch is correct only via its ordering before startswith("qwen") and has zero offline test coverage of that ordering.
  - scenario: A refactor reorders the reasoner branches; qwen3-vl-30b-a3b silently routes to QwenReasoner and fails only as a wrong-architecture from_pretrained crash minutes into a GPU run under run-qwen3vl/config, which no automated test exercises.

---

## Full review

Review complete. The change is a clean additive experiment landing — the adapter subclass fully replicates the parent's attribute contract, the registry branch ordering (`qwen3-vl` before the generic `qwen` prefix) is correct, the ModelManager cache key is distinct from the Role-4 captioner's, the claimed `ask.py` backfill fix is verified present on main (ffe9609), COORDINATION.md got its log entry, and the offline suite is green (435 passed / 2 skipped). The hardcoded weights path is explicitly flagged per repo convention, and the parity claim is backed by documented golden runs with human-verified ground truth.

Four findings survived verification:

**1. major — `scripts/critical_paths.txt` (bounded-review gap).** The list gates `run-siglip/config/` and `run-claude/config/` behind the `golden-verified` attestation, but the new `run-qwen3vl/config/` real-model config dir — now "Accepted", not merely experimental, per the model-analysis decision block — is not added. Failure scenario: a future PR edits `run-qwen3vl/config` (e.g. the revisit-trigger #3 captioner swap, or a `min_cosine` tweak) and CI goes green with no golden-gate attestation, while the identical edit in a sibling config dir would be blocked — real-model behavior changes silently. Safe path: add `run-qwen3vl/config/  golden-verified` in this same change (the PR already needs the human-reviewed label for `scripts/`).

**2. minor — `run-qwen3vl/config/roles.yaml:2` (stale copied comment).** The header says "Select via `VA_CONFIG_DIR=run-siglip/config`" — wrong for this dir (it perpetuates the same error already in `run-claude/config/roles.yaml`). A reader following the comment selects the qwen2.5 reasoner instead. Safe path: fix the comment to name `run-qwen3vl/config` (and ideally the run-claude copy too).

**3. minor — documentation parity in CLAUDE.md.** CLAUDE.md's operative surfaces — the config-dir parenthetical at line 73 and the "Reasoner backends:" enumeration at line 97 (`rule`, `qwen2.5-vl-7b`, `claude-code`, `claude-api`) — don't mention `qwen3-vl-30b-a3b` / `run-qwen3vl/config`. COORDINATION.md and the model-analysis doc cover it, but CLAUDE.md is what every session loads; the next agent won't know the local-reasoner config exists. Safe path: one line added to the backend list in this change.

**4. minor — no offline coverage of the new registry branch (`src/va/registry.py:305`).** The `qwen3-vl` route is correct only because it precedes `startswith("qwen")`; nothing tests that ordering (no reasoner-routing tests exist at all, so this matches repo precedent — hence minor). Failure scenario: a refactor reorders the branches, `qwen3-vl-30b-a3b` silently routes to `QwenReasoner`, and the failure surfaces only as a wrong-architecture `from_pretrained` crash minutes into a GPU run under the non-default config. Safe path: a small routing test that stubs the adapter module in `sys.modules` and asserts the selected class per model string.

Verdict: request_changes (one major finding — the critical-paths parity gap).

```json
{"verdict": "request_changes", "findings": [
  {"severity": "major", "file": "scripts/critical_paths.txt", "line": 37, "issue": "New real-model config dir run-qwen3vl/config/ is not added to the golden-verified critical-path list, unlike its siblings run-siglip/config/ and run-claude/config/.", "scenario": "A future PR tweaks run-qwen3vl/config (captioner swap per revisit trigger, retriever floors) and merges green with no golden-gate attestation, silently changing real-model behavior that the identical edit in run-siglip/config would have gated."},
  {"severity": "minor", "file": "run-qwen3vl/config/roles.yaml", "line": 2, "issue": "Header comment copied from the baseline says 'Select via VA_CONFIG_DIR=run-siglip/config', which is wrong for this directory.", "scenario": "An operator follows the comment, exports VA_CONFIG_DIR=run-siglip/config, and runs the qwen2.5-vl-7b reasoner while believing they are exercising the Qwen3-VL backend."},
  {"severity": "minor", "file": "CLAUDE.md", "line": 97, "issue": "The operative 'Reasoner backends:' list and the config-dir parenthetical are not updated with qwen3-vl-30b-a3b / run-qwen3vl/config, though COORDINATION.md and the model-analysis doc are.", "scenario": "A future session reads CLAUDE.md's backend enumeration, concludes the only local reasoner is qwen2.5-vl-7b, and never discovers or maintains the accepted Qwen3-VL path."},
  {"severity": "minor", "file": "src/va/registry.py", "line": 305, "issue": "The new qwen3-vl routing branch is correct only via its ordering before startswith(\"qwen\") and has zero offline test coverage of that ordering.", "scenario": "A refactor reorders the reasoner branches; qwen3-vl-30b-a3b silently routes to QwenReasoner and fails only as a wrong-architecture from_pretrained crash minutes into a GPU run under run-qwen3vl/config, which no automated test exercises."}
]}
```
