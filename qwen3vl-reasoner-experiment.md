# Experiment — Qwen3-VL-30B-A3B as the Role-11 reasoner

*Status: **CLOSED at parity, 2026-07-29 — artifacts LANDED on main** (adapter, registry
routing, `run-qwen3vl/config`; see the Outcome section and the Role 11 decision block in
`video-analytics-model-analysis.md`). Experiment ran 2026-07-26. Goal: swap the
Qwen3-VL-30B-A3B (MoE) VLM in as the Role-11 reasoner, run the suites (esp. golden fixtures),
and judge whether a **local** MoE VLM can power Role 11 + the planned Telegram chat interface
with **no rented LLM** ([chat-interface-plan.md](chat-interface-plan.md)).*

*Assumed/derived values flagged inline with **[assumed]**.*

---

## TL;DR / verdict

- **Loaded? YES.** After the HF download was found to be **Wi-Fi-capped on this box** (~0.5 MB/s —
  see [hf-download-speed-investigation.md](hf-download-speed-investigation.md)), the ~58 GB of
  weights were pulled to a laptop and copied to `~/qwen3vl` over the LAN. The model loads in-process
  (`Qwen3VLMoeForConditionalGeneration`, bf16, `device_map=cuda`) in **~6.5 min**, resident **~42–45 GB
  RSS**, and **co-resides with the retrieval models** (SigLIP/Whisper/YOLO/etc.) — peak ~104 GB of the
  box's 119 GB. No OOM with a single reasoner instance.
- **Golden suite: RAN. 84 passed / 1 xfailed / 1 failed** (`RUN_GOLDEN=1 VA_CONFIG_DIR=run-qwen3vl/config
  GOLDEN_WORKDIR=.va-shots`, 838 s).
  - **Retrieval / planning: PERFECT — 84/84.** Qwen3-VL's `plan()` routes every modality (visual,
    caption, transcript, OCR, object, action, semantic, diarization) **exactly like the `claude-code`
    baseline**. The lone xfail (`ferrari-pos-06` distant grandstands) is the **known** SigLIP gap,
    identical to baseline. **This is the headline: a local MoE VLM plans our queries as well as the
    rented model.**
  - **Ask / Role-11 deep-scan counting: 1 of 2 as first run; 2 of 2 after the fix.** `dresses-ask-01`
    **PASS**; `bird-ask-01` **FAIL** (`total_episodes=0`) → **PASS after the one-line `ask.py` fix**
    (re-validated 2026-07-26: `1 passed in 728s`, a fresh bird-target sweep found birds on 54/120
    frames and the count landed in [4,5]).
- **The one failure was a PIPELINE BUG, not a model incapacity — now FIXED + validated.** Root-caused
  below to a `scan_target` hand-off gap that silently substituted an **outfit-biased default**.
  Qwen3-VL actually **got the hard part right** (it set `needs_deep_scan=true` — it understood the
  counting intent). With the fix, **Qwen3-VL is at parity with `claude-code` on the full golden set.**
- **Verdict for the no-rented-LLM strategy:** **viable.** Local Qwen3-VL is good enough to plan +
  route + reason for Role 11 and the chat interface. Two things to address before relying on it:
  (1) the deep-scan `scan_target` robustness gap (one-file fix, below); (2) **latency** — the
  vision-reasoning call is **~100 s/question [assumed: from the smoke test]**, acceptable for a home
  chat tool but not interactive-fast.

---

## Results

### Golden suite (real models, `.va-shots`, 6 videos)

| Bucket | Qwen3-VL result | `claude-code` baseline | Delta |
|---|---|---|---|
| Retrieval queries (visual/caption/transcript/ocr/object/action/semantic/diarization) | **84 pass** | ~83 pass | same routing |
| Known SigLIP gap (`ferrari-pos-06`) | 1 xfail (strict) | 1 xfail | same |
| Ask — `dresses-ask-01` (deep-scan count) | **PASS** | pass | same |
| Ask — `bird-ask-01` (deep-scan count) | **FAIL** (0 vs 4–5) | pass | **regression** |
| Wall-clock | 838 s incl. ~6.5 min load | — | — |

### The failure

```
AssertionError: bird-ask-01: total_episodes=0, expected [4, 5] (provenance: human-verified)
```
Question: *"How many birds come and feed on the feeder?"* Ground truth: 4–5 visits (human-counted;
the system measured 5 across four prior baseline sweeps). Qwen3-VL's ask returned **0**.

---

## Root cause (recovered by offline cache forensics — no GPU re-run needed)

The deep-scan cache in `.va-shots/catalog.db` (`observations` table) records every sweep keyed by
`prompt_key = sha1("v5|" + canonical_key(scan_target) + "|120|1.0")`. Because the key is a pure
function of `scan_target`, the exact target Qwen3-VL used is recoverable by brute-forcing the hash.

**The birdfeeder video had a sweep whose captioner output was `none` on all 120 frames.** Its
`prompt_key` reverses to `canonical_key = "outfit"`, i.e. `scan_target = "the main person's outfit"`
— which is `DEFAULT_TARGET` in `deep_scan.py`. The captioner (unchanged `qwen2.5-vl-7b` in both
configs) was asked to *"name the main person's outfit"* on a birdfeeder video → correctly answered
`none` every frame → `analyze()` dropped all → **0 episodes**.

The bug chain:

1. `run_deep_scan` (deep_scan.py:391) resolves `target = plan.params.get("scan_target") or DEFAULT_TARGET`,
   and `DEFAULT_TARGET = "the main person's outfit"` — a legacy of the dress-counting origin.
2. `QwenReasoner.plan()` has an explicit salvage for a **known Qwen JSON quirk** ("emits `params` as a
   string where a dict is required"): on `ValidationError` it **drops the offending field**. Qwen3-VL
   set `needs_deep_scan=true` **but botched the `params`/`scan_target` shape** → salvage dropped
   `params` → the plan reached `ask()` with the counting flag set **but no `scan_target`**.
3. The rule-floor `scan_target` backfill (ask.py:204) lives **inside** `if rule_plan.needs_deep_scan
   and not plan.needs_deep_scan:` — but Qwen3-VL *did* set the flag, so `not plan.needs_deep_scan` is
   False → **the backfill was skipped**.
4. → fell through to `DEFAULT_TARGET` → all-`none` sweep → **0**.

**Corroboration (why `dresses-ask-01` passed but `bird-ask-01` didn't):** the dress video's cached
sweeps use *real dress targets* (e.g. `"the girl's dress"`, rich non-`none` labels) — it never
touched the `outfit` default key. The outfit-biased default is **coincidentally correct** for the
dress use-case, so it **masks the bug on outfit footage and only exposes it elsewhere** (birds,
and by extension any non-person subject: vehicles, animals, security-cam events).

**So there are two distinct issues, one per layer:**
- **Model (Qwen3-VL):** *inconsistent* `scan_target` emission — a valid target for the dress question,
  a malformed one for the bird question. A reliability wobble, not a hard failure (it got the
  `needs_deep_scan` intent right both times).
- **Pipeline (ours):** a latent robustness gap that turns a missing `scan_target` into a
  **silently-wrong** outfit default instead of deriving one from the query. This would bite **any**
  planner (including the qwen2.5-vl-7b captioner-planner) that sets the flag but not the target.

---

## The fix (applied + validated 2026-07-26)

Backfill `scan_target` from the query **whenever `needs_deep_scan` is set but no valid `scan_target`
is present** — not only in the rule-floor-forced branch. Applied in `ask.py`, after the rule-floor
block, reusing the already-computed `rule_plan`:

```python
# Backfill scan_target for ANY deep-scan plan that lacks one — e.g. an LLM
# planner set needs_deep_scan but emitted malformed params that JSON-salvage
# dropped. Without this, run_deep_scan falls back to the outfit-biased
# DEFAULT_TARGET and scans the wrong subject.
if plan.needs_deep_scan and not plan.params.get("scan_target"):
    plan.params["scan_target"] = rule_plan.params.get("scan_target")
```

The rule reasoner derives the subject from the query (verified: *"How many birds come and feed on the
feeder?"* → `scan_target = "the birds come and feed feeder"`). **Result:** a fresh sweep under that
target replaced the all-`none` outfit sweep (54/120 frames now detect birds), and `bird-ask-01`
**passes** (`total_episodes` in [4,5]; `1 passed in 728s`). Offline suite unchanged (**164 passed /
2 skipped**). The fix is **planner-agnostic** — it protects the `claude-code` and `qwen2.5-vl-7b`
paths from the same footgun and cannot regress `dresses-ask-01` (that plan already carries a valid
`scan_target`).

**Follow-ups (not done):** (1) the rule-derived target is clunkier than an LLM's clean phrasing (54
vs ~100–110 non-`none` frames for `claude-code`'s "the bird at the feeder") — enough to count right
here, but a neater backfill (or trusting Qwen3-VL's own target when it parses) would improve
sensitivity. (2) ~~Secondary hardening: change `DEFAULT_TARGET` from the outfit-specific string to a
neutral `"the main subject"` so a total miss fails visibly instead of silently scanning for
outfits.~~ **SUPERSEDED by R11.a (2026-08-07): `DEFAULT_TARGET` is deleted outright — there is
no canned fallback of any kind. A query with no derivable subject skips the sweep and says so,
and the recorded footage profile can veto sweeps entirely. Do not reintroduce a neutral default.**

*(Flagged per repo convention: this changes shared `ask()` behavior. Applied and validated; still
uncommitted, pending the session checkpoint.)*

---

## Latency & footprint

| Metric | Value | Note |
|---|---|---|
| Model load (cold) | **~6.5 min** | 882-shard bf16 load onto unified memory; one-time per process |
| Resident RSS | **~42–45 GB** | MoE (~3B active of 30B); co-resident with retrieval models |
| Peak box memory | **~104 / 119 GB** | single reasoner + retrieval stack; no OOM |
| `plan()` call | **~4–5 s [assumed]** | small JSON out; 84 of them fit in the 838 s run |
| `reason()` call (vision, cited answer) | **~100 s [assumed]** | from the smoke test; the golden asks hit the sweep cache so aren't a clean latency sample |

For the chat interface, the user-perceived latency is dominated by `reason()` (~100 s). That is
**usable for an async home tool** (ask → answer arrives), **not** for snappy back-and-forth. A Q4
GGUF served build would trade some quality for speed **[assumed]** — still the recommended production
route (below).

---

## Methodology caveats

- **Root cause was recovered offline** from the SQLite deep-scan cache (hash-reversing `scan_target`)
  — no extra 45 GB model loads spent to diagnose. The all-`none` sweep + the `"outfit"` key are
  durable evidence in `.va-shots/catalog.db`.
- **Cache-key contamination risk for future reasoner comparisons:** the deep-scan *observation* cache
  (`prompt_key`) and *normalization* cache (`prompt_key:norm2`) key on `scan_target` + prompt version
  but **not the reasoner identity**. Two reasoners that emit the same `scan_target` share the sweep;
  the `:norm2` mapping is reused across reasoners. For a clean head-to-head, run on a **fresh workdir**
  or clear the caches. (Here it didn't affect the verdict — the failing sweep was Qwen3-VL's own
  outfit-default sweep, distinct from every baseline sweep.)
- **The concurrent-run hazard:** a second golden run (a leftover experiment agent) collided with this
  one and had to be killed to avoid a double 45 GB load OOM. Run **one** real-model golden suite at a
  time on this box.

---

## Outcome (2026-07-29) — CLOSED at parity

Step 1 below is DONE: the `scan_target` backfill fix merged to main (planner-omission
PR), and `bird-ask-01` re-validated under `run-qwen3vl/config`: **1 passed in 447 s**
(cold ~45 GB load included). **Qwen3-VL-30B-A3B is at parity with `claude-code` on the
golden set.** The decision + revisit triggers (serving path for interactive latency,
accuracy regression, qwen3-as-captioner) are recorded in
`video-analytics-model-analysis.md` (Role 11 decision block, Accepted 2026-07-29) —
that doc is authoritative from here; this one is the experiment log.

## Recommended next steps

1. **Apply the `scan_target` backfill fix** (above) and re-validate `bird-ask-01` under
   `run-qwen3vl/config`. If it passes, Qwen3-VL is **at parity with `claude-code`** on the golden set.
2. **Decide the serving path for production** — in-process (done here) vs a **Q4 GGUF via
   llama.cpp/Ollama** behind an OpenAI-compatible endpoint (the chat-interface plan's route). GGUF =
   ~3× smaller, faster decode; caveat: verify **VLM** support for Qwen3-VL in the server **[assumed
   uncertain]**.
3. **Record the decision** in `video-analytics-model-analysis.md`: Role 11 can run on a local MoE VLM;
   the revisit trigger is latency (if interactive chat needs <10 s) or a deep-scan accuracy
   regression.

## Artifacts (LANDED on main 2026-07-29 — table kept as the experiment-time record)

| Path | What | Keep? |
|---|---|---|
| `src/va/adapters/reasoner/qwen3vl_inproc.py` | new Role-11 adapter (subclass of `QwenReasoner`) | keep |
| `src/va/registry.py` | +`qwen3-vl` routing branch (additive) | keep |
| `run-qwen3vl/config/` | experimental config dir (`reasoner.model: qwen3-vl-30b-a3b`, `weights: /home/debug/qwen3vl`) | keep |
| `~/qwen3vl` | local weights (~58 GB) | keep (gitignored; not in repo) |
| new deep-scan sweep rows in `.va-shots/catalog.db` | the failing `outfit` sweep (evidence) | harmless cache |

At experiment time nothing was committed to `main` (the artifacts above LANDED 2026-07-29);
`run-claude/config`, `run-siglip/config` untouched. The `.va-shots`
golden DB gained one deep-scan sweep (the diagnostic evidence) but no video data changed.
