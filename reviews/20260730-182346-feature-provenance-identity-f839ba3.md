# Agent review — approve

date: 2026-07-30T18:30:52.360927
range: origin/main..HEAD
branch: feature/provenance-identity
findings: 1

- **minor** `src/va/cli.py:234` — The va stale remedy footer tells the user to pass `--fps <the fps shown above>` unconditionally, but videos with unknown recorded fps (recorded_fps=None, i.e. every pre-PROV-3 video) print no fps note, so the instruction references information the report never showed.
  - scenario: A user upgrades an existing .va-shots workdir to this build and runs `va stale`: every video is listed stale (no provenance rows) with no `[ingested at fps=...]` annotation, yet the footer directs them to re-run reingest with 'the fps shown above' — there is none, so they either guess or fall back to the 1.0 default without realizing it may change the frame density Roles 2/5/6/7 saw.

---

## Full review

Review complete. This range (4 commits, provenance pillar A: PROV-1 fingerprint helper → PROV-2 table/migration/store → PROV-3 ingest stamping → PROV-4 `va stale`) is solid work. I verified the suspicious spots and most dissolved on inspection:

**Checked and clean:**
- **Failed-role bookkeeping in `ingest.py`**: every one of the 10 `PROVENANCE_ROLES` is either covered by a `failed.add/update` handler or (scene detector, visual embedder) aborts the ingest entirely, in which case the video never reaches `done` and `stale_report` skips it. The outer-transcript failure correctly marks both `speech_to_text` and `speaker_diarizer`; counts keys match `PROVENANCE_ROLES` exactly.
- **Pinned-config claim**: registry getters (`get_scene_detector()` etc.) each call `load_config()` fresh, so the "mid-ingest edit degrades to false-stale, never missed-stale" argument in the comment is factually correct.
- **The "unknown model" asymmetry** (`role_fingerprint` → `"unknown"` for an unconfigured role while the registry falls back to real stubs) is consistent between the stamp side and the check side, is explicitly documented as a limitation, and the default config sets every role — no wrong behavior reachable.
- **Deep-scan cache keying**: the function-level imports mean the monkeypatched tests genuinely exercise the production path; an identity mapping (`reasoner=None`/rule reasoner) produces an empty `mapping` and is never cached, so it can't poison the reasoner-fingerprinted `map_key`. The one-time `.va-shots` cache invalidation is logged in COORDINATION.md.
- **Migration**: v2 migration + rollback test generalization is a strengthening, not a weakening; `va remove` purges the new table with a test; COORDINATION.md logs the schema bump, the stamping, and the read helper.
- **Docs/commit messages**: `va stale` is in CLAUDE.md, `--role` is self-documenting via `choices`, the PROV-4 scope cut (`va provenance <video>` deferred) is recorded in the plan rather than silent, and all finalized commit bodies are self-sufficient with plan IDs trailing.
- Offline suite: **487 passed, 2 skipped** (58s).

One minor finding survived. Also a non-finding note for the PR stage: this diff touches `schema.py`, `cli.py`, `ingest.py` (**human-reviewed** label) and `src/va/pipeline/` (**golden-verified** label), so the PR will need both attestations — and the golden `ask` run will legitimately re-sweep once because of the intentional cache-key change.

**Minor — `src/va/cli.py:234`**: the stale-report footer unconditionally instructs `va reingest <video> --fps <the fps shown above>`, but the most common stale population — pre-PROV-3 videos, which have *no* provenance rows at all (exactly what an existing `.va-shots` workdir contains on first use of this feature) — prints no fps note, so the remedy references information the report didn't show. Safe path: when `recorded_fps` is None for any listed video, say so explicitly ("fps unknown for videos without a note — pass the fps you originally ingested with, default 1.0") instead of pointing at an absent value.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/cli.py", "line": 234, "issue": "The va stale remedy footer tells the user to pass `--fps <the fps shown above>` unconditionally, but videos with unknown recorded fps (recorded_fps=None, i.e. every pre-PROV-3 video) print no fps note, so the instruction references information the report never showed.", "scenario": "A user upgrades an existing .va-shots workdir to this build and runs `va stale`: every video is listed stale (no provenance rows) with no `[ingested at fps=...]` annotation, yet the footer directs them to re-run reingest with 'the fps shown above' — there is none, so they either guess or fall back to the 1.0 default without realizing it may change the frame density Roles 2/5/6/7 saw."}]}
```
