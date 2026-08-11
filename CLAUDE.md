# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A proof-of-concept "Ctrl-F for Video" platform: ingest a video (YouTube URL or local file)
and search it with natural-language text, returning ranked moments (timestamps). **Roles 1
(Scene Detection), 2 (Visual Embedding), 4 (VLM Captioner), 5 (Object Detector), 6 (Object
Tracker), 7 (Action Recognizer), 8 (Speech-to-Text), 9 (Speaker Diarizer), 10 (OCR), and 11
(Reasoning/Planner)** of the planned 11-role pipeline are implemented so far (only Role 3,
cross-modal audio, remains). Design docs:
`plan.md` (implementation plan + role roadmap), `video-analytics-solution-architecture.md`
(the 11 roles + data model), `solution_code_hike.md` (detailed walkthrough of what's built),
`video-analytics-model-analysis.md` (**per-role model-selection decisions + "revisit when…"
triggers** — the place to record/reconsider which model backs each role, e.g. the Role 7
X-CLIP decision).

**Multiple agents work in this repo in separate sessions.** Read `COORDINATION.md` at
session start — it defines ownership boundaries and the cross-layer contract, and has an
append-only log. Log any change to shared interfaces there. The web frontend is planned in
`web-frontend-plan.md`. **Stabilization/QA phase** (traceability → test-authoring UI → failure
root-cause ledger → deferred CI/CD) is planned in `qa-and-traceability-plan.md` — the current
focus is hardening the repo for a stable git baseline, not new roles.

## Commands

Everything runs through a project-local venv (editable install). There is no separate build
step and no linter configured.

```bash
# setup
python3 -m venv .venv
.venv/bin/pip install -e .              # core deps; uses the STUB embedder (no GPU/network)
.venv/bin/pip install -e '.[siglip]'    # optional: real SigLIP (torch+transformers, heavy)
bash scripts/setup-hooks.sh             # activate the trust gates (git hooks) — once per clone/machine; see workflow-trust-plan.md

# tests (34, no GPU/network — they use the stub embedder + synthetic clips)
.venv/bin/pytest -q
.venv/bin/pytest tests/test_e2e.py -q                 # one file
.venv/bin/pytest tests/test_catalog.py::test_get_or_create_is_idempotent -q   # one test

# run with the STUB backends (color-only visual + sidecar transcript; default config)
.venv/bin/va --workdir .va ingest "<youtube-url-or-local-path>"
.venv/bin/va --workdir .va query "red sports car" -k 5      # visual search (Role 2)
.venv/bin/va --workdir .va transcript "the budget" -k 5     # speech search (Role 8)
.venv/bin/va --workdir .va transcript "the budget" --speaker SPEAKER_01  # filter by speaker (Role 9)
.venv/bin/va --workdir .va caption "the kitchen scene" -k 5 # caption search (Role 4)
.venv/bin/va --workdir .va ocr "coors light" -k 5           # on-screen text search (Role 10)
.venv/bin/va --workdir .va actions "driving" -k 5           # recognized actions (Role 7)
.venv/bin/va --workdir .va objects "car person"             # object appearances (Role 5)
.venv/bin/va --workdir .va count "car"                      # distinct instances (Role 6)
.venv/bin/va --workdir .va ask "what color is the car?"     # reasoned, cited answer (Role 11)
.venv/bin/va --workdir .va remove "<uuid|source_key|url>"   # delete a video everywhere
.venv/bin/va --workdir .va reingest "<...>"                 # re-process (model changes)
.venv/bin/va --workdir .va stale                            # videos on an outdated model/config (§6-b)
.venv/bin/va motion-probe "2026-08-02" "2026-08-03"         # query the MotionSource (WS-4; sidecar stub by default;
                                                            #   lnr-eventlog needs VA_NVR_HOST + VA_NVR_USER/PASS env;
                                                            #   VA_NVR_TZ / role-spec `tz:` if the NVR clock isn't system-local)
.venv/bin/va --workdir .va ingest "nvr://1/2026-08-01T12:00:00/2026-08-01T12:00:30"
                                                            # WS4.c: pull+ingest an NVR window (channel/start/end;
                                                            #   naive times = NVR clock tz, VA_NVR_TZ or system-local;
                                                            #   stored URI is canonical UTC; window capped at 120 s —
                                                            #   pull motion episodes, not raw hours).
                                                            #   Same env as motion-probe; ONE loadfile session per
                                                            #   window, verified lighting-independently: dHash
                                                            #   self-consistency vs the pull's own consensus trims the
                                                            #   §5d stale lead-in, and a per-channel ReferenceLibrary
                                                            #   (<workdir>/nvr_refs/, survives cache wipes) rejects
                                                            #   wholly-wrong-camera clips — so backfill of KNOWN modes
                                                            #   works at night/any lighting. A mode the library hasn't
                                                            #   seen is admitted only if it matches a LIVE snapshot
                                                            #   (right camera, current lighting — how day-seeded
                                                            #   channels acquire night mode near-real-time); matching
                                                            #   neither refuses with recovery guidance, so backfilling
                                                            #   a never-seeded mode under mismatched lighting fails
                                                            #   closed. FIRST pull on an empty channel seeds UNVERIFIED
                                                            #   (warned, only after the pull passes verification).
                                                            #   Trim caveat: clips can be seconds shorter than the
                                                            #   window and t=0 ≈ start_epoch only to ~1 s —
                                                            #   PTS-accurate alignment is backlog.
                                                            #   SINGLE-recorder assumption: identity (source_key,
                                                            #   camera `nvr-ch<n>`) has no recorder id — repointing
                                                            #   VA_NVR_HOST at another NVR dedups/links wrongly
                                                            #   (multi-NVR identity is backlog).
                                                            #   Defaults to --profile security;
                                                            #   sets videos.camera_id (`nvr-ch<n>`) + start_epoch BEFORE
                                                            #   roles run, so motion-episodes segments land (needs a real
                                                            #   motion_source — the unconfigured sidecar warns).
.venv/bin/va --workdir .va watch --interval 60             # WS6.b: THE A-LSSRVF orchestrator — per registered
                                                            #   camera, catch up from its durable watermark
                                                            #   (cameras.last_processed_epoch): query the MotionSource,
                                                            #   pull each new motion episode as an nvr:// window,
                                                            #   ingest, advance. --interval 0 = one pass (cron).
                                                            #   Idempotent (source_key dedup + monotonic watermark);
                                                            #   bounded: --lookback-hours (never-watched cameras),
                                                            #   --max-windows/pass (split per camera), --settle lag,
                                                            #   --cluster-gap (pull-episode merge — NOT the
                                                            #   scene_detector gap_s), --open-instant-age (lost-End
                                                            #   recovery bound).
                                                            #   SLA: the NVR ring keeps ~6 days — outages longer than
                                                            #   that are unrecoverable (watcher pulls what remains).
                                                            #   Cameras register on first nvr:// ingest of a channel.
                                                            #   Known interaction: a backfill episode in a NEVER-seeded
                                                            #   lighting mode refuses fail-closed (see nvr:// above),
                                                            #   and the held watermark queues later episodes behind it
                                                            #   until the live lighting rotates to match (≤ ~12 h,
                                                            #   self-heals; loss only if wedged past the ~6-day ring —
                                                            #   one refused device pull is burned per cycle meanwhile).
.venv/bin/va --workdir .va reprocess --all-stale --yes      # re-run stale roles in place (needs --yes to mutate; --dry-run to plan) (§6-b pillar B; text/visual embedders + captioner wired, others → `va reingest`)

# run with the REAL models (SigLIP + Whisper) on GPU; downloads weights on first use
VA_CONFIG_DIR=run-siglip/config .venv/bin/va --workdir .va ingest "<url>"
VA_CONFIG_DIR=run-siglip/config .venv/bin/va --workdir .va query "<text>" -k 5
VA_CONFIG_DIR=run-siglip/config .venv/bin/va --workdir .va transcript "<text>" -k 5

# web UI (browser on the LAN: ingest, play, search-with-click-to-seek; see web-frontend-plan.md)
.venv/bin/pip install -e '.[web,dev]'
VA_CONFIG_DIR=run-siglip/config .venv/bin/va --workdir .va serve --port 8080
                                                            # WS6.a: job queue is DURABLE (jobs table, schema v7) —
                                                            #   a restart RESUMES queued/running ingest jobs exactly
                                                            #   once; pending asks are failed ("resubmit"), never
                                                            #   silently re-run.
```

`run-siglip/config` selects the real backends per role (visual_embedder=siglip,
speech_to_text=whisper, speaker_diarizer=pyannote, vlm_captioner=qwen2.5-vl-7b,
object_detector=yolo-world,
object_tracker=bytetrack, action_recognizer=xclip, ocr=rapidocr, reasoner=qwen2.5-vl-7b,
scene_detector=**pyscenedetect** — the
histogram default merges montage-style cuts: measured 6 vs 71 segments on the same clip,
which silently destroys per-shot captions; `run-claude/config` = same but reasoner=claude-code). Each role
follows the same pattern: a dependency-free **stub** default (hash / sidecar / color /
histogram / iou / motion / rule) so tests run offline, plus a **real** backend behind an optional
extra (`[siglip]`, `[whisper]`, `[diarize]`, `[qwenvl]`, `[yolo]`, `[track]`, `[action]`, `[scenedetect]`, `[ocr]`).
`va objects` = frame appearances (Role 5); `va count` = distinct instances via tracks
(Role 6). **Tracker caveat:** the default `iou` tracker over-counts fast-moving objects at
1 fps sampling (no motion model) — use `bytetrack` for real footage; measured on the
Ferrari clip: iou 38 "cars" vs bytetrack 6. `va actions` = recognized actions (Role 7).
**Action caveat:** X-CLIP scores a **fixed ingest-time vocabulary** (`DEFAULT_INGEST_ACTIONS`,
overridable via roles.yaml `actions:`) per segment and always picks the least-bad label
(softmax over the requested phrases) — so it answers "is *one of these* actions happening"
well (Ferrari → "driving a car" 0.94-0.99) but cannot recognize a specific action the vocab
doesn't list ("counting dresses" came back "dancing"). Arbitrary-action queries need
query-time recognition (the action analogue of GroundingDINO) — not built. An **abstention
foil** (`NO_ACTION = "no particular action"`, always in the candidate set) gives the softmax
somewhere to park probability when nothing fits; when it wins, no event is stored. Measured:
it left confident-correct labels intact (Ferrari 11/11 driving) while trimming the dresses
montage from 29 → 23 borderline labels.

**Role 11 (`va ask`)**: `pipeline/ask.py` runs plan (LLM call 1) → `assemble()` evidence →
keyframes at top moments (per-video `keyframes/` dirs) → reason (LLM call 2, sees images) →
answer rendered with hyperlinked timestamps (YouTube `&t=` deep links). Deep-scan triggers
are defense-in-depth: LLM planner (primary) + closed regex floor (weak-planner/offline
paths) + **self-escalation** (insufficient sparse answer → one deep-scan re-run). When a
deep scan ran, the rendered answer LEADS with the verbatim CODE-COUNTED line. Reasoner backends:
`rule` (stub/fallback), `qwen2.5-vl-7b` (shares the Role-4 model — same ModelManager key,
no extra VRAM), `qwen3-vl-30b-a3b` (local MoE via `VA_CONFIG_DIR=run-qwen3vl/config`;
58 GB weights at `~/qwen3vl`, loaded bf16, golden-set parity with claude-code — decision +
revisit triggers in `video-analytics-model-analysis.md`),
`claude-code` (headless `claude -p` on the local subscription login),
`claude-api` (**placeholder** — pending the ANTHROPIC_API_KEY decision; raises with
guidance). LLM JSON is parsed tolerantly (`parse_json_block`, `coerce_timestamp` — Qwen
really does emit `"3.5s"`); unparseable output falls back to the rule reasoner.

## Commit & review lifecycle (trust gates — full spec: workflow-trust-plan.md)

Git hooks enforce this mechanically (activated per machine by `scripts/setup-hooks.sh`;
already active here). Every commit subject MUST be one of:
- **`need_agent_review: <desc>`** — work for a task/plan chunk is COMPLETE. The
  post-commit hook spawns a fresh reviewer (headless, read-only; rubric single-sourced
  from `.claude/agents/code-reviewer.md`); verdict + findings land in `reviews/`.
- **`wip:` / `checkpoint:`** — deliberately unfinished. Free, but unapproved content
  still gets the backstop review at push; it can never reach main unreviewed.
- **plain subject** — ONLY for finalizing an approved commit (or docs-only branches).

**The committer's full procedure is `/task-commit`**: scope check, combination check
(affected roles×backends×profiles cells + their tests), documentation check (new
surface documented in the same change; unsure → ask in the digest), review loop,
four-section digest, STOP for the human's `touch .commit-approved` (human-only; NEVER
create it), finalize. **Final commit messages describe the change for an uninformed
reader** — shorthand IDs (WT.x/RI.x) only as trailing references. All overrides
(`AGENT_REVIEW=skip`, the `ALLOW_*` family) are human-only.

**CI gates (WT.5–WT.7)** run on GitHub, beyond reach of any local override — though not
absolutely: a `pull_request` run executes the PR's own copy of the workflows and of
`check_critical_paths.sh`, so a PR can weaken the checks that gate it. Branch protection
pins the required check NAMES server-side (deleting one leaves it unreported and the merge
blocked), but weakened check *contents* are caught only by human review of `.github/` and
`scripts/`. The gates are:
`offline-tests` (the full offline suite — a red suite blocks merge), `evidence` (the PR
body must carry real pytest counts, not the phrase "tests pass" — run `/verify` to
generate the block), and `critical-paths` (PRs touching `scripts/critical_paths.txt`
entries need the `human-reviewed` / `golden-verified` label). Checks re-run on `edited`
and `labeled`, so fixing a body or adding a label needs no new commit.

**What the labels mean (D9):** they are the human's *attestation*, not proof. Agent
sessions share the human's GitHub credential, so the guards blocking `gh`/`curl`/etc from
applying labels are a speed bump against accident, not a guarantee. **Never apply a review
label yourself** — the label is worthless if you do, and the bounded-review contract (P5)
is the only thing that depends on it.

**Session guards (WT.3)** are active via `.claude/settings.json`: bash/path guards
block gate-bypass commands and edits to gate machinery (`.githooks/`, `.claude/`,
`.github/workflows/`, trust scripts, `reviews/`, the sentinels); a Stop gate blocks
ending a turn with a red offline suite. Gate maintenance requires the human's
`touch .guard-override` (remove it after) — it relaxes ONLY the machinery-write
guards; approval/waiver/audit rules (sentinels, `AGENT_REVIEW`, `gh pr merge`,
`reviews/`) stay enforced even then. Hooks snapshot at session start — restart
sessions after guard changes.

## Heuristics & validation (engineering conventions)

- **Never introduce hardcoded content, magic values, or canned heuristics silently.** Flag
  them explicitly when proposing them, explain the choice, and ask before relying on them
  (a hardcoded `scan_target` string had to be challenged by the user; don't repeat that).
  Hardcoded *structure* (mechanisms, budgets) is fine; hardcoded *content* (subjects,
  domain strings) almost never is — derive content from the user's query or the data.
- **Determinism is not correctness.** For counting/detection features, validate output
  against known ground truth before declaring success (the deep-scan counted 70-99 "dress
  changes" with perfect stability; truth was ~12-15 — it was reproducibly counting camera
  cuts). Report results alongside the ground-truth comparison.

## Lessons (append via `/lesson`)

Corrections that cost something to learn, kept so they are never re-learned. Add with
`/lesson <what you got wrong and why>` — one dated line each, newest at the bottom.

**Pruning rule:** when this list passes ~20 lines, fold the stable entries into the
relevant prose section above or convert them to hooks, and delete them here. A bloated
CLAUDE.md gets skimmed and then ignored, which silently disables this whole advisory
layer. Instructions decay; hooks don't — if a lesson is a mechanical invariant
("always/never do X"), the right home for it is `.claude/hooks/` or `.githooks/`.

- 2026-07-28: Never `git add -A <dir>` in this worktree — other sessions leave
  uncommitted files there, and sweeping one in shipped a test importing an uncommitted
  module, which would have turned CI red. Stage explicit paths.
- 2026-07-28: When a gate blocks a legitimate action, change the approach — don't reach
  for the human-only override. The test-deletion guard was right that removing a file
  from a commit looked like deleting tests; rebuilding the commit was the correct fix.
- 2026-07-28: A regression test that cannot reproduce the original failure is decoration
  — the SIGPIPE test wrote 14 KB against a 64 KB pipe buffer and would have passed
  against the very bug it was named for. Make the test fail on the old code first.
- 2026-07-28: When a rule must understand a command's grammar, parse it — don't iterate
  on regexes. Three positional patterns for `git commit -n` each missed a spelling;
  shell tokenization closed all of them at once.
- 2026-07-28: An over-broad guard is a defect too. Blocking any command whose text
  merely *mentioned* a review label broke `gh pr create --body "<template>"` — every
  false block pushes toward a workaround, which is the behavior guards exist to prevent.
- 2026-07-28: A polling loop must not observe itself — a `pgrep -f X` / `ps | grep X`
  inside a loop matches the loop's OWN command line (the pattern is written right there),
  so it never terminates. Use the bracket trick (`[X]`) or match by PID. *A hook to
  enforce this is deferred to its own PR (see WT.8 as-built); advisory until then.*
- 2026-07-28: Prose is not an action — three guards in a row false-blocked legitimate work
  by matching command TEXT (a label name in a PR body, "until" inside a heredoc string).
  Key rules on token position and flags, never on a word appearing somewhere.
- 2026-08-03: Best-effort except blocks swallow test-double signature mismatches —
  widening `get_ingest_classes()` to take `cfg` broke five zero-arg `lambda:` doubles as
  silent 0-count assertion failures, never a visible TypeError. Before widening an
  internal callable's signature, grep tests/ for lambda doubles of it.
- 2026-08-03: A batch finalize is not exempt from the digest — a six-item squash's
  combined message was composed AFTER `.commit-approved` was touched and consumed the
  sentinel unvetted, letting plan-ID shorthand into shipped history. Present any
  newly-composed final message in a digest BEFORE consuming the sentinel; wording has
  no mechanical gate, so digest review is the only check.
- 2026-08-04: Never launch a full-suite run while another is live — every turn end ALSO
  spawns a Stop-gate suite, and five piled up: an 87 s suite ground to 46 min, two tests
  flaked with OSErrors, a push died on a phantom red. `pgrep -f '[p]ytest -q'` first;
  and `pytest | tail` returns TAIL's exit code — never gate a `&&` chain on a pipeline.
- 2026-08-05: The bracket trick immunizes only the pgrep PATTERN — a wait-loop chained
  as `until ! pgrep -f '[p]ytest -q'; …; pytest -q` matches the LITERAL `pytest -q`
  later in its OWN command line and spins forever (11 h lost overnight). A poll must
  not share a command line with the thing it polls for: separate calls, or match PIDs.
- 2026-08-08: A test that never CONSTRUCTS its scenario is decoration even when
  green — three in one branch: a catalog-failure test whose missing workdir was
  silently created empty, a two-source-type test that ingested one video, and a
  floor pin whose thresholds didn't bracket the stub's real scores (1.0/-0.1).
  Assert observable BEHAVIOR; run it against the broken code before believing it.

## The two things most likely to trip you up

1. **Default config uses a stub, not a real model.** `config/roles.yaml` sets
   `visual_embedder.model: hash` — a deterministic *color-aware* stub (a red frame matches the
   word "red"). It exists so the whole pipeline + tests run with no GPU/network/downloads. For
   real semantic search you must select SigLIP via **`VA_CONFIG_DIR=run-siglip/config`** (a
   separate config dir kept apart so tests still use the stub).
2. **Ingest and query must use the same embedder config.** The stub is 64-dim, SigLIP is
   1152-dim — different vector spaces. Shards are now **embedder-tagged** and the query-time
   guard SKIPS shards whose embedder ≠ the current one (logging a count on `store.skipped`), so
   a mixed workdir degrades to fewer results + a warning, never silently-wrong ones. Switching
   models = `va reingest <video>` (per video, same workdir) to re-tag + rejoin them. Workdir
   layout v2: `catalog.db` (ONE shared DB for all videos) +
   `videos/<key16>-<slug>/` per-video dirs (media + `vectors.npz` shard + `keyframes/`) +
   transient `cache/`. The shards form one logical index — search spans all videos.
   `va remove <video>` deletes everywhere; pre-v2 workdirs: `va migrate-layout`.

## Architecture: the hosting-agnostic spine

The central design constraint (the DGX Spark may not hold every model, so any role must run
locally OR remotely without caller changes) shapes everything. Three seams per role:

- **Role interface** — `src/va/roles/<role>.py` is a `Protocol`. Callers depend only on this.
- **Adapters** — `src/va/adapters/<role>/*` are interchangeable backends: `*_inproc` (in-process),
  and (future) `http_client` / cloud clients. For Role 2: `hash_inproc.py` (stub) and
  `siglip_inproc.py` (real).
- **Registry** — `src/va/registry.py` reads config and returns the right adapter. Swapping a
  backend is a one-line edit in `config/roles.yaml`; no pipeline code changes.

`src/va/configuration.py` merges three layers into one `RoleConfig`: `roles.yaml` (which
backend+model per role) + the active **hardware profile** `config/profiles/<name>.yaml`
(per-model load params: device/dtype/weights) + an optional **footage profile**
`config/profiles/footage/<name>.yaml` (per-input-domain role overrides, deep-merged over
the role specs; default `generic` = no-op, missing file tolerated). Select via
`load_config(footage_profile=...)`, roles.yaml `active_footage_profile`, or per-ingest
`va ingest --profile <name>` (validated up front, source-derived default, recorded as
`videos.profile`; NULL = pre-profile ingest). Ingest pins the overlaid config and passes
it to every role getter, so a profile GATES roles (`enabled: false` skips a best-effort
role; dependents skip with their parent — STT→diarizer, detector→tracker). Skipped roles
are not provenance-stamped, and `va stale` EXCLUDES them (with the same dependency
closure) while the profile disables them AND they are unstamped; a role that RAN before
the profile was edited to disable it reads stale (its rows contradict the profile —
reingest purges them). Profiles also override vocab (`classes:`/`actions:`) and carry profile-wide knobs on
`Config.footage` — `retention_days` / `time_model` (recorded-but-inert until P7.a and
WS-3 presentation work consume them) and `deep_scan` (**consumed since R11.a**: the
dominant video's RECORDED profile gates `va ask` deep scans — security sets "off";
also R11.a removed the hardcoded outfit fallback target entirely: no derivable
query subject = no sweep, never canned content). Unknown or ill-typed knobs fail at
load; quote `deep_scan: "off"` — bare `off` is YAML false. `security` (A-LSSRVF) ships: skips speech
roles 8/9, narrows detector vocab, and selects the **motion-episodes** Role-1 backend
(WS4.b: segments = clustered MotionSource episodes mapped epoch→relative via
`videos.start_epoch`; knobs `pad_s`/`gap_s`/`min_span_s`/`query_margin_s` on the
scene_detector spec (the margin widens the MotionSource query past the chunk bounds —
live-validated: the NVR logs episode End markers at/just past the chunk edge, and an
exact-range query collapses the episode to an instant);
`SceneDetector.detect` gained an optional `SceneContext` — chunks with NO `start_epoch`,
e.g. any plain A-EV ingest, degrade to ONE full-span segment with a warning, and a
MotionSource failure degrades the same way rather than aborting the ingest).
Core roles (scene detect, embedders) ignore `enabled`.
Since R11.a the profile also gates deep scans, and since **R11.b the retrieval relevance
floor** (`retriever:` block) is resolved PER VIDEO from that video's recorded profile, so
each video is judged on floors calibrated for its own footage. In the **run-\*/config**
dirs `security` sets `min_cosine: 0.0` (measured — see the profile comment); the default
`config/` dir declares no `retriever` role at all, so it does no thresholding anywhere.
**Per-video floors do not make a MIXED workdir safe**: gather and fusion still rank every
video's frames against each other on a cosine that does not compare across domains, so
A-EV frames (relevant 0.11–0.18) bury A-LSSRVF ones (0.020–0.077) before the gate is
reached. Keep A-LSSRVF chunks in their own workdir. `retrieve` flags a mixed **workdir**
in the evidence notes — deliberately the workdir and not the surfaced candidate pool,
because total burial keeps the weaker domain OUT of the pool and would silence the
warning exactly when it matters; don't "simplify" it back. Domain-aware gather/fusion
(per-domain top-k + per-domain normalization) is loop backlog.
**Caveat: do NOT override embedder models in a footage profile yet** — ingest tags shards
honestly from the overlay, but the query path is profile-unaware EVERYWHERE ELSE (it loads
base config), so such shards get tag-skipped at query time and the video vanishes from
search. Query-side profile awareness beyond the gate is future work (loop backlog) — the
SR.6 VLM-verifier floor is the next known gap. `VA_CONFIG_DIR` overrides the config
directory.

## Architecture: two pipelines over shared stores

Both live in `src/va/pipeline/`. The universal join key is `video_id`.

**Ingest (write path, `ingest.py`):** `resolve_source(uri)` → `VideoSource.resolve()` (cheap,
yields a stable `source_key` for dedup) → **`Catalog.get_or_create()`** (skips if already
`done` — this is the idempotency point) → `source.fetch()` (yt-dlp download ≤480p, or locate
local file) → **`SceneDetector.detect()` → `SegmentStore` (Role 1 segments)** →
**`VLMCaptioner.caption()` per segment keyframe → `segments.caption` (Role 4, best-effort)** →
**`SpeechToText.transcribe()` (Role 8) → `SpeakerDiarizer.diarize()` → `assign_speakers()`
joins turns onto the lines by temporal overlap → `TranscriptStore` (Roles 8+9, best-effort)** →
**`OcrReader.read()` → `OcrStore` (Role 10, best-effort)** →
**`ActionRecognizer.recognize()` per Role-1 segment → `ActionStore` (Role 7, best-effort)** →
`media.sample_frames()` at N fps → `VisualEmbedder.embed_image()` (batches of 32) →
`VectorStore.add()` tagged with `video_id`+`timestamp` → mark `done`. There are five query
paths: `query.py` (visual, Role 2), `caption.py` (scene descriptions, Role 4),
`transcript.py` ("what was said", Role 8), `ocr.py` (on-screen text, Role 10), and
`actions.py` (what happens, Role 7); the
`va ask` planner (Role 11) unifies them via QueryPlan tier flags.

**Query (read path, `query.py`):** `embed_text()` → `VectorStore.search()` (cosine top-k) →
join each hit's `video_id` back to the catalog for `source_uri` → ranked `SearchHit`s. Text and
images share one vector space, so search is just nearest-neighbor between a text vector and
pre-computed frame vectors.

Supporting layers:
- **`src/va/runtime/`** — `ModelManager` (singleton `MANAGER`) loads models once and caches
  them; in-process adapters get models via `MANAGER.get()`, never loading directly. `device.py`
  falls back cuda→cpu so the same config runs on the Spark or a laptop.
- **`src/va/sources/`** — `youtube.py` (any URL form → 11-char video_id = `source_key`),
  `local.py` (sha256 = `source_key`); `base.resolve_source()` dispatches.
- **`src/va/storage/`** — the **central correlation DB** is one SQLite file (`<workdir>/catalog.db`)
  whose full schema is `structured/schema.py`: `videos` (catalog/dedup; `camera_id` links
  a chunk to its camera and `start_epoch` is the absolute UTC base of t=0 — both NULL for
  standalone A-EV videos; stored timestamps stay video-relative, translation lives in
  `pipeline/timeline.py`) + `cameras` (WS-3 camera entity) + one table per role
  (`segments`, `object_tracks`, `object_detections`, `action_events`, `transcripts`, `ocr_results`),
  all keyed by `video_id`. All tables are created up front; complex queries will correlate roles
  via temporal SQL joins on `video_id` + time. Today `catalog_sqlite.py` (videos) and
  `segments.py` (Role 1) write to it. Vectors live separately in `vector/numpy_flat.py` (brute-force
  cosine), also keyed by `video_id` — TWO stores per video since WS4.d: `vectors.npz` (Role-2 frame
  embeddings, searched by `va query`) and `appearance.npz` (one crop embedding per object track,
  meta-tagged `space: appearance-crop`; `object_tracks.appearance_ref` points into it — Role-12
  ReID schema insurance, NOT searched by any query path yet). Everything is behind interfaces so
  Postgres / Milvus swap in later.
- **`src/va/contracts/`** — pydantic schemas (`Video`, `ResolvedVideo`, `FrameEmbedding`,
  `SearchHit`, `Segment`, `TranscriptLine`) mirroring the architecture doc's data model, plus
  the **runtime contracts** `QueryPlan`/`Evidence`/`Answer` (`query_plan.py`, `evidence.py`).
  The runtime contracts are evolution-tolerant by rule: every field has a default,
  `extra="allow"` preserves unknown fields across round-trips, modality-specific payload goes
  in `attributes`/`params` dicts. `pipeline/evidence.py` assembles an `Evidence` bundle from a
  plan's flags (skipping-and-noting unavailable/unknown tiers) — the input for future Role 11.

## Testing

Tests use the stub embedder + synthetic color clips (`media/synth.py`) so they assert real
retrieval behavior deterministically without models. See `tests/test_e2e.py` for the full
ingest→query path. **Golden-query fixtures** for real videos live in `tests/golden_queries/`
(`<video_id>.md` human + `<video_id>.yaml` machine-readable assertions); they are generated by
a vision+adversarial-verify agent workflow (see that dir's README) and split into `match` /
`no_match` / future-role queries. Two gated harnesses run them against a pre-ingested
real-model workdir (`RUN_GOLDEN=1 VA_CONFIG_DIR=run-claude/config GOLDEN_WORKDIR=.va-shots
.venv/bin/pytest -m golden`): **`test_golden_queries.py`** (per-modality `queries:` +
`semantic_text:` + `diarization:` blocks) and **`test_golden_ask.py`** (deep-scan counts).
Visual match = strongest hit *inside* `time_range` ≥ `min_score` (calibrated to **0.10** on
the first real run). Known model limitations carry `xfail: "<reason>"` (strict, so a better
model alerts). A query may set `verify: true` to route through the SR.6 VLM verifier (Qwen
re-checks SigLIP/YOLO results; selective, since blanket verification erodes recall). In the
live path the Role-11 planner auto-sets `QueryPlan.needs_visual_verification` (applied in
`retrieve()`); `va query --verify` is the manual switch. Current: 83 pass / 1 xfail / 0 fail
+ 2 ask questions pass. (NB: a fixture audit found two defects — cobra "indoor kitchen" was a
HALLUCINATED match (no kitchen; → no_match), and ferrari "grandstands" was passing on a FALSE
POSITIVE (real but distant grandstands SigLIP can't retrieve; → narrowed + xfail). Treat
low-score vision-verified MATCH fixtures as audit candidates; a green test can still be wrong.)

## Gotchas specific to this repo

- **No system ffmpeg** — frame decode and yt-dlp merging use the binary bundled by
  `imageio-ffmpeg`. `sources/youtube.py` symlinks it as `ffmpeg` and passes `ffmpeg_location`.
- **SigLIP** needs `protobuf` (in the `[siglip]` extra) for its tokenizer, and on
  transformers v5 `get_image_features`/`get_text_features` return an output object, not a
  tensor — `siglip_inproc.py` unwraps `image_embeds`/`text_embeds`/`pooler_output`.
- **SigLIP scores are small in absolute terms** (sigmoid training): relevant ≈0.11–0.18,
  irrelevant ≈0 or negative. Rank/relative gap matters, not magnitude.
- **Query always returns top-k regardless of score** — there is no relevance threshold yet, so
  a no-match query still prints hits (with low/negative scores).
- **paddlepaddle segfaults on this aarch64 box** (inference predictor init, PIR param
  loading; v5/v6 models, mkldnn on/off — all crash). Role 10 therefore uses RapidOCR
  (same PP-OCR models on onnxruntime). Don't "upgrade" the OCR backend to paddleocr
  without re-testing predictor init on aarch64.
- **Role 9 (pyannote) has FOUR setup gotchas — validated working 2026-06-15.** (1) pyannote
  **3.x crashes on import** here (uses `torchaudio.AudioMetaData`, removed in torchaudio 2.11 /
  torch 2.12+cu130); the `diarize` extra pins `>=4`. (2) The pipeline composes **four
  separately-gated HF models** — accept ALL of `pyannote/speaker-diarization-3.1`,
  `segmentation-3.0`, `speaker-diarization-community-1`, and `embedding` (wespeaker is ungated);
  each 403s individually until accepted. Authenticate with a read-scope token via
  `huggingface_hub.login(token=...)` (the `huggingface-cli` entrypoint may be absent; use the
  Python `login()` or `hf`). (3) pyannote 4.x decodes audio via **torchcodec**, which needs
  FFmpeg shared libs this box lacks → the adapter loads the WAV itself (`load_wav_mono`) and
  passes a `{"waveform","sample_rate"}` tensor to bypass it. (4) pyannote 4.x's pipeline returns
  a **`DiarizeOutput`** (use `.speaker_diarization`), not an `Annotation`. Diarization is
  best-effort in ingest — any of these failing just leaves `transcripts.speaker` NULL, never
  aborts the ingest. The **sidecar** stub (`<video>.diarization.json`) covers offline tests.
  Ungated alternative if gating is a blocker: **NeMo Sortformer** (Apache-2.0).
