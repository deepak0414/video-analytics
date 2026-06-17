# QA, Traceability & Test-Authoring Plan

Status: **proposed 2026-06-16.** The pipeline (Roles 1–2,4–11 + Retrieval Layer SR.1–6) works
end-to-end on a handful of videos with offline + golden tests. This plan shifts focus from
*building features* to *stabilizing, debugging, and scaling test coverage with human-grounded
truth* — so every future change is regression-tested and failures are diagnosable.

See also: `plan.md` (role/feature roadmap), `web-frontend-plan.md` (the existing web app this
extends), `tests/golden_queries/README.md` (fixture format + the two harnesses),
`video-analytics-solution-architecture.md`.

---

## Why now / the goal

We "vibe-coded" this over a few days. Before the codebase grows further we want:
1. **Debuggability** — when something breaks, a single place to look (trace artifacts).
2. **Scaled, human-grounded testing** — a UI to author golden fixtures whose ground truth
   comes from the user watching the video, not from a model guessing.
3. **Root-cause discipline** — collate failures by root cause and fix the *pattern*, not each
   instance.
4. **Maintainability** — one stable git check-in, then incremental feature commits that each
   ship with tests and run the regression suite.

## Guiding principles (earned the hard way, 2026-06-16 fixture audit)

These are not abstract — each came from a concrete defect this week:
- **Ground truth is the human's; auto-generated queries are HYPOTHESES.** The cobra "indoor
  kitchen" fixture was a vision-agent hallucination (no kitchen exists). Every auto-populated
  query starts `unverified`; user confirmation mints ground truth.
- **A green test can be wrong.** `ferrari-pos-06` "grandstands" *passed* — on a false-positive
  runway frame, while the real grandstands (distant) were never retrieved. So a test must let
  you see *what evidence satisfied it*, to confirm it passed for the RIGHT reason.
- **Determinism ≠ correctness.** A reproducible wrong answer is still wrong; validate against
  ground truth (the deep-scan counted camera-cuts perfectly stably and still wrong).
- **Fix at the capability/calibration level, not per-instance.** Collate failures by root
  cause; one fix (SR.6, a threshold) retires a whole class.
- **Hide the machinery from the QA author.** They give query + truth + (optional) where; the
  backend computes `min_score`, `provenance`, modality, YAML.

## Build order (decided)

**Traceability first** (it is the substrate the other two read), then the authoring UI, then
the failure ledger. CI/CD is documented but deferred (needs dedicated GPU hardware).

```
T0 git baseline ─▶ T1 traceability ─▶ T2 authoring UI ─▶ T3 failure ledger ─▶ (T4 CI/CD, deferred)
```

---

## T0 — Version control & stabilization baseline

**The repo is not under git yet.** This is step zero for "one stable check-in."

| Step | Deliverable | Done when |
|---|---|---|
| T0.1 | `git init` + `.gitignore` | Excludes workdirs (`.va/`, `.va-shots/`), `.venv/`, `__pycache__/`, `*.pyc`, model caches, media blobs, `traces/`. **Commits** all source, configs, docs, and fixture YAMLs (incl. drafts). |
| T0.2 | `scripts/test.sh` (or `make test`) | One command runs the **offline** gate; a flag adds the **golden** gate (`RUN_GOLDEN=1 VA_CONFIG_DIR=run-claude/config GOLDEN_WORKDIR=.va-shots`). Documents the two-tier contract. |
| T0.3 | Initial stable commit | Offline suite green (132) + golden green (83 pass / 1 xfail) recorded in the commit message. |

The two-tier split already exists and is CI-shaped: **offline** (no GPU, runs anywhere, fast)
+ **golden** (real models, GPU, gated). Keep that invariant — every new test declares its tier.

---

## T1 — Traceability (the substrate)

A trace is the single place a human (or Claude, debugging) looks to understand one ingest or
one query end-to-end.

**Design**
- `src/va/runtime/trace.py` — a lightweight `Tracer`. Structured **JSONL** events
  (`{ts, run_id, role, action, summary, details}`) + a rendered human-readable `.md` summary.
  A `run_id` links a query trace to the ingest traces of the videos it touched.
- **Low-friction instrumentation:** a `contextvar`-based "current run" + `trace(role, action,
  summary, **details)` that no-ops when no run is active — so pipeline stages add one-line
  calls without threading a tracer through every signature. Offline tests run with tracing off
  (or to a temp dir) → zero behavior change.
- Not OpenTelemetry (overkill for a POC), but the interface is small enough to back onto OTel
  later if we productionize.

| Step | Deliverable | Done when |
|---|---|---|
| T1.1 | `Tracer` + `trace()` API | JSONL writer + `.md` renderer; contextvar current-run; no-op when inactive; unit-tested offline. |
| T1.2 | **Ingest trace** (`videos/<key>-<slug>/trace/ingest-<runid>.jsonl`) | One event per role: model used (stub vs real), input→output counts ("scene_detector: 71 segments", "embedder: 374 vectors"), timing, and **degradations** ("diarizer FAILED torchcodec → speaker NULL"). Re-uses `IngestResult` + the best-effort except blocks already in `ingest.py`. |
| T1.3 | **Query/ask trace** (`<workdir>/traces/query-<runid>.{jsonl,md}`) | The `QueryPlan` (tiers chosen + why), each tier's hits with scores, **fusion + gate decisions** (formalizes today's `evidence.notes`), SR.6 verifier verdicts, keyframes picked, and the **verbatim text + image list handed to the reasoner** plus its **raw response**. |
| T1.4 | Surfacing | `va trace <run_id>` prints the rendered summary; (optional) a `/trace/<run_id>` page in the web app. |

**Why first:** T2's auto-populate reads what the roles extracted (trace/evidence); T3's
root-causing reads the query trace to see where a bad hit came from; and a query trace is what
turns "the test is green" into "the test is green *for the right reason*."

---

## T2 — Test-authoring web interface ("Ground Truth Studio")

Extend the **existing** FastAPI app + background job queue (`src/va/web/`) and the **idempotent**
ingest (`Catalog.get_or_create` already skips done videos) — do not build a second app. A new
authoring page, clearly separated from the search UI.

**Flow**
1. **Paste a YouTube URL** → enqueue ingest (reused if already `done`; re-ingest only on
   model/role changes, per the workdir-v2 layout).
2. **Auto-populate candidate queries** — two sources, BOTH marked `unverified`:
   - *derive-from-evidence:* grounded in what the roles actually extracted (captions →
     scene queries, transcript lines → speech queries, detected classes → object queries,
     OCR → on-screen-text, actions → action queries).
   - *LLM-augment:* an LLM proposes natural-language queries from the analysis (richer phrasing,
     and Q&A questions).
3. **User confirms/edits against the video.** For each candidate the UI shows *what evidence /
   which frame would satisfy it* (from the trace) so you confirm the RIGHT moment. Actions:
   confirm-as-match / mark-no-match / edit / delete.
4. **Add custom queries** — two kinds:
   - **Retrieval** (deterministic): query + `expect` (match/no_match) + modality (+ optional
     "where" timestamp, + count bounds). Backbone of the suite.
   - **Q&A** (LLM-judged): a question + the user's ground-truth answer, routed through `va ask`;
     an **LLM-as-judge** grades whether the produced answer matches the truth.
5. **Submit** → backend writes **draft golden fixtures**.

| Step | Deliverable | Done when |
|---|---|---|
| T2.1 | Authoring page + API | URL in → ingest job → candidates rendered; add/edit/confirm/delete; submit. Reuses the job queue. |
| T2.2 | Candidate generator | derive-from-evidence + LLM-augment; all `status: unverified`; each carries the satisfying-evidence pointer for human review. |
| T2.3 | Draft fixture writer | Writes `tests/golden_queries/draft/<video_id>.yaml` in the existing format, `provenance: human-confirmed`, technical fields (min_score, etc.) computed server-side. Hidden from the user. |
| T2.4 | **LLM-judge** layer | `roles/answer_judge.py` (Protocol) + stub (substring/number match, offline) + real LLM backend. Grades Q&A; **the judge's verdict + rationale are themselves logged to the trace/ledger** (judges can be wrong → reviewable). |
| T2.5 | Draft harness | A non-blocking `pytest` over `draft/` (gated like the golden harness) so drafts run but don't break the regression gate until promoted. |

**Naming:** "Ground Truth Studio" (captures that the human supplies truth) — rename freely.

---

## T3 — Failure collation & root-cause analysis

Systematizes exactly what the 2026-06-16 audit did by hand.

**Design**
- A **failure ledger** (`testing/failure-ledger.yaml` or a small SQLite table): one entry per
  non-matching query — `{query, fixture, modality, observed, expected, root_cause_tag,
  trace_run_id, status}`.
- **Starter root-cause taxonomy** (grows over time), seeded from this week's defects:
  - `siglip-attribute/negation/composition` → SR.6 VLM-verify (built)
  - `siglip-distant-background-object` → region-aware retrieval (SR.7, not built)
  - `yolo-vocab-gap` → SR.6 VLM-presence (built)
  - `caption-covered-visual-isolated` → multimodal fusion routing
  - `hallucinated-ground-truth` → fixture correction (human)
  - `false-positive-pass` (green for the wrong reason) → tighten fixture (time_range/threshold)
  - `threshold-miscalibration` → calibrate floor

| Step | Deliverable | Done when |
|---|---|---|
| T3.1 | Ledger schema + writer | Harness failures (golden + draft) auto-append entries with their trace ref. |
| T3.2 | Collation view | Group by `root_cause_tag`; a per-tag count + the queries under it. (CLI report and/or web page.) |
| T3.3 | Root-cause review loop | Per tag: decide a higher-level fix (capability / calibration / fixture) → apply once → re-run. |
| T3.4 | **Promotion gate** | A draft fixture moves `draft/` → the regression set once it **passes** OR carries a **confirmed, documented xfail** (real capability gap) — never a false green. |

---

## T4 — CI/CD & dedicated hardware (documented, deferred)

Blocked on getting GPU test machines; captured here so the path is set.

- **Two-tier CI gate:** offline suite on **every push** (any runner, no GPU); golden suite on
  **GPU test machines** (nightly + pre-merge), `RUN_GOLDEN=1`.
- **Feature-commit-ships-with-tests:** each feature branch adds its own tests and must pass the
  full regression (offline always; golden on the GPU runner) before merge.
- **Scaling test generation:** the T2 LLM-augment + T2.4 LLM-judge are the seed of
  "another LLM to create/grade tests at scale" — always with human spot-audit (the cobra
  lesson: never trust an agent's ground truth unaudited).
- **Hardware:** ≥1 dedicated GPU box (the golden/real-model tests + Qwen/SigLIP/Whisper). The
  DGX Spark is the dev box; CI needs its own so runs don't contend.

---

## Open questions / risks
- **LLM-judge reliability** — judges misgrade (and can be led, like the VLM was). Log every
  verdict + rationale; spot-audit; prefer deterministic retrieval assertions where possible.
- **Draft media in git** — fixture YAMLs are committed; the videos they reference are not
  (re-fetched by source_key on ingest). A fixture's video must be re-ingestable from its URL.
- **Trace volume** — JSONL traces are gitignored artifacts; add rotation/retention later.
- **Auto-populate over-trust** — the UI must make `unverified` visually loud so candidates are
  never mistaken for confirmed truth.
