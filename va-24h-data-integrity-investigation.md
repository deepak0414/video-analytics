# `.va-24h` data-integrity forensic investigation

Investigator: agent session, 2026-08-19. Read-only against
`/home/debug/video-analytics/.va-24h` and the repo git history. No workdir file, no
`src/` file, and no git state was modified. No live NVR access was attempted; no host
or credential appears in this document.

Framing (repo convention): **determinism is not correctness.** The pull path that
produced this workdir is reproducible — repeat pulls were measured byte-identical.
This document is about whether what it reproducibly produced is **true**. It is not,
in a bounded and now fully-quantified way.

**Bottom line:** the two reported problems are **one defect with two symptoms**. The
recorder's `loadfile` time-seek prepends a fragment of *stale ring-buffer content* to
every pull. That fragment is dated **≈7 days earlier** (the timestamp symptom) and
belongs to **whichever camera occupied that region of the disk** (the channel symptom).
`camera_id` and `start_epoch` are stamped from the *request*, never from the *delivery*,
so both labels are asserted rather than verified.

---

## 0. Inventory and method

`catalog.db`, 264 `videos` rows, all `source_type = nvr_recorded`, all `profile = security`.

| metric | value |
|---|---|
| videos total | 264 |
| `ingest_status = done` | 238 |
| `ingest_status = failed` | 26 |
| `start_epoch` span | 2026-08-10 14:18:53 → 2026-08-11 14:15:45 (America/Los_Angeles) — 24 h |
| `created_at` span | 2026-08-11T21:18:18Z → 2026-08-12T07:35:39Z |
| `fetched_at` span | 2026-08-11T21:19:36Z → **2026-08-13T06:34:34Z** |
| resolution | 2688x1520 @ 20 fps (234), **352x240 @ 10 fps (4)** |

| camera_id | done | failed |
|---|---|---|
| nvr-ch1 | 102 | 11 |
| nvr-ch2 | 109 | 8 |
| nvr-ch3 | 2 | 1 |
| nvr-ch4 | 11 | 3 |
| nvr-ch5 | 3 | 0 |
| nvr-ch6 | 11 | 3 |

Role tables: `segments` 238, `object_tracks` 526, `object_detections` 9187,
`action_events` 81, `ocr_results` 10248, `transcripts` 0 (security profile skips Roles
8/9), `role_provenance` 1904. `<workdir>/nvr_refs/` still holds the PR-#38 dHash
reference library (ch1 4 modes, ch2 3, ch3 1, ch4 2, ch5 1, ch6 3).

### Two independent ground truths were available — this is a census, not a sample

1. **The burned-in Lorex clock is already in the database.** The `security` profile ran
   Role 10 (RapidOCR) during ingest, and the overlay (`MM-DD-YYYY hh:mm:ss AM/PM`, bbox
   ≈ x 0.71 / y 0.04) was OCR'd at **1 fps for all 238 done clips** → `ocr_results`,
   10248 rows. Parsing them yields **8337 second-level readings of the true recording
   clock** across 234 clips (4 clips yielded no parsable overlay). Every skew number
   below is `burned-in clock − (start_epoch + t)`, i.e. delivered minus requested.
2. **Frames**, decoded with the repo's own `va.media.frames` (bundled imageio-ffmpeg).
   Every one of the 238 done clips was rendered into per-channel contact sheets and read
   visually; every clip carrying a foreign clock reading was rendered again as a
   *foreign-frame / clean-frame* pair.

So the extent figures are over **100 % of the ingested workdir**, not a sample.

---

## 1. Problem 2 — timestamp skew: quantified

### Shape: NOT constant. Bimodal — 94 % exact, 6 % exactly-one-ring-cycle stale.

Classification of all 8337 parsed clock readings:

| class | rule | count | share | what it is |
|---|---|---|---|---|
| aligned | \|Δ\| ≤ 5 s | 7865 | **94.3 %** | correct |
| drift | 5 s < \|Δ\| < 12 h | 213 | 2.6 % | timeline shift on **6 clips only** (max 56 s) — the documented trim-induced drift, *"stored timestamps run early by that much"* |
| **foreign** | \|Δ\| ≥ 12 h | **259** | **3.1 %** | footage from another week |

There is **no constant offset and no timezone error**. `start_epoch` is derived correctly:
`nvr.py::resolve()` stores `float(int(start.timestamp()))` from the parsed URI, and 94.3 %
of frames confirm it to within the documented ~1 s `loadfile` alignment. Hypothesis B3
(tz/epoch conversion bug) is **ruled out** — a conversion bug would move *every* frame by
the same amount.

The foreign readings cluster tightly and **drift monotonically across the backfill**:

| requested time (3 h buckets) | n | median offset | vs exactly −7 d |
|---|---|---|---|
| 08-10 14:00 | 50 | −6.9876 d | +17.9 min |
| 08-10 17:00 | 80 | −6.9820 d | +25.9 min |
| 08-10 20:00 | 23 | −6.9778 d | +32.0 min |
| 08-10 23:00 | 15 | −6.9784 d | +31.1 min |
| 08-11 02:00 | 10 | −6.9782 d | +31.4 min |
| 08-11 05:00 | 6 | −6.9785 d | +31.0 min |
| 08-11 08:00 | 60 | −6.9770 d | +33.1 min |
| 08-11 11:00 | 13 | −6.9702 d | +42.9 min |
| 08-11 14:00 | 1 | −6.9690 d | +44.7 min |

Overall (excluding the four wholly-foreign clips): n=116, median −6.9783 d, offset from
exactly −7 d ranging **+17.9 → +44.7 minutes**, i.e. the "7 days" is **never exactly 7
days** and shrinks by ≈27 min per day of requested time. That is the signature of a
**physical ring buffer whose wall-clock capacity varies with bitrate** — not of any
clock, calendar, or arithmetic error. One outlier (`98f6aba8`, ch4) sits at −9.978 d
(burned-in 2026-08-01), consistent with a deeper stale block.

### Extent per clip

| category | videos | share of 238 |
|---|---|---|
| clean — every frame's clock matches the request | 142 | 59.7 % |
| **≥ 1 second of foreign-clock footage** | **96** | **40.3 %** |
| — foreign confined to a short HEAD lead-in | 91 | 38.2 % |
| — **wholly foreign** (no aligned frame at all) | **4** | 1.7 % |
| — mixed / not head-confined | 1 | 0.4 % |

Foreign head lengths measured at 1 fps: 1 s ×77, 2 s ×7, 3 s ×9. Frame-exact probing
(below) shows the true head is **0.05–0.3 s** — one to a few video frames — but because
Role 2/Role 10 sampling always emits `idx 0` (`media/frames.py::sample_frames`: *"Always
yields at least the first frame"*), that fragment lands as **one full indexed sample per
affected clip**.

Per camera:

| camera_id | videos | ≥1 foreign second | wholly foreign |
|---|---|---|---|
| nvr-ch1 | 102 | 36 | 1 |
| nvr-ch2 | 109 | 46 | 3 |
| nvr-ch3 | 2 | 0 | 0 |
| nvr-ch4 | 11 | 6 | 0 |
| nvr-ch5 | 3 | 1 | 0 |
| nvr-ch6 | 11 | 7 | 0 |

### Worked example (the spike's exhibit)

`484f2ad9`, ch1, `source_uri = nvr://1/2026-08-11T04:03:21+00:00/2026-08-11T04:04:34+00:00`,
`start_epoch = 1786421001` (2026-08-10 21:03:21 local). Its own `ocr_results`:

```
t=0.0   "08-03-2026 09:35:39 PM"   <- foreign: 7 d earlier, 32 min later in the day
t=1.0   "08-10-2026 09:03:23 PM"   <- aligned
t=2.0   "08-10-2026 09:03:24 PM"
```

Decoding both frames confirms it visually: `t=0.0` is a dark side-yard wall (a different
camera); `t=0.05` onward is ch1's driveway at 9:03 PM on 08-10.

### The four wholly-foreign clips — delivered vs requested

| camera_id | requested (local) | window | burned-in clock | Δ | duration | resolution | what it actually shows | video_id |
|---|---|---|---|---|---|---|---|---|
| nvr-ch1 | 2026-08-11 09:29:25 | 86 s | **2026-08-04 10:02:34** | −6.978 d | 86.0 s | 352x240 | a **back yard** (lawn, fence, patio) — not ch1's driveway | `ed09f4ba` |
| nvr-ch2 | 2026-08-10 15:57:56 | 38 s | **2026-08-03 16:15:49** | −6.988 d | 38.0 s | 352x240 | the **upper back lawn** (retaining wall, red maple) = ch6's view | `6555a8e4` |
| nvr-ch2 | 2026-08-10 18:29:14 | 36 s | **2026-08-03 18:55:09** | −6.982 d | 91.3 s | 352x240 | a front-porch view (cannot rule out ch2's own camera) | `b853dba0` |
| nvr-ch2 | 2026-08-11 08:06:21 | 37 s | **2026-08-04 08:38:30** | −6.978 d | 37.0 s | 352x240 | a **side yard** (fence + siding) — not ch2's porch | `7ed27957` |

Note the perfect 1:1 correlation with the **only four 352x240 @ 10 fps clips in the
workdir**: the device served its low-rate *sub-stream* recording, whose ring holds older
data, instead of the requested main-stream window.

---

## 2. Problem 1 — channel contamination: quantified

`videos.camera_id` is written by `src/va/sources/nvr.py::resolve()`
(`id=f"nvr-ch{chan}"`) purely from the request URI. Nothing downstream ever checks that
the delivered pixels belong to that camera.

### Is each channel's *dominant* view stable? Yes.

All 238 clips were reviewed as per-channel contact sheets ordered by `start_epoch`:

- **nvr-ch1** — 102 clips: a single stable view (driveway / street seen from under a
  porch eave) across the full 24 h, day, dusk and IR night.
- **nvr-ch2** — 109 clips: a single stable view (front porch pillar / lawn / street).
- **nvr-ch4** — dark side yard; **nvr-ch5** — narrow side passage; **nvr-ch6** — back
  patio; **nvr-ch3** — dark passage. All stable.

So there is **no channel remap** in this dataset: every channel's *body* footage is one
fixed physical camera for all 24 h. **Hypothesis B (the NVR move / reconfiguration
remapped channel numbers) is NOT supported for `.va-24h`** — the workdir predates the
move, and no channel changes view mid-workdir. (It remains a live risk for any *future*
pull; see §5.)

### The contamination is at the head of the clip, and it is a true cross-camera swap

Frame-exact measurement: for each of the 92 clips having both foreign and clean frames,
dHash (8×8, 64 bit) of the frame at the foreign timestamp vs a clean frame from the
*same clip*:

| pairing | median | p90 | max |
|---|---|---|---|
| clean vs clean (same clip, 5 s apart) | **0** | 2 | 11 |
| **foreign vs clean (same clip)** | **29** | 39 | 44 |

Calibrated against the bands measured on this install from clean clips only, comparing
frames at similar hour (lighting matched):

| pairing | n | p10 | median | p90 | ≤18 |
|---|---|---|---|---|---|
| same camera, similar hour | 992 | 1 | 8 | 20 | 88 % |
| **cross camera, similar hour** | 58 | 24 | 35 | 38 | **0 %** |

Applying those bands to the 92 head lead-ins:

| verdict | criterion | clips | share |
|---|---|---|---|
| **different physical camera** | d ≥ 24 | **68** | **74 %** |
| ambiguous | 19 ≤ d ≤ 23 | 3 | 3 % |
| same view, different day | d ≤ 18 | 21 | 23 % |

Adding the wholly-foreign clips (3 of 4 unambiguously another camera):

> **71 of 238 ingested clips (29.8 %) contain footage from a physical camera other than
> the one their `camera_id` names.** A further 25 contain footage from the right camera
> but the wrong week.

An independent nearest-neighbour classifier (each foreign frame matched against clean
reference frames from all channels at similar hour) put **151 of 259 foreign frames
(58 %) on another channel**, with **ch6 (back patio) the single most common intruder**
(94 of 259 = 36 %). That classifier is noisier (p90 best-match distance 25), so the
frame-exact figure above is the one to quote; the two agree in direction and in naming
ch6 as the dominant contaminant.

### Downstream blast radius (already-computed role rows)

| table | rows inside a contaminated head | total | share |
|---|---|---|---|
| `segments` (start inside) | 96 | 238 | 40.3 % |
| `object_tracks` (`first_seen` inside) | 55 | 526 | 10.5 % |
| `object_detections` | 242 | 9187 | 2.6 % |
| `ocr_results` | 435 | 10248 | 4.2 % |
| `action_events` (start inside) | 29 | 81 | 35.8 % |
| `vectors.npz` frame embeddings | ≥ 96 (one per affected clip; `sample_frames` always emits idx 0) | — | — |

Plus, for the 4 wholly-foreign clips, **100 %** of their rows are mislabelled:
`ed09f4ba` 28 detections / 2 tracks / 53 OCR lines; `b853dba0` 140 / 2 / 95;
`7ed27957` 28 / 1 / 38; `6555a8e4` 0 / 0 / 39.

For the ReID work that surfaced this: a cross-camera appearance embedding sitting in the
wrong camera's track set is exactly the input that manufactures phantom re-identifications.

---

## 3. Root cause

### 3.1 The mechanism (both symptoms, one cause)

`src/va/sources/nvr.py::_fetch_window` issues one
`loadfile.cgi?action=startLoad&channel=N&startTime=…&endTime=…` per window. On this 2017
firmware the time-seek does not begin cleanly: the stream's first fragment is content
already resident at the resolved disk position. The evidence says that content is **one
ring-cycle stale**:

- its clock is `requested − (7 days − 18…45 min)`, and the deficit **grows monotonically
  with request time** (§1) — the signature of a byte-sized circular buffer, not of a clock;
- it belongs to **an arbitrary channel** (§2) — the recorder interleaves all channels on
  one disk, so the physically-preceding region is whatever camera was writing there.

The repo's own docs mis-describe this. `nvr.py`'s module docstring calls it *"~1-2 s from
a previous load session's buffer"*. If it were a previous *session's* buffer, the dates
would be 08-10/08-11 — the backfill's own working set. They are 08-03/08-04. It is stale
**disk** content, which is why it is cross-camera by nature.

`resolve()` then stamps `camera_id = f"nvr-ch{chan}"` and `start_epoch = float(int(start.timestamp()))`
from the request. **Nothing verifies delivery against request.** That is the root cause of
both symptoms.

### 3.2 Why the PR-#38 safeguard did not stop it — and Hypothesis A is only 8/238 true

**The git timeline (all times UTC):**

| commit | authored | committed | merged | effect |
|---|---|---|---|---|
| `f9aeb93` | 2026-08-11 20:04 | 2026-08-11 21:05 | `2fab182` 2026-08-11 21:14 | PR #38 — consensus dHash + `nvr_refs/` **added** |
| `fd69fc5` | 2026-08-12 04:29 | 2026-08-12 05:26 | `834b48b` 2026-08-12 05:52 | read `.dav` directly |
| `ce2d588` | **2026-08-13 05:22** | 2026-08-13 06:08 | **`19606a3` 2026-08-13 06:13** | PR #40 — **dHash/ReferenceLibrary DELETED** |

`.va-24h`'s `fetched_at` values straddle that last merge:

| fetch era | videos | ≥1 foreign second | cross-channel | 352x240 substream |
|---|---|---|---|---|
| **A — fingerprinting ACTIVE** (`fetched_at` < 2026-08-13T06:13Z) | 230 | 90 (39 %) | 56 | 1 |
| **B — fingerprinting REMOVED** (`fetched_at` ≥ 06:13Z) | 8 | **6 (75 %)** | 6 | **3** |

So:

> **Hypothesis A is falsified for the bulk of the workdir.** 230 of 238 clips were pulled
> with the dHash safeguard *active*, and 90 of them are contaminated anyway. The safeguard
> was present and did not work.
>
> **Hypothesis A is correct for the tail.** The 8 clips fetched 2026-08-13T06:21–06:34Z —
> 8–21 minutes after PR #40 merged — are the workdir's worst rows: 6/8 contaminated, and
> **3 of the 4 wholly-foreign substream clips are in this batch**
> (`ed09f4ba` 06:32:12Z, `6555a8e4` 06:33:29Z, `7ed27957` 06:34:34Z).
> These 8 windows all appear in `.va-24h/backfill_failures.json`: they had been **refused**
> by the safeguard during the backfill and were re-driven once the refusal was gone.

**Why the active safeguard missed it — the specific defect.** `_pull_window` (at `fd69fc5`)
ran two checks:

1. *Whole-clip identity* against `ReferenceLibrary` — **this worked.** Comparing all 14
   stored `nvr_refs` scene modes against real clean frames from every channel, **every
   mode's nearest match is its own channel** (distances 0–16). The library was never
   poisoned; it correctly answered "this clip is ch1".
2. *Lead-in trim* via `self_distances` / `longest_clean_run` — **this was structurally
   blind.** `_frame_hashes` samples with
   `ffmpeg -vf fps={FPS_SAMPLE},scale=400:-1` (FPS_SAMPLE = 4). Measured on the stored
   clips, that filter's **first output frame is not the file's first frame**: its dHash
   distance from `t=0.0` is 28–39 (cross-camera band) while its distance from `t=0.05` is
   0–3. The contaminating fragment is 1–5 video frames long — shorter than one sample
   interval — so the frames the verifier hashed never included it, and
   `longest_clean_run` reported a clean run starting at index 0.

The trim did fire: **209 of 238 clips are shorter than their requested window** (9 equal,
20 longer; −0.5 to −3.8 s typical, up to −100.8 s). It removed the *long* contamination
and left the *sub-sample* head — which is precisely the frame `sample_frames` then hands to Role 2.

That is the textbook shape of "determinism is not correctness": a deterministic,
well-reasoned, measurement-backed check that reproducibly passed footage it could not see.

### 3.3 The current (post-removal) design does not fix it either

`ce2d588` replaced verification with a **duration sanity check** (`_probe_cut`: the cut
must decode and land within `DURATION_TOL_S = 2.0` s of the window). That gate cannot
detect an identity error, and the workdir contains the proof — clips it produced:

| clip | requested window | delivered duration | passed duration gate? | actually |
|---|---|---|---|---|
| `ed09f4ba` | 86 s | 86.0 s | yes | wrong camera, wrong week, sub-stream |
| `6555a8e4` | 38 s | 38.0 s | yes | wrong camera, wrong week, sub-stream |
| `7ed27957` | 37 s | 37.0 s | yes | wrong camera, wrong week, sub-stream |

And the padded-fetch + PTS-cut design does **not** eliminate the head lead-in: of the 5
main-stream clips fetched under it, **3 still carry a cross-camera head**
(`8b2a979c` d=29, `887d84b5` d=30, `2b4c76ea` d=33). The CLAUDE.md claim that *"the fixed
pre-pad absorbs the §5d seek lead-in and the cut discards it, so lighting never matters"*
is contradicted by the data: the contaminated fragment is present at t=0 of the **cut**,
not only of the padded fetch.

### 3.4 Alternatives ruled out

| hypothesis | verdict | evidence |
|---|---|---|
| B3 — tz / epoch conversion bug (constant offset) | **ruled out** | 94.3 % of 8337 readings align within 5 s, and the 2.6 % drift class is 6 clips at <60 s; the offending offset is never a round number and drifts +27 min/day |
| B1 — the NVR clock itself was wrong/reset | **ruled out** | the overlay and the request agree on 94.3 % of frames from the same device; a wrong device clock would move all of them together |
| B — channel numbers remapped by the NVR move | **not supported for this workdir** | each channel's body footage is one stable view across all 238 clips / 24 h; the move postdates these pulls |
| A — removal of fingerprinting caused the contamination | **true for 8/238 clips only** | 230 clips were fetched with the safeguard active and 90 are contaminated |
| library poisoning (a wrong-camera scene admitted as a "lighting mode") | **ruled out** | all 14 `nvr_refs` modes match their own channel (distance 0–16) |
| ring-edge / off-ring footage (the documented exact-window fallback case) | **not the cause** | the requested footage was 1–2 days old, nowhere near the ~6-day edge; the stale content is *older* than the request, not missing |
| B2 — ring wrap returned nearest-available older footage | **this is the cause, refined** | offset = ring capacity in bytes → 7 d − (18…45 min), drifting with bitrate; content is cross-channel because the ring interleaves channels |

---

## 4. Is `.va-24h` salvageable?

**Partially — and the cheap repair is worth doing, but it does not produce a clean
dataset.**

What is repairable **without touching the NVR**, because the burned-in clock is already
in `ocr_results`:

1. **Quarantine the 4 wholly-foreign clips.** They are 100 % wrong on both axes and are
   trivially identified (`resolution = '352x240'` — a perfect discriminator here, and
   `ok = 0` in the clock analysis). Delete them with `va remove`.
2. **Truncate the contaminated head of the other 92.** The foreign fragment is 0.05–0.3 s.
   Dropping all role rows and frame embeddings with `t < 1.0 s` on those 92 clips removes
   the contamination at a cost of ~1 second per clip: 55 tracks, 242 detections, 435 OCR
   rows, 29 action events, 96 frame embeddings, and 96 segment start bounds.
   `t = 0` rows are *the* contaminated rows — no finer surgery is needed.
3. **Re-derive `start_epoch` from the burned-in clock** for the survivors. Median aligned
   |Δ| is under 2 s, so this is a confirmation rather than a correction — but doing it
   converts an asserted label into a measured one, and it is the only way to keep the
   windowed `va aggregate` tier honest.
4. **Do not attempt to re-key `camera_id` from scene fingerprints.** It would be busywork:
   every clip's *body* is already the right camera. Only the heads are wrong, and step 2
   deletes them.

After that, 234 clips with their first second removed are usable for per-camera counting
and for ReID. What you would **not** have is a defensible 24-hour dataset: 96 clips have a
one-second hole, the four removed windows leave gaps, and 26 windows never ingested at all.

**Recommendation: repair `.va-24h` in place for continued development, and treat a fresh
pull as required before any result is published or used to validate ReID.** The repair is
cheap and makes the existing data safe to work with; it cannot manufacture the seconds
that were never delivered. A fresh pull of *this* footage is in any case impossible — the
2026-08-10/11 window is long past the ~6-day ring and the recorder has moved.

---

## 5. Safeguards that must be restored before any re-pull

The dropped fingerprinting is **not** the right thing to restore verbatim — this
investigation shows it was blind to the failure that actually occurred. Restore the
*function* it was meant to serve, with the specific fixes the data demands.

**Mandatory, in priority order:**

1. **Verify delivery against request, per frame, using the recorder's own burned-in clock.**
   This is the single highest-value control and it is nearly free: the OCR is already
   running, the overlay is legible, and the check is `|burned_in − (start_epoch + t)| ≤ tol`.
   It catches *both* symptoms at once — a wrong-week frame and a wrong-camera frame are
   both wrong-clock frames, because the stale content carries its own clock. It is
   lighting-independent, which is exactly what defeated PR #38. Fail the pull, or trim the
   offending frames, rather than ingest them.
   *This must be a gate on the pull, not a post-hoc audit — the OCR that revealed this
   contamination ran and was simply never consulted.*
2. **Fix the sampler blindness.** Any frame-level verification must inspect the **actual
   first frames** of the clip. `ffmpeg -vf fps=N` demonstrably skips them (measured:
   distance 28–39 from the true `t=0`). Sample by frame index (`sample_frames`, which
   yields idx 0) or add an explicit `t=0` probe. A verifier that cannot see the frame the
   indexer will embed is decoration.
3. **Pin the stream identity.** Assert the delivered resolution/frame-rate matches the
   channel's main stream. A one-line check would have refused all four wholly-foreign
   clips: they are the workdir's only 352x240 @ 10 fps files.
4. **Keep the duration gate, and stop treating it as sufficient.** It passed three clips
   that were the wrong camera on the wrong day at exactly the right length.
5. **Restore whole-clip camera identity** (the `ReferenceLibrary` idea). It was the part
   that *worked*: all 14 persisted modes correctly matched their own channel. Keep the
   per-channel keying and the "admit a new lighting mode only against a live snapshot"
   rule; the measured cross-camera band on this install (median 35, p10 24, 0 % ≤ 18)
   cleanly separates cameras when the comparison is lighting-matched.
6. **Record a recorder identity in `source_key` / `camera_id`.** CLAUDE.md already flags
   the single-recorder assumption as a known gap; the NVR has since moved, so the next
   pull against a different unit will silently dedup and link against these rows.

**Also worth doing:** write the *delivered* time (from the overlay) into the catalog
alongside the *requested* `start_epoch`, so the discrepancy is visible in SQL forever
instead of requiring a forensic pass to discover.

---

## 6. Open items — what I could not settle, and what would settle it

1. **Which working tree was checked out at each `fetch`.** Git proves when commits were
   authored/merged, not what was running. `b853dba0` (the fourth substream clip) was
   fetched 2026-08-12T18:58Z — before PR #40 merged, but during that branch's development
   — so it may have been pulled by uncommitted post-removal code.
   *Settled by:* the shell history / driver script for the backfill and the 2026-08-12
   and 2026-08-13 re-runs, or an ingest trace from those runs. `.va-24h/traces/` holds
   only `reprocess-20260814-052425-6b9a.trace` (the Role 5/6 rebuild), which re-ran
   detection **on the same contaminated media**, so the contamination propagated into the
   current `object_detections` regardless.
2. **Whether `b853dba0` shows ch2's own camera or a different one.** Its view is the front
   porch at a framing that differs from ch2's stable view, possibly only because it is a
   352x240 sub-stream with different cropping. Its clock is unambiguously 7 days wrong
   either way. *Settled by:* a labelled camera inventory, or a live snapshot of each
   channel.
3. **The exact firmware behaviour** (whether the stale fragment is a read-ahead buffer, an
   index-resolution error at the wrap, or a sub-stream fallback). The data pins the
   *properties* — one ring cycle stale, cross-channel, 1–5 frames, plus rare whole-window
   sub-stream substitution — but not the internal cause. *Settled by:* instrumented live
   pulls against the recorder, capturing the raw `.dav` head before any cut. **Not
   attempted here** (no live pulls; footage off-ring; recorder moved).
4. **Whether the current channel→camera mapping still matches this workdir's.** The NVR was
   moved/reconfigured after these pulls. *Settled by:* one `snapshot.cgi` per channel on the
   relocated recorder, compared against the `nvr_refs` modes in this workdir.
   **Recommended for the human** — it is a five-minute check and it gates whether any
   future pull can be joined to this data at all.
5. **One −9.978 d outlier** (`98f6aba8`, ch4, burned-in 2026-08-01) sits two ring cycles
   back rather than one. Single instance; not enough data to model.

---

## Repair as executed (2026-08-19)

§4 above *recommends* a repair; this section records what was actually run on `.va-24h` (data-only,
in place; catalog.db + vector-shard backups in the session scratchpad, machine log in the untracked
`.va-24h/repair_log.json`). Motivating count **77 → 72 (ch2 51 / ch1 21)**.

- **4 wholly-foreign sub-stream clips quarantined** (`resolution='352x240'`): `ed09f4ba` (ch1),
  `6555a8e4`, `b853dba0`, `7ed27957` (ch2) — marked reversibly via `ingest_status='quarantined'` and
  their role rows/shards cleared (196 detections, 5 tracks, 225 OCR, 4 segments, 253 embeddings).
  **Known follow-up:** `'quarantined'` is outside `contracts.IngestStatus`, so `Catalog.list()` (web
  `/api/videos`, `va migrate-layout`) raises on this workdir until the enum gains the member (or the
  rows are re-marked `failed` + `ingest_error`); the golden ask path does not call `list()`, so the
  fixture still runs.
- **Contaminated-head rows dropped on 92 clips** (`t<1.0 s`, plus definitively-foreign `t≥1.0` frames
  on 16 clips per the burned-in clock): object_detections −77, ocr_results −217, frame embeddings
  −117; object_tracks **39 dropped** (all-foreign) + **13 adjusted** (first/last/frame_count
  recomputed from surviving body detections). Segments and action_events KEPT (each spans the whole
  clip; not head-only).
- **`start_epoch` re-derived from body-frame OCR on 5 time-skewed clips** (+6..+19 s): `95f56ac8` +7,
  `57c9a742` +6, `b641c6ca` +19, `ca9b7c9b` +7, `ec70e92a` +7; the other 229 aligned clips unchanged.
  No clips left unresolved.
- Table totals: tracks 526→482, detections 9187→8914, OCR 10248→9806, segments 238→234, actions
  81→81. Integrity re-checked: 0 orphans, 0 frame_count mismatches, 0 zero-frame tracks.
- **Judgment call:** the head-drop extended past strict `t<1.0` to the 16 clips' foreign `t≥1.0`
  frames (strict `t<1.0` alone would leave **74**, with 2 all-foreign phantom tracks); 72 is the
  more-correct result, reversible from the backups.

The golden fixture `tests/golden_queries/nvr24h_aggregate.yaml` pins this **72** (raw upper bound —
plumbing, not footage truth).
