# Traceability Plan

Status: **proposed 2026-06-17.** Deep-dive expansion of milestone **T1** in
`qa-and-traceability-plan.md` (which keeps the broader QA roadmap: authoring UI, failure
ledger, CI/CD). This document is traceability *only*, and is the build spec for it.

Goal: **when something breaks — in ingestion or in a query/ask — there is a single artifact
that says what each stage did, why it degraded, and exactly what was handed to the reasoner.**
A new developer (or Claude, debugging) reads one file instead of re-deriving the pipeline.

---

## The problem, grounded in the current code

**1. Silently swallowed failures (the #1 hole).** `pipeline/ingest.py` runs each optional role
in a best-effort block that discards the cause of failure:
```python
try:
    lines = get_ocr_reader().read(...)
    ocr_lines = len(lines)
except Exception:
    ocr_lines = 0     # WHY is gone: "no on-screen text" vs "RapidOCR segfaulted on aarch64"
```
There are **six** such blocks (caption, transcript+diarize, OCR, detect+track, actions,
text-index). When diarization silently degraded to `speaker=NULL` (the torchcodec bug), nothing
recorded it. Capturing the exception+traceback in these blocks — **without changing behavior**
(still best-effort, still `count=0`, still never aborts) — is the highest-value change here.

**2. Opaque reasoning.** `ask()` builds evidence, renders it to text via `render_evidence()`,
attaches keyframes, and calls the reasoner — but none of that (the verbatim prompt, the raw LLM
response, why deep-scan/self-escalation fired) is persisted. A wrong answer can't be
reconstructed after the fact.

**3. Two clean run boundaries.** `ingest()` and `ask()`/`query()` are each single functions, so
one context manager at the top brackets a whole run — no threading state through the ~10 ingest
stages or the retrieval internals.

---

## Design decisions

| Decision | Choice | Rationale / rejected alternatives |
|---|---|---|
| **Activation** | `VA_TRACE` env var, **default OFF**. `1`→on; `va serve --trace` sets it for the server process. | Opt-in keeps normal runs clean (no files per request); turn on to debug/reproduce. Default-off = literally zero work (the run context manager yields a no-op tracer, contextvar stays `None`, every `trace()` call short-circuits). **Known tradeoff:** an unexpected break with tracing off isn't captured — reproduce with `VA_TRACE=1` (ingest/retrieval are deterministic; a debug server runs `--trace`). |
| **Instrumentation** | `contextvar` "current run" + `trace(role, action, summary, **details)` that no-ops when inactive. | One-line calls, no signature churn across stages. Rejected: threading a `tracer` arg everywhere (verbose); stdlib `logging` (string-y, correlation awkward). **Thread caveat:** the web job queue runs jobs in worker threads, so the run is established *inside* `ingest()/ask()` (the working thread), never inherited across threads. |
| **Format** | A **single human-readable `.trace` file per run** (the file IS the view): short timeline lines, structured details as indented JSON, and big verbatim payloads (reasoner input/output, tracebacks) as REAL-newline blocks delimited by `----- <field> -----`. Written incrementally (append-per-event, crash-safe) and still lightly parseable (`load_events`). | Readability-first (per the user): open one file and read it top-to-bottom — no `jq`/word-wrap, no escaped `\n` walls, no companion files. JSONL was rejected because the verbatim reasoner dump becomes one unreadable escaped line. |
| **Location** | `<workdir>/traces/<kind>-<run_id>.jsonl` (workdir-level, not per-video). | The per-video dir doesn't exist until *mid*-ingest (after fetch) — workdir-level sidesteps that and writes incrementally from event #1. Already gitignored. Video id is an event field. |
| **Run id** | `YYYYMMDD-HHMMSS-<short>` (e.g. `20260617-043105-a1b2`). | Readable, sortable, unique; correlates a query run to the videos/ingest it touched. |
| **Retention** | Keep **everything** by default; clean up via **`va trace prune`** (`--keep N` / `--older-than 7d` / `--all`). | No silent data loss; cleanup is one command, never manual file-hunting. |
| **Safety** | Trace writes are **best-effort — never raise**; trivial overhead when on. | A trace is a debug aid, not the critical path. Default-off + tmp-workdir tests ⇒ **zero change** to the 132 green offline tests. |

### Where trace calls live (the instrumentation rule)

Tracing is **call-based, not inherited** — no module extends or implements anything from the
trace module; the pipeline simply *calls* `trace(...)`. The dependency is one-way
(`pipeline → runtime/trace`, never the reverse), so role **Protocols and adapters stay pure**.

- **Default: instrument at the ORCHESTRATION layer** (`pipeline/ingest.py`, `ask.py`,
  `retrieval.py`). The orchestrator already holds the try/except blocks, the counts, and the
  model choice — it emits one event per role boundary (model, in→out counts, timing, and the
  exception+traceback on failure). ~18 of ~20 adapters never import `trace`.
- **Exception: a role with rich, failure-prone INTERNALS may emit its own events** — because
  `trace()` is a no-op free function (no base class, no Protocol, no hard dependency), an
  adapter can add a one-line `from va.runtime.trace import trace` where its internal steps are
  worth narrating. Sanctioned candidates: **diarization** (the 4-gated-model + torchcodec-bypass
  chain) and the **deep-scan sweep** (per-frame observations). Everywhere else, orchestrator-level.
- A role's *activity* is therefore ALWAYS traced (events are tagged with a `role:` field); only
  the *trace-call site* differs — boundary (orchestrator) by default, internal (adapter) for the
  two complex roles.

### Event block (one per event, in the readable `.trace` file)
```
# trace · ask · 20260617-172601-dd72 · 2026-06-17T17:26:01+00:00

[09] reasoner/input · 5 evidence items + 3 keyframes -> reasoner
     {"keyframes": ["…/0.png", "…/1.png"]}
----- reasoner_input -----
[E1] (caption @ 0.0-3.0s, …) a gray scene
[E3] (transcript @ 1.0-2.0s, …) the red box is here
----- end reasoner_input -----
[10] ✗ ocr/failed · RapidOCR init segfault
----- traceback -----
Traceback (most recent call last): …
----- end traceback -----
```
`[NN] [marker] role/action · summary` is the one line a human skims (`✗`=error, `!`=warn); the
indented JSON line is small structured detail (counts, scores, model id); big payloads —
**exception traceback** and (for asks) the **verbatim reasoner input/output** — are their own
real-newline blocks. `load_events()` parses it all back for tooling.

---

## Instrumentation map (what gets recorded)

**Ingest run** — one event per stage, in pipeline order:
`resolve/dedup` → `fetch` (title, duration, path) → `scene_detect` (N segments + detector) →
`caption` (N + model **or the exception**) → `stt` + `diarize` (N lines, N speakers **or
exception** — catches the silent `speaker=NULL`) → `ocr` → `detect` + `track` → `actions` →
`visual_embed` (N vectors + model) → `text_index` → final `summary` (the whole `IngestResult`).
Plus `model_load` events from `ModelManager` (so a slow first run shows "SigLIP cold-load 3.2s").
A deduped re-ingest logs a single "skipped — already done."

**Query / ask run:**
`plan` (LLM call #1 + the rule-floor-merge decision — *why* deep-scan got forced) → `retrieve`:
per-tier `gather` (visual + **SR.6 verify verdicts**, semantic-text or lexical-fallback,
structured) → `fuse` (scores) → `gate` (drops + reasons = the `evidence.notes`) → `keyframes`
(which moments) → **`reasoner_input`** (verbatim `render_evidence` text + keyframe paths) →
**`reasoner_output`** (raw LLM response) → `answer`, plus the branches `deep_scan` and
`self_escalation`. A bad answer becomes fully reconstructable: you see the exact evidence the
reasoner received and what it did with it.

---

## Surfacing
```
va trace --last                 # rendered summary of the most recent run
va trace <run_id>               # a specific run
va trace list                   # recent runs: kind, video, when, #warnings
va trace prune --keep 50        # cleanup: also --older-than 7d / --all
```
The rendered view leads with **degradations** (`level=error/warn`), then the stage timeline with
timings, then (for asks) the reasoner-input dump. A web `/trace/<id>` viewer is **deferred** to
the web golden-query-harness work (it'll sit beside the search/authoring UI).

---

## Milestones

**Build order: TR.1 → TR.3 → TR.2 → TR.4.** TR.2 and TR.3 are independent (both depend only on
TR.1's API); TR.3 (query/ask — the "why did that answer happen" surface) is prioritized for the
current QA focus, with TR.2 (ingest) after. The only TR.3↔TR.2 link is TR.4's optional
ingest↔query pointer, which degrades gracefully if ingest tracing isn't wired yet.

| Step | Deliverable | Done when |
|---|---|---|
| **TR.1** | Tracer core: `runtime/trace.py` (`Tracer`, `traced_run(kind, workdir)` ctx-mgr gated by `VA_TRACE`, `trace()` no-op-when-inactive, readable `.trace` writer) + `load_events`/`render_trace` + `va trace` CLI (list / show / `--last` / prune). | Offline tests pass: a `VA_TRACE=1` synth run writes a readable trace; default-off writes nothing; prune works; full suite green (no new files when off). |
| **TR.2** | Ingest instrumentation — stage events + `model_load` events + **exception capture in the 6 best-effort blocks**. | A forced stub-role failure appears as an `error` event with traceback; counts/timings present; **behavior unchanged** (still best-effort, still completes). |
| **TR.3** | Query/ask instrumentation — plan (+ rule-floor merge), retrieve tiers (gather/verify/fuse/gate), keyframes, **`reasoner_input` verbatim + `reasoner_output` raw**, answer, deep-scan, self-escalation. | A trace reconstructs an ask end-to-end (evidence in → prompt → response → answer); deep-scan and self-escalation branches are visible. |
| **TR.4** | Polish — ingest↔query linking (catalog records each video's last ingest `run_id` so a query trace points at the ingests it used) + prune ergonomics. *(Web viewer handed to the web milestone.)* | A query trace names the ingest runs of the videos it touched; prune retention documented. |

## Testing strategy
- **Offline unit tests** (`tests/test_trace.py`): `.trace` write + `load_events` round-trip; the verbatim block is real-newline text (not escaped); **no-op when `VA_TRACE` unset**; best-effort — a write error never raises.
- **Offline e2e**: `VA_TRACE=1` + synth ingest → assert events for `scene_detect`/`visual_embed`/`text_index` + the `summary`; monkeypatch a stub role to raise → assert an `error` event with traceback is captured *and ingest still completes*.
- **Default-off invariant**: the existing 132 offline tests stay green and write **no** trace files (they never set `VA_TRACE`).

## Open questions / future
- **`--trace` on `ingest`/`query`/`ask` too?** TR.1 ships the `VA_TRACE` env gate + `serve --trace`; per-command `--trace` flags are a trivial add if wanted.
- **Trace → OpenTelemetry** later, if productionized — the event schema is deliberately OTel-shaped (run_id≈trace_id, seq/role≈spans).
- **Redaction** — traces may contain transcript/caption text; they're gitignored local artifacts, but note before ever shipping them off-box.
