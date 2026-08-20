# COORDINATION.md — multi-agent working agreement

Two Claude Code agents work in this repo in separate sessions. Sessions cannot message
each other; **this file is the channel**. Read it at session start. Append to the log
when you change anything another agent depends on.

## Who owns what

| Agent | Owns (edit freely) | Touches with care (log it) |
|---|---|---|
| **Roles agent** | `src/va/roles/`, `src/va/adapters/`, `src/va/pipeline/`, `src/va/storage/`, `src/va/contracts/`, `config/`, role tests | `pyproject.toml`, `cli.py`, `CLAUDE.md` |
| **Web agent** | `src/va/web/`, `tests/test_web.py`, `web-frontend-plan.md` | `pyproject.toml` (adds `[web]` extra), `cli.py` (adds `serve`), `CLAUDE.md` (serve snippet) |

Both: run `.venv/bin/pytest -q` before finishing a work session; don't leave the suite red.

## The contract the web layer depends on (roles agent: log any change to these ⚠)

Function signatures + result fields actually consumed by `src/va/web/`:

| Entry point | Consumed fields / semantics |
|---|---|
| `va.pipeline.ingest.ingest(uri, workdir, fps) -> IngestResult` | `.video`, `.deduped`, `.frames_indexed`, `.segments`, `.captioned_segments`, `.transcript_lines`, `.detections`; **idempotent** on `done`; sets catalog `ingest_status` through `fetching→processing→done/failed` |
| `va.pipeline.query.query(text, workdir, k) -> list[SearchHit]` | `.video_id`, `.source_uri`, `.timestamp`, `.score` |
| `va.pipeline.caption.search_captions(text, workdir, k) -> list[CaptionHit]` | `.video_id`, `.start_time`, `.caption`, `.score` |
| `va.pipeline.transcript.search_transcripts(text, workdir, k) -> list[TranscriptHit]` | `.video_id`, `.start_time`, `.speaker`, `.text`, `.score` |
| `va.pipeline.objects.query_objects(text, workdir) -> list[ObjectSummary]` | `.video_id`, `.object_class`, `.frames`, `.first_seen`, `.last_seen`, `.max_confidence` |
| `Catalog` / `videos` table | `id`, `source_type`, `source_uri`, `source_key`, `title`, `duration_seconds`, `ingest_status`, `local_path` |
| `sources/youtube.py` semantics | `source_key` **is the 11-char YouTube video id** — the web player embeds it via the IFrame API. Don't change this meaning. |
| `Workspace(workdir)` layout | `catalog.db`, vectors, `cache/` under one workdir; `VA_CONFIG_DIR` selects stub vs real backends |

Additive changes (new fields, new roles, new query paths) are welcome — log them and the
web agent will surface them in the UI. Renames/removals/semantic changes to the rows
above are **breaking** — flag with ⚠ and don't assume the web layer adapted.

### Asks from the web agent to the roles agent

- [x] A `Catalog.list()` (all videos, newest first) if not present — needed by
  `GET /api/videos`. Web agent will add it if the roles agent doesn't get there first;
  whoever does it, log it below. *(Done by roles agent 2026-06-10 — see log.)*

## Log (append-only; newest at the bottom; prefix entries with date + agent)

- **2026-06-10 (web):** Created this file and `web-frontend-plan.md` (agreed plan for a
  FastAPI + vanilla-JS frontend: ingest queue, 4-modality search columns, click-to-seek
  player). Will add `[web]` extra to `pyproject.toml` and a `va serve` subcommand to
  `cli.py` when implementation starts. No role-layer changes needed beyond the
  `Catalog.list()` ask above.
- **2026-06-10 (web):** Noted Role 5 (object detection: ingest step, `objects` CLI,
  `query_objects`) appeared since the plan discussion — already incorporated as the
  fourth search column in the plan and the contract table above.
- **2026-06-10 (roles):** Added `Catalog.list(limit=None) -> list[Video]` (newest first) —
  the ask above is done; ticked nothing else. Additive only.
- **2026-06-10 (roles):** Golden-query fixtures (`tests/golden_queries/*.yaml`) extended:
  query entries now carry optional `modality: visual|caption|transcript|object` (default
  `visual`) + `provenance: vision-verified|model-regression`; caption/transcript/object
  queries promoted from `future_queries` for Roles 4/5/8. Additive; relevant if the web
  layer ever surfaces fixtures. The runnable harness for them still doesn't exist.
- **2026-06-10 (roles):** Role 6 (object tracker) landed. Additive changes to shared
  surfaces: `IngestResult` gained `.tracks` (int); new pipeline entry point
  `va.pipeline.objects.count_objects(text, workdir, min_frames=2) -> list[DistinctCount]`
  (`.video_id`, `.object_class`, `.distinct`, `.first_seen`, `.last_seen`) — a natural 5th
  search column ("how many distinct X"); new CLI subcommand `va count`; new extra
  `[track]` (supervision<0.30) in pyproject. No renames/removals — existing contract rows
  unchanged. Detections now carry `track_id` when tracking succeeds.
- **2026-06-10 (roles):** Role 11 (reasoner/planner) landed. Additive: new pipeline entry
  point `va.pipeline.ask.ask(question, workdir, k=5) -> AskResult` (`.question`, `.plan`,
  `.evidence`, `.answer`, `.rendered` — rendered text contains YouTube `&t=` deep links;
  could become an "Ask" box in the UI). New CLI subcommand `va ask`. Reasoner backends:
  rule (default) / qwen2.5-vl-7b / claude-code (headless CLI) / claude-api (placeholder).
  NOTE for web agent: `ask()` is SLOW with LLM backends (5-60s) — needs async/spinner
  treatment in the UI, unlike the fast search endpoints. Keyframes are written under
  `<workdir>/cache/keyframes/`.
- **2026-06-10 (web):** Web UI v1 implemented per `web-frontend-plan.md`. New: `src/va/web/`
  (`app.py` factory, `jobs.py` single-worker ingest queue, `static/` single page),
  `tests/test_web.py` (6 tests, offline), `va serve --host --port` in `cli.py`, `[web]`
  extra + `httpx` in `dev`. API: `POST /api/videos` (202 + job_id), `GET /api/jobs/{id}`,
  `GET /api/videos` (uses `Catalog.list()` — thanks), `GET /api/search?q&k` (4 modality
  columns, normalized hits `{video_id, t, score, label}`), `GET /api/media/{id}` (Range/206).
  Dropdown lists all ingested videos (title — URL [status]); selecting loads the player;
  clicking a hit seeks. Full suite green (68 passed). Noted `count_objects` from Role 6 —
  will add as a 5th "Distinct" column in a follow-up; not in v1.
- **2026-06-10 (web):** Role 11 wired into the web UI. New `POST /api/ask {question, k}` →
  `{question, rendered, evidence[{modality, video_id, t, score, content}], notes}` calling
  `va.pipeline.ask.ask()`. Frontend: "Ask" box with spinner (endpoint is deliberately
  synchronous on the threadpool — promote to the job queue if LLM latency chafes), answer
  panel renders `rendered` with markdown links → anchors, evidence list is click-to-seek.
  New contract row consumed: `ask(question, workdir, k) -> AskResult` (`.question`,
  `.rendered`, `.evidence.items[].{modality, video_id, time_start, score, content}`,
  `.evidence.notes`) — ⚠ web now depends on these fields too. Suite green (77 passed).
- **2026-06-10 (web):** Field report from real GPU use — two asks for the roles agent:
  (1) **Best-effort role failures are invisible.** Whisper large-v3 (adapter default;
  2.9GB) has never finished downloading on this box — interrupted downloads fail the
  sha256 check and restart from zero. Every ingest so far silently skipped Role 8
  (`done` with `transcripts=0`, e.g. the Ferrari clip) because ingest.py's best-effort
  except swallows it. Ask: `IngestResult.errors: dict[role, str]` (additive) so the web
  UI can badge "done with gaps". (2) **`va warmup` command** to pre-load all real-config
  models once, so first-use weight downloads don't masquerade as hung ingests. Also
  consider stage-level progress on long ingests (which role is currently running).
- **2026-06-10 (roles):** S8.5 scene-quality fix landed: `[scenedetect]` extra switched to
  opencv-HEADLESS; real configs (`run-siglip`, `run-claude`) switched scene_detector
  histogram→pyscenedetect (6→71 segments on the dresses clip → per-shot captions; the
  dress-change ask now answers correctly with 13 hyperlinked changes). `render_evidence`
  now balances evidence round-robin across modalities (visual hits were crowding out all
  captions). All additive/internal; no interface change. New workdir `.va-shots` = dresses
  full-pipeline re-ingest (71 captions, 95 transcript lines, 270 detections, 73 tracks).
  **Re: whisper large-v3** — interim relief: real-config profiles now pin `whisper: base`
  (cached, works; that's why `.va-shots` HAS transcripts). Both asks ((1) IngestResult.errors,
  (2) va warmup) are acknowledged and queued — (1) next time ingest.py is touched.
- **2026-06-11 (roles):** Deep-scan (Tier 5b) finished inside `ask()` — counting questions
  ("how many times does X change") now sweep frames, LLM-normalize labels, and count in
  code; validated stable + correct on the dress question (11 distinct / 34 transitions,
  identical across runs). All internal to `ask()` — no interface change for the web layer;
  note such asks take ~3-4 min on first sweep (cached + fast after; another reason ask
  belongs on the job queue eventually). New `observations` table in the central DB
  (additive). 84 tests green.
- **2026-06-11 (web):** Web-ask 500 root-caused + fixed. Cause: Qwen's planner emitted
  well-formed JSON with a wrong-typed field (`"params": "person's dress"` — string where
  dict required); `QueryPlan.model_validate` raised ValidationError, which escaped
  `plan()` (only `parse_json_block`→None had a fallback) and crashed `/api/ask`.
  Question-dependent, so it looked intermittent. **Note: I edited a roles-agent file** —
  `adapters/reasoner/qwen_inproc.py` `plan()` now drops the offending fields named in the
  ValidationError and re-validates; full rule fallback only if still invalid (mirrors the
  module's existing unparseable-JSON fallback). Offline test added in
  `tests/test_reasoner_rule.py` (`test_qwen_plan_salvages_wrong_typed_fields`, real
  payload from the crash). Web-side hardening (my files): `/api/ask` now serializes
  concurrent asks behind a lock, returns `HTTPException(500, "<Type>: <msg>")` and logs
  the traceback instead of a bare 500; frontend shows the detail and guards Enter-key
  re-entry. Suite green (86 passed). Suggest the same salvage pattern for any future
  `model_validate` on LLM output (`reason()` builds `Answer` manually — already fine).
- **2026-06-11 (roles):** ⚠ **Workspace layout v2** (user-requested). The `Workspace`
  contract row changed: per-video artifacts now live in `videos/<key16>-<slug>/`
  (media as `media.<ext>`, per-video `vectors.npz` shard, `keyframes/`); `catalog.db`
  unchanged at the root; `cache/` is transient-downloads only. **`GET /api/media` is
  unaffected** — it reads `local_path` from the catalog, and migration retargets those
  rows (verified: web test suite green post-change). New CLI: `va remove <video>`,
  `va reingest <video>`, `va migrate-layout`; new pipeline entry points
  `va.pipeline.manage.remove_video/reingest_video` (could become a delete button / a
  re-process action in the UI). All experiment workdirs (.va, .va-test, .va-shots,
  .va-snake, .va-nature) migrated; old monolith kept as `vectors.npz.v1.bak`. If the web
  layer globbed any paths directly (rather than via catalog `local_path`), adapt to the
  new layout.
- **2026-06-11 (web):** Layout-v2 follow-up on the web side. The roles agent's "media is
  unaffected" held only when the server's CWD matches the ingesting session's: `ingest`
  stores `local_path` as derived from the `--workdir` *argument*, so a relative workdir
  (`.va-shots` from the repo root — true of every existing row) yields a CWD-relative
  `local_path`. A server (or any consumer) started from elsewhere would see
  `has_media=false` / 404 — defeating v2's ingest-once-reuse-everywhere goal. Web fix:
  `app.py` resolves media as `local_path` if it exists, else falls back to the canonical
  per-video dir (`ws.video_dir(source_key)` glob `media.*`). ⚠ web now also depends on
  `Workspace.video_dir(source_key)` semantics (key-prefix glob). Regression test added
  (`test_media_resolves_via_video_dir_when_local_path_is_stale`). Optional roles-side
  improvement: store `local_path` absolute (or workdir-relative by convention) at ingest.
  Also: README's stale "switching models = fresh workdir" line updated to `va reingest`,
  plus a note that web + CLI share the workdir library. Suite green.
- **2026-06-11 (roles):** Self-escalation added inside `ask()` (no interface change):
  when no deep scan ran and the sparse answer admits insufficiency (or is uncited+empty),
  the ask re-runs once with a deep scan. **Web UX implication:** a question that used to
  return fast-but-shruggy can now legitimately take the deep-scan latency (minutes on a
  first sweep) — the existing spinner copes, but the job-queue promotion for /api/ask is
  now more attractive. Escalations are visible in `evidence.notes`
  ("self-escalation: ...").
- **2026-06-11 (web):** /api/ask promoted to the job queue (the follow-up flagged in the
  self-escalation entry — deep-scan asks can take minutes, too long for a sync request).
  ⚠ Endpoint shape changed (web-internal; only the bundled frontend consumes it):
  `POST /api/ask` now returns 202 `{ask_id}`; poll `GET /api/asks/{id}` →
  `{ask_id, question, state, error, result}` with the old response body as `result`.
  `jobs.py` refactored: generic `SerialQueue` base, `IngestQueue` + new `AskQueue`
  (single worker per queue — also replaces the ask lock for LLM serialization).
  Frontend polls with an elapsed-seconds pill and now renders `evidence.notes` under
  the answer, so "self-escalation: ..." is visible to the user. Tests updated to the
  submit+poll protocol. The `ask()` pipeline contract is unchanged. Suite green (96).
- **2026-06-12 (roles):** Role 10 (OCR) implemented end-to-end. New: `roles/ocr.py`
  (`OcrReader.read(media_path) -> OcrLine[]`), adapters `ocr/sidecar_inproc.py` (stub,
  `<video>.ocr.json`) + `ocr/rapidocr_inproc.py` (PP-OCR models on onnxruntime, `ocr` extra, CPU), `OcrStore`
  over the existing `ocr_results` table, query path `pipeline/ocr.py`, CLI `va ocr`.
  ⚠ Shared-contract additions (backward-compatible, defaults only): `QueryPlan` gains
  `needs_ocr_search`; new evidence modality string `"on_screen_text"` (source_role=10);
  `IngestResult` gains `ocr_lines: int` (web ingest status may want to surface it).
  Ingest runs OCR best-effort after transcripts. Configs: `ocr:` role added to default
  (sidecar) and run-siglip/run-claude (rapidocr). Plan deviation, flagged: paddlepaddle's
  inference engine segfaults at predictor init on aarch64 (tried v5/v6 models, mkldnn
  on/off) — RapidOCR runs the same PP-OCR lineage on onnxruntime instead. NOTE for web sessions: a running
  server needs a restart to pick up the new tier (stale-module caveat from 2026-06-11
  still applies). Existing `.va-shots` videos got OCR rows via backfill (no reingest).
- **2026-06-12 (roles):** Role 7 (Action Recognizer) implemented end-to-end. New:
  `roles/action_recognizer.py` (`recognize(media_path, spans, actions) -> List[List[ActionEvent]]`),
  adapters `action_recognizer/motion_inproc.py` (stub) + `xclip_inproc.py` (X-CLIP, `action`
  extra), `ActionStore` over the existing `action_events` table, query path `pipeline/actions.py`,
  CLI `va actions`. ⚠ Shared-contract additions (backward-compatible, defaults only):
  `QueryPlan` already had `needs_action_query` — it now EXECUTES in `assemble()` instead of
  being recorded as an "unavailable" note (the `_UNAVAILABLE` dict is now empty); new evidence
  modality string `"action"` (source_role=7); `IngestResult` gains `action_events: int` (web
  ingest status may want to surface it alongside ocr_lines). Ingest runs Role 7 best-effort
  per Role-1 segment, after OCR. Configs: `action_recognizer:` added to default (motion) and
  run-siglip/run-claude (xclip). NOTE for web sessions: restart to pick up the new tier.
  Heads-up: discovered 3 `.va-shots` videos (ferrari/cobra/F&F) were ingested pre-Role-1 with
  0 segments — backfilled segments+actions without reingest; web "media is unaffected" still holds.
- **2026-06-12 (roles):** Role 9 (Speaker Diarizer) implemented end-to-end. New:
  `roles/diarizer.py` (`diarize(media_path) -> SpeakerTurn[]`), adapters
  `speaker_diarizer/sidecar_inproc.py` (stub, `<video>.diarization.json`) +
  `pyannote_inproc.py` (pyannote.audio, `diarize` extra), `pipeline/diarize.py::assign_speakers`
  (temporal-overlap join). No schema change — it fills the existing `transcripts.speaker`
  column. ⚠ Shared-contract additions (backward-compatible, defaults only): `IngestResult`
  gains `speakers: int` (distinct speakers assigned; web ingest status may want it alongside
  transcript_lines); `TranscriptStore.search()` + `pipeline.transcript.search_transcripts()`
  gained an optional `speaker=` filter; `va transcript --speaker <label>`. Ingest runs Role 9
  best-effort between STT and the transcript write. Configs: `speaker_diarizer:` added to
  default (sidecar) and run-siglip/run-claude (pyannote). NOTE: the real pyannote path needs
  HF_TOKEN + the gated model accepted — unavailable in this env, so it's implemented but
  unvalidated; degrades gracefully (speaker stays NULL). Restart any running server to pick
  up the new field/flag.
- **2026-06-17 (perf):** Added `performance-and-productization-plan.md` — a proposal doc (no code
  changes yet) for taking the PoC to a DGX Spark appliance (multi-tenant, continuous camera ingest,
  agent hosting). Relevant to BOTH agents since it touches storage, pipeline, web, and proposes new
  Go/Rust components. Key proposed seams: vector engine swap behind the existing `VectorStore`
  Protocol (LanceDB/Qdrant), SQLite→WAL/pooled→Postgres behind the store interface, model serving as
  `backend: http` (Triton/vLLM/NIM), and two NEW out-of-Python components (Go camera-edge daemon, Go
  API gateway/agent host) that call the existing role HTTP adapters — spine unchanged. Nothing
  implemented; no contract changes. Flagged here so neither agent is surprised when these land.
- **2026-07-27 (trust):** Trust-gate layer L1 landed (`workflow-trust-plan.md` WT.0–WT.2 slice,
  branch `trust/l1-git-hooks`): checked-in git hooks under `.githooks/` (pre-commit: branch/artifact/
  secret/test-deletion/syntax gates; commit-msg: trailer hygiene — strips `Co-Authored-By: Claude`,
  appends the repo sign-off; pre-push: full offline suite must be green). **Action for BOTH agents:
  run `bash scripts/setup-hooks.sh` once on this machine** (sets `core.hooksPath=.githooks`).
  Commits on main are now blocked (use branches; `ALLOW_MAIN_COMMIT=1` is a human-only override).
  Sandbox self-tests in `tests/test_trust_hooks.py`. The review lifecycle (post-commit reviewer,
  `need_agent_review` subjects, human sentinel) comes in the NEXT PR — commit subjects are not yet
  gated. No pipeline/contract changes.
- **2026-07-27 (trust):** Review lifecycle landing (branch `trust/l2-review-lifecycle`, plan WT.4):
  **commit subjects are now gated.** Every commit must declare itself — `need_agent_review: <desc>`
  (complete work → post-commit hook spawns a fresh reviewer agent; findings in `reviews/`),
  `wip:`/`checkpoint:` (unfinished, free), or a plain subject (only when finalizing an approved
  commit with the human's `.commit-approved` sentinel, or on docs-only branches). Pushes block on
  provisional subjects and on unapproved content (backstop review). See the new "Commit & review
  lifecycle" section in CLAUDE.md. Affects BOTH agents' commit habits immediately after this merges.
- **2026-07-27 (trust):** Session guards landing (branch `trust/l0-session-guards`, plan WT.3):
  committed `.claude/settings.json` adds PreToolUse guards (bash_guard.py: blocks --no-verify,
  force-push main, hooksPath changes, human-only override tokens, sentinel/approval-file writes,
  reviews/ writes, self-merge/label; path_guard.py: blocks Edit/Write to gate machinery + reviews/ +
  sentinels) and a Stop gate (turn cannot end with a red offline suite; change-detected, exit-code
  based). **Action for BOTH agents: restart sessions after this merges** (hooks snapshot at start).
  Gate-machinery edits need the human's guard-override sentinel. Guards demonstrated live during
  their own development (blocked their author; resolved via the designed human override). NB: bash
  heredocs whose PROSE mentions override commands trip bash_guard — write docs via the Edit tool.
- **2026-07-28 (trust):** CI gates landing (branch `trust/l3-ci`, plan WT.5–WT.7). **The PR contract
  changes for BOTH agents once this merges:** (1) `offline-tests` runs the full offline suite on
  every PR/push to main and is a required check — a red suite now blocks merge on GitHub, not just
  locally; (2) every PR body must contain a filled `EVIDENCE: offline suite` block (the `evidence`
  check fails on the unedited template — use the new `/verify` command to generate it); (3) PRs
  touching the critical paths in `scripts/critical_paths.txt` (schema, contracts, ingest, cli,
  golden fixtures, and all trust machinery → `human-reviewed`; adapters, pipeline, config dirs →
  `golden-verified`) fail CI until the HUMAN applies the label — agents are guard-blocked from
  applying it via `gh pr`, `gh issue`, the REST/GraphQL API, and common HTTP clients — but note
  this is defense-in-depth, NOT a guarantee: agent sessions share the human's credential, so the
  label marks intent rather than proving it (see D9 in workflow-trust-plan.md). Checks re-run on
  `edited`/`labeled`, so fixing a body or adding a label needs no new commit. Golden gate stays
  manual on the Spark.
- **2026-07-29 (trust):** Role instructions landing (branch `trust/role-instructions`, plan WT.11).
  **Action for BOTH agents:** completed tasks are now committed via the **`/task-commit`**
  procedure (scope check, combination check, documentation check, review loop, four-section
  digest — the digest must name affected backend/config/profile combinations with tests run,
  documentation added or open doc questions, and the proposed final commit message). The
  reviewer rubric moved: `.claude/agents/code-reviewer.md` is now the SINGLE source —
  `scripts/agent-review.sh` assembles its prompt from it (drift-tested), so rubric edits happen
  in one file only. Final commit messages must read plainly for uninformed readers; shorthand
  IDs (WT.x/RI.x) only as trailing references. Disputes of review findings go in
  workflow-trust-plan.md, never in `reviews/`.
- **2026-07-29 (roles):** Qwen3-VL reasoner experiment CLOSED at parity and landing on main:
  new Role-11 adapter `src/va/adapters/reasoner/qwen3vl_inproc.py` (subclass of `QwenReasoner`),
  an additive `qwen3-vl` routing branch in `src/va/registry.py`, and the `run-qwen3vl/config`
  dir (reasoner: `qwen3-vl-30b-a3b`, 58 GB local weights at `~/qwen3vl`, loaded bf16, outside the HF
  cache). Golden ask set at parity with `claude-code` (bird-ask-01 re-validated post
  scan_target-backfill fix). No contract changes; web layer unaffected. Caveat for anyone
  running real models: the loaded reasoner is ~45 GB resident — ONE real-model golden run at a
  time on this box. Decision + revisit triggers in `video-analytics-model-analysis.md`.
- **2026-07-29 (storage):** `catalog.db` is now schema-versioned via SQLite `PRAGMA user_version`
  (`storage/structured/schema.py`). **Action for BOTH agents:** opening a workdir DB through
  `connect()`/`apply_schema()` now auto-migrates it forward (additive, idempotent `ALTER`s in a
  `BEGIN IMMEDIATE` txn) and stamps its version; a DB written by a newer build than the code logs
  a warning and proceeds. No contract or query-surface change — every store opens the DB the same
  way and the migration is transparent. To evolve the schema, follow the in-file recipe (base DDL
  + ordered idempotent migration + `SCHEMA_VERSION` bump); indexes are built after migrations.
- **2026-07-30 (storage):** Vector shards now carry an identity tag. `NumpyFlatVectorStore` persists
  a `meta` entry inside each `.npz` — `{embedder: <model id>, dim: <D>}` — stamped at write time
  (`ingest.py` visual shard, `text_index.py` text shard). **Action for BOTH agents:** the `.npz` shard
  format gained a second array (`meta`); old untagged shards still load (`store.meta is None`), and
  search is unchanged (nothing reads the tag yet). The query-time guard that USES the tag to refuse
  mixing vector spaces (`stub-64` vs `SigLIP-1152`) lands next (provenance-reprocess-plan.md, TAG-3).
  No contract or query-surface change.
- **2026-07-30 (retrieval):** The shard tag above is now ENFORCED at query time.
  `ShardedVectorStore.search(expect_embedder=...)` skips shards whose embedder tag != the current
  query embedder (and skips a dim mismatch that would otherwise crash `vecs @ q`), exposing the count
  on `store.skipped` + a logged warning; `query()` and `search_text()` pass the current embedder.
  **Effect for the web agent:** a workdir mixing embedders now returns results only from the matching
  shards (stale-embedder videos drop out, with a warning) instead of silently-wrong hits; reprocess /
  reingest re-tags + rejoins them. Legacy untagged shards are admitted when their dim matches
  (best-effort — the honest gap until TAG-4 backfill).
- **2026-07-30 (storage):** `catalog.db` is now schema **v2** — a new `role_provenance` table
  (`(video_id, role)` PK; model, fingerprint, fps, run_id, row_count, produced_at) recording which
  model/config produced each role's rows (WS-1 §6-b provenance). **Action for BOTH agents:** opening a
  workdir DB auto-migrates it to v2 (adds the table; existing rows untouched). Nothing writes it yet —
  ingest stamping lands in PROV-3; read/write via `storage.structured.provenance_store.ProvenanceStore`.
  No contract or query-surface change.
- **2026-07-30 (storage):** ingest now STAMPS `role_provenance` on every successful run (PROV-3,
  `ingest._record_provenance`) — supersedes the "nothing writes it yet" above. **Action for BOTH
  agents:** the shared `catalog.db` gains up to ~10 provenance rows per ingested video (incl. via
  `va serve`); best-effort so it never fails an ingest, and roles whose best-effort step failed are
  intentionally NOT stamped (absent row = stale to `va stale`).
- **2026-07-30 (reasoning cache):** the deep-scan `observations` cache keys now fold in the captioner
  + reasoner fingerprints (PROV-3, `deep_scan.py`), so a model upgrade re-runs the sweep instead of
  serving stale code-counted answers. **One-time effect for the web agent:** after this lands, the
  FIRST `va ask` per previously-cached question on a shared workdir (`.va-shots`, incl. via `va serve`)
  re-runs a multi-minute VLM sweep because the old cache keys no longer match — this is an intentional
  one-time invalidation, not a hang or perf regression. Subsequent asks are cached as before.
- **2026-07-30 (read helper):** new read-only `va stale [--role R]` (`pipeline/stale.py::stale_report`)
  lists DONE videos whose recorded provenance fingerprint != the current config's, per role. No shared
  contract or schema change (it only READS `role_provenance`). **Available if useful to the web agent:**
  `stale_report(workdir)` returns `[{video_id, source_uri, title, stale_roles}]` — handy for a "videos
  needing reprocessing" view once pillar B (selective reprocess) lands.
- **2026-07-31 (read helper):** new read-only `va reprocess [--role R] (--all-stale | --video IDENT)
  [--dry-run]` (`pipeline/reprocess.py::plan_reprocess`) — the pillar-B selection front-end. Returns
  the same stale rows as `va stale`, scoped to a chosen video or role. **No mutation yet:** execution
  (RPRC-1) is not built, so the command can only PLAN (refuses without `--dry-run`). No shared contract
  or schema change. When execution lands it will re-run role rows AND purge the deep-scan `observations`
  cache — a heads-up will follow here before any write path ships.
- **2026-07-31 (WRITE PATH — pillar B RPRC-1a):** `va reprocess` (without `--dry-run`) now EXECUTES for
  the first wired role, **`text_embedder`** (`reprocess.py::execute_reprocess`): it rebuilds that
  video's `text_vectors` shard in place (via `text_index.backfill_text_index`) THEN restamps
  `role_provenance` — rows first, provenance second, so a crash stays stale (safe to retry). **For the
  web agent:** on the shared `catalog.db`/workdir, a `text_vectors` shard can now be rebuilt out from
  under a running `va serve`. `index_text` now embeds BEFORE unlinking the old shard, so a rebuild that
  fails (e.g. GPU OOM) leaves the prior shard intact; the replace window is just the local `.npz` write,
  and the shard is idempotent. Only `text_embedder` mutates today; every other stale role is SKIPPED
  with a `va reingest` pointer (visual/caption reprocessors + the `observations` purge are RPRC-1b/c).
- **2026-07-31 (WRITE PATH — pillar B RPRC-1b):** `va reprocess` now also re-runs **`visual_embedder`**
  in place (`reprocess.reindex_visual`): it re-samples frames at the video's RECORDED fps (from
  provenance — an unknown fps is refused, pointing to `va reingest --fps`) and re-embeds the `vectors`
  shard, building to a temp `vectors_rebuild.{npz,json}` and `os.replace`-swapping it in only on success
  (a failed re-embed leaves the old shard, so visual search keeps working). **For the web agent:** the
  visual `vectors` shard can now be rebuilt+re-tagged under a running `va serve`, same as `text_vectors`.
  Still SKIPPED → `va reingest`: caption and the leaf roles (RPRC-1c + beyond).
- **2026-07-31 (shard-write ordering — concurrency):** all shard writers now write/swap the `.npz`
  LAST (`NumpyFlatVectorStore.persist` writes `.json` then `.npz`; `reindex_visual` swaps `.json` then
  `.npz`). This supersedes the RPRC-1a entry's "the replace window is just the local `.npz` write"
  framing: because the sharded shard-cache keys on the `.npz` mtime and `_load` requires both files, a
  reader racing a rebuild now sees either the old pair or the fully-new pair — never an empty/torn shard
  cached under the final mtime (which would have dropped a video from search until restart). **For the
  web agent (`va serve`):** queries concurrent with `va reprocess`/`reingest` are now safe against that
  persistent-empty-shard race. A torn read during the two-file swap (new `.json` + old `.npz`) is now
  caught by a vector/payload length check in `_load` — a mismatched pair reads as EMPTY (not misaligned
  hits) and self-heals on the next query (the `.npz` mtime bump invalidates it). The only residual is a
  same-COUNT content mismatch in that microsecond window, which the length check can't see. No API change.
- **2026-07-31 (durability — text shard):** `index_text` now builds to a temp shard and swaps via the
  shared `numpy_flat.swap_shard` (same as `reindex_visual`), replacing the earlier embed-before-unlink
  approach. So a failure ANYWHERE in a text rebuild — embed, a disk-full in `np.savez`, a process kill —
  now leaves the prior `text_vectors` shard intact (the RPRC-1a "leaves the prior shard" claim held only
  for pre-unlink failures before this). Affects ingest's text index too, not just reprocess. No API change.
- **2026-07-31 (pillar B COMPLETE — RPRC-2):** `va reprocess` is now dependency-aware: when a role's
  reprocess also rebuilds a dependent's artifact, the dependent is restamped without a redundant rebuild
  (one active edge: re-captioning rebuilds the text index, so a stale `text_embedder` is restamped, not
  rebuilt again — shown as "restamped (rebuilt via a dependency)"). Internal optimization; no contract
  change. Pillar B (§6-b: find-stale via `va stale` → re-run in place via `va reprocess`) is complete
  for the three standalone-code roles (text/visual embedders, captioner); leaf roles remain `va reingest`.
- **2026-07-31 (WRITE PATH — pillar B RPRC-1c):** `va reprocess` now also re-runs **`vlm_captioner`**
  (`reprocess._reprocess_vlm_captioner`): re-captions each segment's keyframe and updates
  `segments.caption` (caption-all-first, so a mid-run failure overwrites nothing), then propagates to
  the two caption dependents — **rebuilds the `text_vectors` shard** (captions are a text modality) and
  **purges the deep-scan `observations` cache** for the video (new `ObservationStore.purge`), so the
  next `va ask` re-sweeps. **For the web agent:** after a caption reprocess a video's `segments.caption`,
  `text_vectors` shard, AND cached `va ask` sweeps all change together. This completes RPRC-1 (all three
  standalone-code roles — text, visual, caption — wired); the remaining stale roles still → `va reingest`.
- **2026-08-03 (roles):** Footage-profile config layer landed (architecture-evolution-plan WS-2,
  item WS2.a). `load_config()` gained an optional third layer: `config/profiles/footage/<name>.yaml`
  per-role overrides deep-merged over roles.yaml specs at load time; `Config` gained
  `footage_profile: str` (default `"generic"` = no-op; missing generic file tolerated, so run-siglip/
  run-claude/run-qwen3vl config dirs are unaffected). Additive only: `load_config()` signature gains
  optional `footage_profile=`; no call-site or behavior change under the default — full suite green
  (530 passed). Per-ingest selection (`--profile`, `videos.profile`) is the next item (WS2.b).
- **2026-08-03 (roles):** Per-ingest footage-profile selection landed (WS2.b, stacked on WS2.a).
  Additive shared-surface changes: catalog DB is schema **v3** (`videos.profile` TEXT, NULL =
  pre-profile ingest; auto-migrates on open), the `Video` contract + catalog row mapping gain
  `profile`, `ingest()` gains optional `profile=` (validated via `load_config` BEFORE fetch; unknown
  name raises `FileNotFoundError`, even on the already-ingested dedup path; recorded name resolves
  explicit arg > roles.yaml `active_footage_profile` > source-derived default, so the record matches
  what roles self-loading config actually run under), `va ingest` gains
  `--profile` (default source-derived, currently always `generic`; a deduped ingest with a differing
  profile prints a not-applied notice), and `va reingest` gains `--profile` — `reingest_video()`
  carries the video's recorded profile forward by default instead of resetting it to the source
  default, and validates the target profile BEFORE the destructive removal (a typo'd name leaves
  the video intact). NOTE: the profile is *recorded only* — roles do not consume it until WS2.c, and
  the provenance stamp still fingerprints the base config on purpose (stamping overlay-modified cfg
  before roles apply it would stamp models that didn't run). Full suite 538 passed / 2 skipped.
  One test double updated (`test_provenance_ingest` load_config lambda now accepts kwargs).
- **2026-08-03 (roles):** Footage profiles now GATE roles + vocab at ingest (WS2.c, stacked on
  WS2.b). `RoleConfig` gains `enabled: bool = True`; ingest pins `load_config(footage_profile=…)`
  and passes that cfg to EVERY role getter (they always accepted an optional cfg), so per-role
  overlay overrides — including `classes:`/`actions:` vocab — now actually apply per ingest.
  `enabled: false` skips a best-effort role (a trace `skipped` event is emitted); dependent roles
  skip with their parent (STT off → diarizer; detector off → tracker). SKIPPED roles are NOT
  provenance-stamped, so `va stale` reads them as stale — deliberate (false stale OK). This
  SUPERSEDES the WS2.b note: the provenance stamp now fingerprints the overlay-applied config,
  which is what the roles really ran under. New checked-in `config/profiles/footage/security.yaml`
  (skips roles 8/9, narrows detector classes). ⚠ Behavior note for the web agent: an ingest under a
  non-generic profile can legitimately produce 0 transcript rows by design — not a Whisper failure.
  Five zero-arg test doubles of registry getters updated to `lambda *a, **k:` (see the new CLAUDE.md
  lesson). Also profile-aware after review: `embedder_id(role, cfg=None)` + `index_text(..., cfg=)`
  tag shards from the SAME overlaid config that embedded them; `va stale` / `va reprocess` compare,
  rebuild, and restamp each video under ITS recorded profile (`config_for()` helper; stale rows
  gained `profile`/`source_type`; profile-DISABLED roles are excluded from staleness, not reported
  forever-stale); a skipped role PURGES rows a prior attempt wrote (empty `replace_*`), so a retry
  under a gating profile honors the 0-rows promise; footage profiles now ship in all four config
  dirs (run-siglip/run-claude/run-qwen3vl too). One reprocess test double moved to the new pin seam
  (`config_for`), assertions kept. Round-2 review fixes: the enabled-gate tolerates a roles.yaml
  that OMITS roles (missing = enabled, mirroring the getters' stub fallback — a minimal config no
  longer aborts ingest); `object_tracker: {enabled: false}` now honored (detections stored
  UNTRACKED with `track_id` NULL, tracks purged); vocab override proven end-to-end (color-class
  profile drives stub detections); ⚠ documented caveat: do not override EMBEDDER models in a
  footage profile — the query path is profile-unaware and would tag-skip those shards (CLAUDE.md +
  loop backlog). Full suite 555 passed / 2 skipped.
- **2026-08-03 (roles):** WS2.c staleness semantics CORRECTED (supersedes "profile-DISABLED roles
  are excluded from staleness" two entries up): a disabled role is excluded ONLY while unstamped;
  a role that RAN before the profile was edited to disable it now READS STALE (its rows contradict
  the profile; `va reingest` under the carried profile purges them and converges). `va reprocess`
  routes profile-disabled roles to `skipped` (never re-runs them — a disabled captioner is not
  regenerated). Also stricter load validation: `enabled:` in a footage yaml must be a real YAML
  boolean (`enabled: "false"` now raises at `load_config`, closing an ingest/stale divergence),
  and core (non-gateable) roles reject `enabled: false` outright. Full suite 565 expected green —
  count in the final digest.
- **2026-08-03 (roles):** WS2.d — footage profiles gain PROFILE-WIDE knobs (config surface only,
  stacked on WS2.c): `Config.footage: FootageSettings` with `retention_days` (None = keep forever),
  `time_model` (relative|wall_clock), `deep_scan` (auto|"off"), validated at load with
  extra='forbid' (a typo'd knob raises, naming the yaml). `security.yaml` records the plan's locked
  values (14 / wall_clock / off) — inert until P7.a, WS-3, and R11.a consume them. ⚠ Small shape
  change: `execute_reprocess`'s `skipped` rows are now 3-tuples `(video_id, role, reason)` (the
  CLI prints the real reason — "profile disables this role" vs "no in-place reprocess yet").
  Full suite 575 passed / 2 skipped.
- **2026-08-03 (roles):** WS3.a — camera entity landed (schema **v4**; stacked on WS2.d).
  Additive: new `cameras` table (`id/name/source_ref/location/created_at`) + `videos.camera_id`
  (nullable FK; NULL = standalone A-EV video — every existing row and all A-EV ingests stay NULL),
  auto-migrating on open. New `Camera` contract (`contracts/video.py`), `CameraStore`
  (`storage/structured/cameras.py`: get_or_create/get/list, id = idempotency key), and
  `Catalog.set_camera(video_id, camera_id)`. Nothing sets `camera_id` during ingest yet — WS-4's
  stream source will — but `reingest_video` PRESERVES an existing camera link across its
  remove+ingest cycle (re-attached on the failure path too, so a later plain-ingest retry keeps
  it). Full suite 582 passed / 2 skipped.
- **2026-08-03 (roles):** WS3.b — dual time model landed (schema **v5**; stacked on WS3.a).
  Additive: `videos.start_epoch` REAL (absolute UTC epoch seconds of a chunk's t=0; NULL =
  relative-only — every existing row and all A-EV ingests), `Video.start_epoch`,
  `Catalog.set_start_epoch`, and NEW `pipeline/timeline.py` — `absolute_time(video, rel)` and
  `wallclock_to_chunks(videos, t0, t1) -> [ChunkRange(video_id, rel_start, rel_end)]` (skips
  NULL-epoch videos; clamps + orders; unknown-duration chunks are CAPPED at the range end, never
  open-ended). `reingest_video` carries `start_epoch` across the cycle like camera_id. Storage rule
  unchanged: ALL stored timestamps remain video-relative (plan §4). Nothing sets `start_epoch`
  yet — WS-4's NVR chunk source will. NB for the future query layer: `va query`/web endpoints are
  still relative-only; wall-clock query surfaces come with WS-4/5. Full suite 589 passed / 2
  skipped.
- **2026-08-03 (roles):** WS4.a1 — MotionSource role landed (plan §3.1). New: `roles/motion_source.py`
  (`MotionSource.events(start_epoch, end_epoch, camera_ref=None) -> [MotionEvent]` Protocol +
  `cluster_events(events, gap_s)`), `contracts/motion.py` (`MotionEvent`: camera_ref/start_epoch/
  end_epoch/kind/attributes, extra-tolerant), adapters `motion_source/sidecar_inproc.py` (JSON stub,
  `events_file` in the role spec) + `lnr_eventlog_inproc.py` (LNR608 `log.cgi` startFind/doFind/
  stopFind poller; host via spec or VA_NVR_HOST; credentials ONLY via VA_NVR_USER/VA_NVR_PASS env —
  never config; NVR-clock timezone via role-spec `tz:` or VA_NVR_TZ, default = system-local rules
  (DST-aware per date); `camera_ref` = the NVR's 1-indexed DISPLAY number, display→API channel mapping
  deferred to the pull step), `get_motion_source()` in registry, `motion_source:` role (sidecar) in
  all four roles.yaml, and a `va motion-probe <start> <end> [--camera] [--cluster-gap]` diagnostic
  CLI. Nothing consumes it at ingest yet (WS4.b/c). LIVE-VALIDATED against the LNR608 (WS4.a2): this firmware emits MULTI-LINE Detail values and
  logs each episode as separate Start/End marker entries — the adapter parses continuations and
  pairs markers per channel (verbatim live fixture in tests). Ground-truth window check: 25 probe
  windows vs 22 golden clips for Aug 1 noon-2pm, consistent. Full suite 610 passed / 2 skipped.
- 2026-08-04 (WS4.b, loop session): **SceneDetector interface extended + motion-episodes
  backend.** `roles/scene_detector.py`: `detect(video_path)` is now
  `detect(video_path, context: SceneContext | None = None)` — `SceneContext(start_epoch,
  camera_ref, duration_seconds)`, all defaults None, so existing backends/callers are
  source-compatible (histogram + pyscenedetect accept-and-ignore it). New adapter
  `scene_detector/motion_episodes_inproc.py`: asks the configured MotionSource for events
  in the chunk's wall-clock range (epoch→relative via start_epoch), clusters
  (`cluster_events`), pads/clamps/merges; degraded modes = full-span single segment
  (missing start_epoch, or MotionSource failure — warns, never aborts ingest); epoch
  present + zero events = zero segments. Registry: `scene_detector.model:
  motion-episodes` (knobs `pad_s`/`gap_s`/`min_span_s` on the spec). All four
  `security.yaml` footage profiles now select it. `ingest.py` builds the context from the
  catalog row (re-read at Role-1 time) + `cameras.source_ref`. lnr adapter: flat-shape
  present-but-unparseable End Time now WARNS before start-anchoring (WS4.a round-8
  carry-over). Tests: `tests/test_motion_scene_detector.py` (known-window ground truth,
  end-to-end ingest oracle), one new lnr regression test.
- 2026-08-05 (WS4.c, loop session): **nvr_recorded chunk source.** New `SourceType.
  nvr_recorded` + `sources/nvr.py` (`nvr://<channel>/<start>/<end>`, naive times = NVR tz
  via VA_NVR_TZ else system-local; source_key `nvr:ch<n>:<start_epoch>-<end_epoch>`;
  fetch = the §5d verify-and-trim pull — curl (--anyauth; endpoints are Basic-ONLY,
  never harden to --digest) loadfile in isolated 10 s
  sessions, dHash-verified per frame against a live snapshot.cgi reference, trimmed to
  the longest clean run, uniform re-encode + concat; VA_NVR_HOST/USER/PASS env-only).
  **ResolvedVideo grew optional `start_epoch` + `camera`** (defaults None — placeless
  sources unaffected): ingest attaches them to the catalog row BEFORE fetch/roles
  (durable under hard kills; Role 1 sees the placement). `_SOURCE_PROFILE_DEFAULTS` maps
  nvr_recorded -> security. Carry-over fixes: `Catalog.set_camera` now VALIDATES the
  camera row exists (raises ValueError; FK pragma stays off), `CameraStore.get_or_create`
  is atomic (INSERT OR IGNORE + re-SELECT, never clobbers an existing row's name).
  Motion-episodes backend gained `query_margin_s` (default 60): live WS4.c validation
  showed the NVR's episode End marker sits at/just beyond the chunk bounds, and an
  exact-range query collapsed the episode to (0,2); with the margin the live re-run
  landed (0.0, 31.7). Timeline caveat: verify-and-trim drops stale frames, so media t=0
  aligns with start_epoch only to ~1 s and clips may be shorter than the window.
- 2026-08-05 (WS4.d, loop session): **per-track appearance embeddings.** Schema v6:
  `object_tracks.appearance_ref TEXT` (nullable; migration `_m6_appearance_ref`);
  `ObjectTrack` contract grew the matching optional field. Ingest: detection crops are
  harvested during the existing single decode pass, spilled to a transient cache dir
  as downscaled JPEGs (RAM stays O(1); §8.1), keyed by timestamp+bbox — VALID because
  both tracker adapters now return the ORIGINAL detections via model_copy (bytetrack
  routes them through supervision's `data` index instead of reconstructing boxes from
  its float32 round-trip; invariant pinned by tests/test_tracker_passthrough.py) —
  then after Role 6 each track's highest-confidence detection crop is embedded with the Role-2 visual embedder into a SECOND per-video
  vector store `appearance.npz` (meta `{embedder, space: appearance-crop}`), refs
  written onto the track rows. Best-effort: appearance failure costs refs, never
  tracks. Frame store untouched (separate file, no query-path change). Role 12 swaps
  in a purpose-trained ReID embedder later — this locks schema + plumbing.
- 2026-08-05 (WS4.e, loop session): **staged model execution.** The hardware profile's
  `residency:` knob (documented since the profile existed, consumed by nothing) is now
  honored by ingest: `unload-after-use` clears the ModelManager at role-GROUP boundaries
  (captioner / speech / ocr / actions / embed+detect+track+appearance — SigLIP and YOLO
  share one group; appearance reuses SigLIP inside it). `keep` (shipped default) is a
  no-op — byte-identical behavior. Measured on the §8.1 repro (single-process 22-clip
  real-model batch, security profile): keep-resident starved YOLO to detections on 1/22
  clips; staged = 22/22 (16-61 det/clip), cost ~90 s/clip in reloads. Batch ingest
  workloads should set unload-after-use; interactive single-video ingests keep the
  default. NB (round-1 review): clearing the MANAGER cache is not enough — the
  ingest-local adapter references (captioner/embedder/detector) pin the weights, so
  the boundaries also RELEASE those locals; any new role wired into ingest must do
  the same or its group's unload is cosmetic. Unknown residency values fail at
  config load; a failed ingest stages on its way out. tests/test_staged_models.py
  pins no-op-on-keep, output-invariance, load-validation, and the failure path.
- 2026-08-06 (WS6.a, loop session — ⚠ ROLES AGENT EDITING WEB-OWNED src/va/web/jobs.py):
  **durable job queue.** Schema v7 adds a `jobs` table (id/kind/state/payload/video_id/
  error/result JSON blobs); new `storage/structured/jobs_store.py` (JobStore: record/
  update/get/pending/fail_pending). `SerialQueue` persists every submit + state
  transition (best-effort: a broken jobs table degrades to the old memory-only behavior
  with a warning, never a dead queue). RESTART SEMANTICS (new): `IngestQueue.start()`
  re-enqueues queued/running ingest jobs from the table — a `running` row is a crash
  artifact; ingest()'s idempotency makes the resume exactly-once. `AskQueue.start()`
  FAILS pending asks ("server restarted — resubmit") and rebuilds their failed records
  in memory so a polling browser sees the failure, not a 404. Web-facing API surface
  (submit/get/to_dict, endpoint shapes) is UNCHANGED. Precisely: pending INGEST jobs
  survive a restart (resumed + pollable), pending ASKS surface as failed; done/failed
  HISTORY rows persist in the table but get() still reads memory only — a
  /api/jobs listing over the table is future web-agent work if wanted.
  AMENDED (round-4/5 review): the jobs table also carries `attempts` — a RUNNING row
  bumps it on each resume (crash evidence; QUEUED rows never bump), and past
  MAX_RESUME_ATTEMPTS=3 the job goes terminal-failed ('gave up after 3 resume
  attempts — this job repeatedly died mid-run'): a job that kills the process can
  never persist its own failure, so the cap is the only exit from a systemd crash
  loop. Unresumable (malformed) rows are terminal-failed, not skipped forever. The
  web UI should expect both new failure strings on polled jobs.
  tests/test_jobs_durable.py covers the oracle
  (kill-mid-job -> resume exactly once), the ask policy, and the degraded mode.
- 2026-08-06 (WS6.b, loop session): **catch-up watcher — the A-LSSRVF orchestrator.**
  Schema v8: `cameras.last_processed_epoch` (durable per-camera watermark; NULL =
  never watched); `Camera` contract + `CameraStore.set_watermark` (monotonic — the SQL
  refuses to rewind). New `pipeline/watch.py`: `catch_up()` = one pass (per registered
  camera: MotionSource query [watermark, now-settle], cluster, pull each episode as an
  nvr:// window, ingest, advance watermark per completed episode; quiet ranges advance
  to the horizon); `run_watch()` = the loop. New CLI `va watch [--camera ...]
  [--lookback-hours] [--settle] [--max-windows] [--interval] [--cluster-gap]
  [--open-instant-age]` (interval 0 = one pass, cron-friendly). NB --cluster-gap
  merges raw motion events into PULL episodes — a DIFFERENT knob from the
  scene_detector spec's same-named gap_s, which segments WITHIN a chunk at ingest;
  --open-instant-age bounds how long a lost-End open instant defers the watermark
  before the recovery pull. max_windows is split per camera (each gets
  max_windows//n, min 1) so one backlogged camera cannot starve the rest. Semantics: idempotent (nvr source_key dedup + monotonic watermark);
  straddling episodes are not re-pulled (only starts >= watermark count); episodes
  longer than the nvr 120 s cap split into back-to-back windows; a failed window HOLDS
  the watermark at the last complete episode and retries next pass; max_windows
  truncation resumes next pass. SLA (§8.2): the LNR608 ring keeps ~6 days — longer
  outages are unrecoverable and the watcher pulls what remains. tests/test_watch.py
  holds the simulated-outage oracle (exact gap windows, exactly once) + bounds.
- 2026-08-07 (R11.a, loop session): **deep-scan outfit hijack removed + profile gate.**
  `deep_scan.DEFAULT_TARGET` ("the main person's outfit" — the scan_target lesson's
  hardcoded content) is DELETED: a deep-scan plan with no derivable target skips the
  sweep with an honest evidence note. Target derivation is centralized in
  `rule_inproc.derive_scan_target(query)` — content words minus the scan-noise set,
  EXCEPT a tiny `_SUBJECT_NOUNS` whitelist ({color, colour, number}) whose members are
  noise for counting purposes but ARE the subject when nothing else survives; a
  pronoun-only query yields None (no sweep). NB it is NOT the search `_STOP` set —
  reintroducing that would strip exactly those subject nouns. Used by the rule planner,
  the LLM-plan backfill, and self-escalation — always FROM THE QUERY. The WS2.d `deep_scan` footage knob is now
  CONSUMED: run_deep_scan resolves the dominant video's recorded profile
  (config_for — record==reality) and skips when it says "off" (security does); skip
  causes are per-cause evidence notes. Done-when evidence (recorded here so the
  branch carries it): A-EV golden-ask harness run 2026-08-07 on this change,
  RUN_GOLDEN=1 run-claude/config .va-shots — dresses-ask-01 passed (330 s run),
  bird-ask-01 passed on retry 103 s (first attempt died on a claude-CLI 240 s
  subprocess timeout, an environmental flake — same class as the PR #31-era one).
- 2026-08-07 (R11.b, loop session): **the relevance floor is now per-footage-domain.**
  Shared-interface change in `pipeline/retrieval.py`: `get_relevance_gate()` gained
  optional `profile=`/`source_type=` (base config when omitted — no behavior change for
  existing callers), and a new `gates_by_video(items, workdir)` maps each candidate to
  the gate its OWN video's recorded footage profile calibrates (`config_for` —
  record==reality, the same resolution R11.a's deep-scan veto uses). `retrieve()` applies
  those per item; an explicit `gate=` still overrides everything, and any catalog failure
  degrades to the base gate. Two consumer-visible shape changes: the "relevance gate
  dropped N/M" note now lists every floor that COVERED a candidate (only those — naming
  the base gate when nothing used it would report an unmeasured threshold), and
  `Evidence.attributes["fusion"]["gate"]` is a LIST of floor dicts when the pool spanned
  domains, still a single dict otherwise.
  Why: an absolute score floor only means something against the footage it was measured
  on. Measured 2026-08-07 on the 22 real NVR clips (.va-nvr, real SigLIP + bge) against
  the human's ground-truth notes for that window (kept outside the repo — they
  describe the household; tracked derivatives live in tests/golden_queries/
  nvr0801_clip*.yaml). Two query sets, two denominators — 9 targeted queries = 26
  ground-truth (query, clip) pairs at the GATE level (0.10 retained 6/26, 0.0 retains
  26/26); 8 natural questions = 25 pairs END TO END through retrieve(), also bounded
  by the k-capped gather. End to end: the A-EV floor (min_cosine 0.10) put 7/25 in
  the final evidence and emptied 3 of 8 questions to "no candidate cleared the floor — no
  match" while the real events sat in the ungated pool; per-video resolution with
  `security: min_cosine 0.0` gives 13/25 and 0 of 8 emptied (at k=20: 9/25 -> 16/25).
  The floors themselves live in run-*/config/profiles/footage/security.yaml, NOT in
  config/ — base roles.yaml omits the `retriever` role on purpose (stub scores are
  uncalibrated), so that file carries a note telling you not to "sync" it in.
  New storage method (logging it per this file's convention for Catalog additions):
  `Catalog.footage_domains() -> set[(profile, source_type)]`, the distinct footage
  domains among `ingest_status='done'` videos. The `done` restriction is load-bearing:
  a catalog row exists BEFORE fetch, so counting all rows would let one failed
  `va ingest "nvr://..."` attach a permanent mixed-domain warning to every answer in
  that workdir. Reuse this rather than writing a second definition of "footage domain".
  Combination attestation (the gate resolution changed for EVERY video, not just
  A-LSSRVF ones, so both real-model combinations were re-run on this change):
  A-EV `RUN_GOLDEN=1 VA_CONFIG_DIR=run-claude/config GOLDEN_WORKDIR=.va-shots pytest -m
  golden` = 85 passed / 25 skipped / 1 xfailed, baseline-identical (both `va ask`
  questions included); A-LSSRVF the same command with GOLDEN_WORKDIR=.va-nvr -k nvr =
  17 passed / 8 xfailed, baseline-identical — but note what that does NOT prove:
  `test_golden_queries` calls `visual_query()` directly and never reaches
  `retrieve()`, and no NVR fixture carries an `ask_questions:` block, so NO golden
  test exercises the gate on security footage. `test_shipped_real_model_configs_
  carry_the_security_floor` is what guards the shipped override; an NVR golden ask
  fixture is backlogged. A-EV retrieval was additionally probed
  item-by-item (old single gate vs new per-video resolution): byte-identical, and the
  resolved gate for a NULL-profile local/youtube video is the base (-3.0, 0.10) —
  `default_footage_profile` maps only `nvr_recorded` to `security`. Stub combinations
  are unaffected by construction: base roles.yaml declares no `retriever` role, so the
  gate stays permissive there (tests/test_relevance_gate_profile.py builds its own
  config dir to exercise the mechanism offline).
  KNOWN GAPS left deliberately (backlogged in the untracked loop state file
  architecture-evolution-loop.md, and summarized here so a clone sees them), each with
  its measurement): the SR.6 verifier floor is still base-config and no-ops on A-LSSRVF;
  `_minmax`'s 1e-9 degeneracy guard amplifies ~1e-3 score noise into full 0-1 spread; the
  action lane floods the pool with one repeated X-CLIP label; and `query_objects` matches
  query WORDS against class names, so "vehicles"/"children" reach none of the 918
  car/person detections.
- 2026-08-11 (NVR verify fix, loop session): **lighting-independent pull verification.**
  `sources/nvr.py` fetch rewritten: ONE loadfile session per window (no more 10 s
  chunk+concat), verified WITHOUT a live snapshot.cgi reference. Why: measured
  2026-08-10 (matched channels), the live-reference dHash carries the lighting of the
  download moment — same camera scores 22–23 six hours later and 38 at night (IR),
  overlapping the 25–38 different-camera band, so backfill after dark rejected nearly
  everything (night episode: 3 of 4 chunks dropped). And chunking multiplied seek
  exposure: A/B over 10 random 5-min windows / 4 days / 4 cameras, single-request
  purity 1.000 in 10/10 vs 2 dirty chunks in 300 for the chunk recipe. New checks:
  per-frame dHash vs the pull's OWN consensus (`consensus_hash`/`self_distances`,
  trims the stale-lead-in minority) + a per-channel `ReferenceLibrary` persisted at
  `<workdir>/nvr_refs/ch<N>.json` (rejects a wholly-wrong, self-consistent clip;
  bounded 12 hashes/channel, atomic writes, corrupt file reseeds). A lighting mode
  the library hasn't seen is admitted only when it matches a LIVE snapshot (right
  camera, current lighting — the production path by which a day-seeded channel
  acquires night mode; round-1 review critical); matching neither library nor live
  view refuses with recovery guidance. Undecodable frames are None (not an
  all-zeros sentinel — that IS the dhash of a dark frame; round-1 finding 3):
  excluded from consensus, forced-dirty in self-distances, not counted toward the
  ≥3-decodable floor. Seeding happens only AFTER verification + trim succeed, from
  the clean run's consensus (round-1 finding 4). FIRST pull on an empty channel
  seeds UNVERIFIED with a warning — seed deliberately (one pull per camera per
  lighting mode) if that trust matters. Removed dead: `chunk_bounds`,
  `CHUNK_S`, `_reference_hash`, `_frame_hammings`, `_pull_chunk_verified`. Trim
  caveat unchanged (clips can run short; t=0 ≈ start_epoch to ~1 s) but the
  dropped-chunk 10 s shift mode is gone. Live before/after: night 2→34 frames,
  0 chunks dropped; day 45→73 frames, detections 73→154. nvr-access-notes.md §5d
  carries the superseded-recipe annotation (file is untracked/local).
- 2026-08-12 (NVR deterministic pull): **pad + PTS-cut replaces dHash verify-and-trim
  + ReferenceLibrary.** `sources/nvr.py` pull path rewritten: fetch a PADDED window
  ([start-10 s, end+2 s], one loadfile session as before, raw .dav read directly),
  PTS-cut to exactly [PAD_PRE, PAD_PRE+window_len], then a deterministic sanity check
  (cut must decode; duration within ±2.0 s of the requested window) with whole-pull
  retries up to MAX_TRIES, then a fail-closed raise. Why: the 2026-08-11 design's
  perceptual admission was still lighting-dependent at its edges and false-refused
  correct footage — 11 dusk windows in the .va-24h backfill were right-camera footage
  refused for a lighting mismatch. Validation against the real LNR608 (2026-08-12,
  scratchpad detpull/): padded pull + PTS cut was clean in 7/7 windows across every
  lighting mode (deep-night IR, morning, late-morning, noon, afternoon, late-evening,
  late-night; max dHash-from-consensus ≤ 2 — night IR identical to noon); the same
  window pulled twice was BYTE-IDENTICAL (frame spread 0: 676/676, 666/666, 646/646);
  a real full-res PTS cut of a 20 s target measured exactly 20.0 s. The §5d seek
  lead-in is a bounded head contamination (~1-2 s measured; single-request purity
  1.000 over 300+ prior pulls), so the 10 s pre-pad discards it deterministically —
  window identity is trusted from the request (the stored file is single-camera).
  A clean no-seek whole-file read (RPC_Loadfile / loadfile-by-fileName) is blocked on
  this 2017 firmware (all forms "Invalid Request"/400), which is why we still pull by
  time and cut client-side. DELETED wholesale: `ReferenceLibrary`, `consensus_hash`,
  `self_distances`, `longest_clean_run`, `dhash`, `hamming`, `_frame_hashes`,
  `_snapshot_hash`, constants `DHASH_THRESH`/`FPS_SAMPLE`/`MIN_KEEP_S`/`UNDECODABLE`;
  `_pull_window` lost its `refs` param (now `(chan, start, end, out_mp4)` — test
  doubles updated). New: `PAD_PRE=10`/`PAD_POST=2`/`DURATION_TOL_S=2.0` and
  `_probe_cut` (full decode to null muxer; duration from the decoder clock, never
  file size). `<workdir>/nvr_refs/` is vestigial (it was a cache) — safe to delete;
  no migration. The old lighting-mode refusal path (and its
  watermark wedge) is gone, BUT the pull can still fail-closed at the ~6-day RING
  EDGE: the pre-pad can predate surviving footage even though [start,end] lives.
  So `_pull_window` runs two phases — PADDED, then an EXACT-WINDOW fallback (no
  pad, aligned by construction, purity 1.000) — raising only if BOTH fail. A
  window that stays unpullable (genuinely gone) still holds its `va watch`
  watermark and aborts the camera's pass; the fallback NARROWS that interaction
  (recovers ring-edge footage) rather than removing it (review round-1 finding).
  A partially-available pre-pad can shift the clip a few seconds before the
  fallback engages. Timeline caveat otherwise unchanged: t=0 ≈ start_epoch to
  ~1 s. Offline suite: 724 passed / 2 skipped (re-verified after the two-phase fix).
- **2026-08-14 (WRITE PATH — reprocess extends to detection/tracking):** `va reprocess` now also
  re-runs **`object_detector`** in place (`reprocess._reprocess_object_detector`) and rebuilds
  **`object_tracker`** in the SAME pass (detection feeds tracking in one loop, as ingest does), so a
  stale tracker is restamped via `_SATISFIES`, not re-run (2nd active edge alongside
  vlm_captioner→text_embedder). It re-samples at the video's RECORDED fps — from `object_detector`
  provenance, falling back to `visual_embedder` because the silent YOLO-World re-prime bug left the
  detector unstamped on 231 of 238 `.va-24h` windows; an unknown fps is refused → `va reingest --fps`
  — then builds fully and `replace_detections` + `replace_tracks` (rows-first: a mid-run failure
  leaves prior rows intact and the role stays stale). Honors the tracker gate: a profile that
  disables `object_tracker` while keeping the detector gets UNTRACKED detections (track_id NULL) +
  ZERO tracks, same as ingest. Drops the ingest-era `appearance.npz` shard (its per-track payloads
  would dangle after the track replace); new tracks carry `appearance_ref` NULL — Role-12 ReID
  appearance is NOT re-captured on reprocess yet. **For the web agent (`va serve`):**
  `object_detections`/`object_tracks` can now be replaced in place under a running server answering
  `va objects`/`va count` — same replace-window/concurrency shape as the RPRC-1a/1b shard rebuilds.
  Wires the 4th reprocess role; the remaining leaf roles (OCR, actions, STT/diarizer) still →
  `va reingest`.
- **2026-08-17 (roles, typed-query tier TQ1.a):** New contracts module `src/va/contracts/aggregate.py`
  (typed-query-tier-plan.md §3–§6): `TimeWindow{start,end,tz}` (tz REQUIRED + validated — blank or
  unknown IANA zone rejects at model validation; `epoch_bounds()` computes NUMERIC UTC epoch bounds
  in Python, never SQLite `strftime` text), `CountResult`, `ResolutionProvenance`, `EventRow`,
  `Bucket`, `DedupMode`. Purely additive — nothing consumes them yet (the aggregation ops land in
  `pipeline/aggregate.py` next). Evolution idiom matches query_plan.py/evidence.py (`extra="allow"`,
  defaults, attributes bags). The two untracked design docs (typed-query-tier-plan.md +
  typed-query-tier-loop.md) are committed alongside.
- **2026-08-17 (roles, typed-query tier TQ1.b):** New `src/va/pipeline/aggregate.py` opens with the
  category resolve-seam: `resolve_category(category) -> (categories, source)` — the SAME plural-strip
  logic `pipeline.objects._classes` always applied, promoted to a named seam returning the provenance
  source string ("plural-strip"). `_classes` now delegates to it (one source, parity pinned by a
  table test). Behavior of `query_objects`/`count_objects` unchanged. Deliberately NO synonym
  content ("vehicle" does not expand) — that is the human-gated TQ1.b2 / Role-12 taxonomy.
- **2026-08-17 (roles, typed-query tier TQ1.c):** Windowed track selection landed. New storage
  surface: `TrackStore.select_placed(classes, epoch_start, epoch_end, cameras=None) ->
  list[PlacedTrack]` (`PlacedTrack{track, camera, first_seen_epoch, last_seen_epoch}` in
  `storage/structured/tracks.py`) — tracks whose ABSOLUTE start (`videos.start_epoch + first_seen`)
  falls in the half-open [start, end) epoch window; NULL-epoch (A-EV) videos skipped by
  construction; TEXT bounds raise TypeError (SQLite orders numbers below text, so a
  strftime('%s') bound silently matches nothing — the false-0 bug the tier exists to prevent,
  pinned by test). New pipeline op `pipeline.aggregate.select_tracks(categories, window, workdir,
  cameras)` — tz conversion happens once in Python via `TimeWindow.epoch_bounds()`. Additive; no
  existing surface changed.
- **2026-08-17 (roles, typed-query tier TQ1.d):** Identity resolve-seam landed in
  `pipeline/aggregate.py`: `resolve_identities(tracks, mode, min_frames=2) -> IdentityResolution`
  (`Entity{category, camera, first/last_seen_epoch, tracks}`). `mode="raw"` = one track per entity
  after the min_frames flicker filter (parity with `TrackStore.distinct_counts` pinned by test);
  `mode="instance"` is ACCEPTED but falls back to raw with an explicit no-ReID caveat —
  `dedup_mode` provenance reports what actually RAN ("raw"), never the requested mode. Unknown
  modes raise. Additive; Role-12 ReID later swaps only the body + provenance strings.
- **2026-08-17 (roles, typed-query tier TQ1.e):** The windowed count op landed:
  `pipeline.aggregate.count_objects(category, window, workdir, cameras=None, dedup="raw",
  min_frames=2) -> CountResult` — the plan-§5 composition (resolve_category -> select_tracks ->
  resolve_identities), filling total/per_camera/window-echo/ResolutionProvenance, three STANDING
  caveats (raw-upper-bound, parked/"crossed"≠"present", start-only window membership), the
  instance-fallback caveat when requested, a mixed-footage-workdir caveat via
  `Catalog.footage_domains()` (done-only, reused per its convention), and one `EvidenceItem` per
  counted entity (modality "object_count", track manifest in attributes). NB: same NAME as the
  whole-corpus `pipeline.objects.count_objects` by design (the plan's op vocabulary), different
  module + signature. Additive; nothing existing changed.
- **2026-08-17 (roles, typed-query tier TQ1.f):** `pipeline.aggregate.list_events(category,
  window, workdir, cameras, limit=100, dedup, min_frames)` (one `EventRow` per counted entity —
  the rows BEHIND `count_objects`, same selection path so they cannot disagree; absolute order,
  limit-capped) and `timeline_histogram(category, window, workdir, bucket="1h", cameras, dedup,
  min_frames)` (per-bucket entity counts, zeros emitted; buckets are fixed absolute spans aligned
  to window start — a '1d' bucket is 24 h, not a calendar day across DST; counts sum exactly to
  the count op; bucket grammar `<int><s|m|h|d>`, 10k-bucket explosion guard). Named heuristics
  flagged: default bucket "1h", MAX_HISTOGRAM_BUCKETS=10000. Additive.
- **2026-08-17 (roles, typed-query tier TQ1.g — ⚠ touches shared cli.py, additive only):** New CLI
  subcommand group `va aggregate {count,events,histogram} <category> --from <iso> --to <iso>
  --tz <IANA> [--camera ...]* [--dedup raw|instance] [--min-frames N] [--limit N] [--bucket 1h]`
  invoking the TQ1.e/f ops; prints the per-camera table / event rows / bucket chart plus
  resolution provenance and caveats. Existing `va count` untouched (whole-corpus semantics
  preserved). Shape decision recorded: a NEW subcommand group was the smaller-diff option vs
  retrofitting window/tz flags onto `va count`. Ground-truth check on `.va-24h` reproduced the
  hand-computed Aug-11 00:00–12:00 PDT car table exactly (ch2 55 / ch1 22 / 77 total,
  frame_count>=2). CLAUDE.md commands block documents the new surface.
- **2026-08-17 (roles, typed-query tier TQ1.h — ⚠ shared-contract additions, all
  backward-compatible defaults):** Planner-integrated aggregation. `QueryPlan` gains
  `needs_aggregation: bool = False` (args in `params["aggregation"]`: op + category/start/end/tz
  [+cameras/dedup/min_frames/limit/bucket]); new evidence modality string `"aggregate_count"`;
  `pipeline.aggregate.AGGREGATION_TOOLS` (JSON-schema registry) + `dispatch_aggregation(params,
  workdir)` — validates args, runs the op, returns ONE summary EvidenceItem (verbatim
  CODE-COUNTED content; full CountResult/rows/buckets in attributes) or degrades to an honest
  "not run" note (never a fabricated number). `retrieve()` dispatches AFTER fusion/gate/cap
  (code-counted facts are never relevance-gated); `assemble()` dispatches too; `ask()` now leads
  the rendered answer with the aggregate CODE-COUNTED line (before deep-scan's, if both).
  PLANNER_PROMPT is rendered FROM the registry (drift-guarded by test). Golden: harness
  `test_golden_ask` gained an optional per-question `modality:` key (default `deep_scan_count`);
  new fixture `tests/golden_queries/nvr24h_aggregate.yaml` asserts total==77 on `.va-24h`
  (hand-SQL ground truth) — run with GOLDEN_WORKDIR=.va-24h. Web layer: unchanged endpoints;
  polled ask answers may now begin with a "[CODE-COUNTED: ...]" line and notes may carry
  "aggregation caveat: ..." entries.
- **2026-08-17 (roles, typed-query tier — batch-review fix):** Windowed counts now DISCLOSE the
  A-EV exclusion (a combined-commit review caught the silent false-zero: NULL-`start_epoch`
  videos' tracks are invisible to any windowed count). New `TrackStore.window_anchoring(classes,
  min_frames) -> WindowAnchoring{placed_videos, unplaced_tracks}`; `count_objects` leads caveats
  with a NOT-APPLICABLE disclosure when the workdir has zero wall-clock-anchored done videos and
  names the excluded matched-track count whenever un-anchored ones exist (also machine-readable
  in `CountResult.attributes["window_anchoring"]`); planner dispatch DEGRADES to an honest note
  (no "[CODE-COUNTED: 0]") on un-windowable workdirs and appends the exclusion NB to the
  CODE-COUNTED line otherwise; `va aggregate` help + CLAUDE.md note it. Additive surfaces;
  behavior change only in caveat/note text on affected workdirs.
- **2026-08-19 (roles, NVR delivery-verification — ⚠ pull-contract change):** An `nvr://`
  pull can now REJECT or TRIM a delivery on verification, not just on duration. New
  source-agnostic seam `src/va/sources/verify.py`: a PURE, injectable
  `verify_delivery(RequestedWindow, ObservedSignals, ExpectedProfile) -> DeliveryVerdict`
  (accept | trim@k | reject) + extractor Protocols (`DeliveryVerifier`/`SignalExtractor`/
  `TimestampReader`) + `DeliveryRejected`. `NvrRecordedSource` now takes optional
  `verifier`/`timestamp_reader` (defaults: the pure verifier; no clock reader) and runs
  `_verify_and_trim` after the PTS cut inside `_pull_window` — a rejected/unrecoverable
  delivery is retried through both phases then FAILS CLOSED (the existing `RuntimeError`,
  no clip landed). New env var `VA_NVR_MAIN_STREAM` ("WxH@fps", comma-separated; unset =
  stream-identity check inactive). New reusable media primitive
  `va.media.frames.first_frames(path, count)` (true first frames by index — fixes the
  `-vf fps` sampler blindness) and synth helper `va.media.synth.write_frames_video`.
  Addresses the `.va-24h` contamination (`va-24h-data-integrity-investigation.md`): the
  census proved the pre-pad did NOT absorb the seek lead-in (30% cross-camera heads, 4
  sub-stream clips, all passing the duration gate). COVERAGE is honest and partial —
  cross-camera heads are trimmed and sub-streams rejected (when `VA_NVR_MAIN_STREAM` is
  set), but SAME-CAMERA WRONG-WEEK footage (~25 census clips + 21 same-view heads below the
  dHash band) is caught ONLY by the burned-in-clock gate, which ships as a tested,
  injectable seam with NO default OCR reader — the census's mandatory item 1 before a clean
  re-pull, deferred here. `fetch()` now also re-verifies an EXISTING cache/reingest clip so
  pre-gate files can't bypass the gate. No change to `ingest()`/`resolve()` signatures or
  the web contract — `ingest` still drives `fetching→processing→done/failed` and dedups on
  `done`; a pull that fails verification surfaces as an ingest failure (status `failed`)
  exactly like any other unpullable window. Backlog flagged in-code: the default OCR
  clock-reader (item 1), re-deriving `start_epoch` after a head trim, per-channel
  main-stream config, and the recorder-id/multi-NVR identity gap (pre-existing).
