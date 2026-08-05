# Agent review — approve

date: 2026-08-05T10:53:52.164545
range: origin/main..HEAD
branch: loop/ws4c-nvr-chunk-source
findings: 1

- **minor** `src/va/sources/nvr.py:199` — _curl embeds the password verbatim in a curl config `user = "u:p"` line, where an embedded double-quote or backslash is interpreted by curl's config parser and silently mangles the credential.
  - scenario: VA_NVR_PASS containing `"` or `\` works for the in-process lnr adapter (base64 Basic) but breaks every curl transfer in the pull path: digest auth fails, and the ingest dies with the unrelated-looking 'could not fetch a live reference frame' / 'every chunk was unverifiable' error; safe path is escaping backslash and quote per curl config quoting rules (or a 0600 netrc in the pull-private tempdir via --netrc-file).

---

## Full review

Review complete. This range (WS4.b motion-episode scene detection + WS4.c NVR chunk source) is the third review round on this branch; I verified the two prior rounds' findings are genuinely fixed in HEAD and then hunted for new issues. Two pytest runs are live (PIDs 132888, 4115671), so per the repo's pile-up lesson I did not launch another suite run and relied on static verification.

## What I verified as clean

- **Prior-round fixes are real.** Credentials now travel via `--config -` on curl's stdin (`_curl_argv`, with a test asserting nothing user:pass-shaped on argv); `_preattach_chunk_metadata` sets `start_epoch` first and catches/warns on a dangling camera instead of crashing post-purge (with a regression test); dirty frames get a sentinel Hamming; pulls run in a private tempdir with atomic `os.replace`; NVR reingest reuses preserved cache media (tested against a dead-device stub); `query_margin_s` is now in CLAUDE.md's knob list; the lighting-match constraint and timeline-drift caveat are documented in CLAUDE.md and COORDINATION.md.
- **Plan conformance / ground truth.** The ingest oracle asserts the literal segment `[(1.0, 4.0)]` from a known motion event through the real security profile; the margin knob has both a forwarding test (`T0-60 … T0+120`) and the live-repro clamp test; `longest_clean_run` / `chunk_bounds` edges are hand-traced; the security-profile scene-detector switch carries a measured 21/22-clips comparison in the profile comment. Determinism-vs-truth discipline is observed.
- **Contracts.** `SceneDetector.detect` gained an optional defaulted `context` (histogram/pyscenedetect accept-and-ignore; no lambda doubles of the detector exist in tests); `ResolvedVideo` grew defaulted optional fields with a `model_rebuild()` for the forward ref; both changes logged in COORDINATION.md. No schema change — `camera_id`/`start_epoch` columns pre-exist. `Catalog.set_camera` validation cannot fire spuriously in ingest because the camera row is `get_or_create`d immediately before.
- **Best-effort discipline.** MotionSource failure and missing epoch degrade to a warned full-span segment; an unconfigured sidecar warns instead of silently returning `[]`; a hard NVR fetch failure correctly fails the ingest (a source is not a best-effort role).
- **Combination coverage.** The motion-episodes selection is replicated across all four config dirs' `security.yaml`; epoch-less chunks under the profile are measured behaviorally identical to pyscenedetect on the 22 real clips; the device layer is stubbed offline and live-validated separately (stated in the test header). `--anyauth` is not a hygiene regression — the lnr adapter already sends Basic to this LAN device.
- **Docs / commit messages.** New surface (`nvr://` form, env reuse, caveats, profile default) is in CLAUDE.md within the change; both subjects are provisional `need_agent_review:`, exempt from the clarity rule.

## Finding (1 minor)

**minor — `src/va/sources/nvr.py:199` — the curl config line doesn't escape the password.** `_curl` composes `user = "{user}:{password}"` verbatim. In curl's config-file grammar, `"` terminates a quoted value and `\` starts an escape, so a password containing either character is silently mangled: every digest exchange 401s, and the pull dies with the misleading "could not fetch a live reference frame" / "every chunk was unverifiable" error — nothing points at the credential. The in-process lnr adapter (base64 header) handles the same password fine, so the same env vars work for `motion-probe` but not for the pull. Safe path: escape per curl's quoting rules (`\` → `\\`, `"` → `\"`) when composing the stdin config line (and reject embedded newlines), or write a 0600 netrc inside the pull-private tempdir and pass `--netrc-file`.

**Verdict: approve** — no critical or major findings.

```json
{"verdict": "approve", "findings": [
{"severity": "minor", "file": "src/va/sources/nvr.py", "line": 199, "issue": "_curl embeds the password verbatim in a curl config `user = \"u:p\"` line, where an embedded double-quote or backslash is interpreted by curl's config parser and silently mangles the credential.", "scenario": "VA_NVR_PASS containing `\"` or `\\` works for the in-process lnr adapter (base64 Basic) but breaks every curl transfer in the pull path: digest auth fails, and the ingest dies with the unrelated-looking 'could not fetch a live reference frame' / 'every chunk was unverifiable' error; safe path is escaping backslash and quote per curl config quoting rules (or a 0600 netrc in the pull-private tempdir via --netrc-file)."}
]}
```
