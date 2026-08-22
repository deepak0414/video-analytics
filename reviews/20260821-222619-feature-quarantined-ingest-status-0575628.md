# Agent review — approve

date: 2026-08-21T22:28:05.404179
range: origin/main..HEAD
branch: feature/quarantined-ingest-status
findings: 1

- **minor** `src/va/cli.py:271` — `_cmd_reingest` does not catch the new ValueError raised by `reingest_video` for a quarantined target, so the operator-facing refusal surfaces as an unhandled traceback rather than the clean `error: …`/exit 2 that `_cmd_reprocess` gives for its parallel refusal; only the library function is tested.
  - scenario: Operator runs `va --workdir .va-24h reingest ed09f4ba…` on one of the four repair-quarantined clips: the command dies with a Python traceback (exit 1) instead of printing `error: video … is quarantined … use va remove then a fresh va ingest` and returning 2; scripts checking the exit code / stderr shape treat it as a crash rather than a refusal.

---

## Full review

Review complete. I read the full diff plus the surrounding code it depends on (`ingest.py` dedup ordering, `Catalog.get_or_create`, `manage.remove_video`/`reingest_video`, the NVR `fetch()` cache path, `watch.py`'s dedup handling, `cli.py` handlers, `app.py`/`app.js`, and the `.va-24h` investigation notes that motivated the change).

**What checks out**
- The quarantine dedup sits after the `done` check and before any role work or profile probe, so a plain `va ingest` / `va watch` replay of a quarantined `source_key` is a pure no-op and the watcher's watermark still advances past it (terminal, like `done`).
- `reingest_video` refuses *before* `load_config` and `remove_video`, so the row and status survive; `plan_reprocess` refuses before the generic "not done → use reingest" routing. `stale_report`, `footage_domains`, and `window_anchoring` are already `done`-filtered, so quarantined rows stay out of `va stale`, the mixed-domain warning, and windowed-count anchoring.
- The "`va remove` + fresh `va ingest` = real re-pull" claim holds: ingest `shutil.move`s the fetched file out of `cache/` into the video dir, and `remove_video` (no `keep_media`) rmtree's that dir, so a fresh ingest has no cached bytes to reuse — unlike `reingest`, which deliberately parks them.
- Tests construct their scenarios (status set via `Catalog.set_status`, raw-SQL insert for the repair path) and assert observable behavior; COORDINATION.md and CLAUDE.md document the new status and the web-contract addition; no new env vars/config keys.

**One finding**
- minor — `src/va/cli.py:271`: `_cmd_reingest` doesn't catch the new `ValueError` from `reingest_video`, so `va reingest <quarantined>` exits via an unhandled Python traceback (the message is in it, but it's stderr noise with exit 1) instead of the clean `error: …` / exit 2 that `_cmd_reprocess` gives for the parallel refusal. The refusal was designed as operator-facing guidance, but only the library function is tested. Safe path: wrap the call in `_cmd_reingest` with the same `except ValueError as e: print(f"error: {e}", file=sys.stderr); return 2` and add a `main([... "reingest", ...])` test asserting the non-zero return + the "quarantined" message in stderr.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/cli.py", "line": 271, "issue": "`_cmd_reingest` does not catch the new ValueError raised by `reingest_video` for a quarantined target, so the operator-facing refusal surfaces as an unhandled traceback rather than the clean `error: …`/exit 2 that `_cmd_reprocess` gives for its parallel refusal; only the library function is tested.", "scenario": "Operator runs `va --workdir .va-24h reingest ed09f4ba…` on one of the four repair-quarantined clips: the command dies with a Python traceback (exit 1) instead of printing `error: video … is quarantined … use va remove then a fresh va ingest` and returning 2; scripts checking the exit code / stderr shape treat it as a crash rather than a refusal."}]}
```
