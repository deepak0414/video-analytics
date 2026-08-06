# Agent review — approve

date: 2026-08-05T12:05:04.051935
range: origin/main..HEAD
branch: loop/ws4d-appearance-store
findings: 1

- **minor** `src/va/pipeline/ingest.py:125` — _capture_appearance embeds every track crop in ONE embed_image call with no batching, unlike the frame path's 32-image batches, and siglip_inproc runs the whole list in a single forward pass.
  - scenario: A long A-EV video under run-siglip/config with the over-counting iou tracker yields hundreds of tracks; the single forward pass over hundreds of 384x384 crops spikes VRAM on the already-OOM-prone box (§8.1), the caught exception silently drops ALL appearance refs for that video with only a trace warn, and no stub-path test can surface it. Safe path: wrap the embed in the existing _batched(..., _BATCH) helper (open crops per batch, np.vstack the results), matching the frame path.

---

## Full review

## Review: `origin/main..HEAD` (WS4.b + WS4.c + WS4.d, round 2 at cd416be)

WS4.b (`f811d2b`) and WS4.c (`9f8fae0`) were approved at these exact SHAs in prior reviews, so I focused the deep pass on the WS4.d commit — specifically the fix delta since round 1's request_changes at `e6bd406` (which I re-verified rather than trusted).

**All three round-1 findings are properly resolved:**

1. **Major (crop-to-track join breaks under bytetrack) — fixed and verified.** The bytetrack adapter now threads `data={"det_idx": np.arange(len(dets))}` through supervision and returns the *original* detections via `model_copy` (`bytetrack_inproc.py:58-71`), eliminating the float32(×1000) round-trip + clamping that perturbed the geometry key. I verified against the installed supervision 0.28 source that this is sound: `update_with_detections` returns `detections[detections.tracker_id != -1]` — a filtered view of the input object — and `Detections.__getitem__` filters `data` row-aligned with `tracker_id` (`core.py:2309-2317`), so `det_idx` indexes back to the exact input detection. The empty-track path (`Detections.empty()`) never touches `data`. The new `tests/test_tracker_passthrough.py` pins the invariant for both trackers using float fractions that provably do not survive the old float32 round-trip — it genuinely fails on the old code (satisfying the repo's regression-test lesson) and will catch the planned supervision 0.30 migration re-breaking it. The false "tracker never moves a box" comments in `ingest.py` and COORDINATION.md were corrected to state the real invariant and its test.

2. **Minor (unbounded crop RAM) — fixed.** Crops now spill to a transient cache dir as ≤512px JPEGs (`ingest.py:520-530`), re-opened per track at capture time, and `rmtree`'d after Role 6. A leak survives only a hard-aborted ingest, into the by-design-transient `cache/` dir, and a retry overwrites then cleans — acceptable.

3. **Minor (NVR recorder identity) — resolved via the documented safe path.** The single-recorder assumption is now stated in CLAUDE.md's `nvr://` block ("repointing VA_NVR_HOST at another NVR dedups/links wrongly; multi-NVR identity is backlog").

**Independently re-verified from round 1:** schema v6 migration is correct (`add_column` mirrored in `CREATE TABLE`, `len(MIGRATIONS) == SCHEMA_VERSION` holds); `TrackStore` round-trips `appearance_ref` and is the only `object_tracks` reader; the query path can never see `appearance.npz` (sharded glob targets `*/vectors.npz`); appearance capture is best-effort (failure costs refs, never tracks, never ingest) and this plus the disabled-tracker purge are tested; WS4.d's Done-when ("refs resolvable in the new store; frame store untouched") is directly asserted by `test_tracks_carry_resolvable_appearance_refs`. Commit subjects are provisional `need_agent_review:` — exempt from the clarity rule.

**One new minor finding in the fix's neighborhood:**

- `_capture_appearance` embeds **all** track crops in a single `embedder.embed_image(crops)` call (`ingest.py:125`), while the frame path deliberately batches at 32 for the real backends. `siglip_inproc.embed_image` has no internal batching — it runs one forward pass over the whole list. On the real-model combination (run-siglip + long A-EV video), the iou tracker's documented over-counting can yield hundreds of tracks, so this one call processes hundreds of 384×384 images in a single forward pass — a VRAM spike the 32-image batch path was sized to avoid, on the box §8.1 already flags for OOM. The failure is caught (best-effort), but the cost is *all* appearance refs for exactly the long, many-object videos where Role-12 ReID matters, with only a trace warn. Safe path: reuse the existing `_batched(..., _BATCH)` helper around the embed call (open crops per batch, `np.vstack` the results). Stub-path tests can't surface this — it only varies on the real-embedder combination.

Checked and dismissed: raw (unclamped) detector boxes now flowing into `object_detections` under bytetrack (they're already-validated `Detection` instances; round 1 endorsed storing honest raw boxes); crop-dir leak on hard abort (transient cache); `agg` confidence/class now sourced from `orig` (row-identical to the old `tracked` arrays); JPEG lossiness (no accuracy contract on a schema-insurance embedding); bytetrack test flakiness (first-frame ByteTrack activation is deterministic at these confidences). No disputes in workflow-trust-plan.md touch these findings.

Verdict: **approve** — the single finding is minor.

```json
{"verdict": "approve", "findings": [{"severity": "minor", "file": "src/va/pipeline/ingest.py", "line": 125, "issue": "_capture_appearance embeds every track crop in ONE embed_image call with no batching, unlike the frame path's 32-image batches, and siglip_inproc runs the whole list in a single forward pass.", "scenario": "A long A-EV video under run-siglip/config with the over-counting iou tracker yields hundreds of tracks; the single forward pass over hundreds of 384x384 crops spikes VRAM on the already-OOM-prone box (§8.1), the caught exception silently drops ALL appearance refs for that video with only a trace warn, and no stub-path test can surface it. Safe path: wrap the embed in the existing _batched(..., _BATCH) helper (open crops per batch, np.vstack the results), matching the frame path."}]}
```
