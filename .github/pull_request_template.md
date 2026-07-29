## What & why

<!-- One paragraph. Link the plan doc + task IDs this implements. -->

## Done-when mapping

<!-- Each plan "Done when" item this PR claims, one line each. -->

## Evidence

<!-- REQUIRED and CI-checked (P4: evidence over assertion). Paste REAL output —
     never write "tests pass" without the numbers. `/verify` generates this block. -->

```text
EVIDENCE: offline suite
<paste: .venv/bin/pytest -q tail>
```

- [ ] Golden gate run — required if `src/va/adapters/`, `src/va/pipeline/` or any
      `config/` was touched (then also apply the `golden-verified` label):

```text
EVIDENCE: golden gate (or "not required because ...")
```

## Review

- [ ] Agent review ledger committed under `reviews/` (post-commit writes it)
- [ ] Critical paths touched? → the user has read them and applied `human-reviewed`
