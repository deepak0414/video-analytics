"""Real Role-9 backend: pyannote.audio speaker diarization.

Loads the pretrained pipeline once via the ModelManager and runs it over the
extracted mono WAV (pyannote wants audio; our media is video, so reuse
media.audio.extract_wav — the diarizer owns its audio handling like Role 8 does).

Requires the `diarize` extra AND a HuggingFace token (read scope) with the gated
`pyannote/speaker-diarization` model accepted on huggingface.co. Provide the token
via `huggingface-cli login` (cached), HF_TOKEN, or load.token / load.hf_token.
Select via config: speaker_diarizer.model = pyannote. Targets the pyannote.audio
4.x API (`>=4` in the extra — 3.x imports torchaudio.AudioMetaData, removed in the
torchaudio 2.11 this box pins): `Pipeline.from_pretrained(token=...)` +
`diarization.itertracks(yield_label=True)`.
"""
from __future__ import annotations

import os
from typing import Any, List

from va.contracts.diarization import SpeakerTurn
from va.media.audio import extract_wav, load_wav_mono
from va.runtime.device import resolve_device
from va.runtime.manager import MANAGER

_DEFAULT_MODEL = "pyannote/speaker-diarization-3.1"


class PyannoteDiarizer:
    def __init__(self, load: dict[str, Any] | None = None):
        load = load or {}
        self.model = load.get("model", _DEFAULT_MODEL)
        self.device = resolve_device(load.get("device"))
        self._token = self._resolve_token(load)
        # Optional default speaker-count hints from config (per-call args override).
        self._num_speakers = load.get("num_speakers")
        self._min_speakers = load.get("min_speakers")
        self._max_speakers = load.get("max_speakers")
        self._pipeline = MANAGER.get(f"pyannote::{self.model}", self._build)

    @staticmethod
    def _resolve_token(load: dict[str, Any]) -> str | None:
        """Explicit config/env token, else the cached `huggingface-cli login` token."""
        tok = (load.get("token") or load.get("hf_token")
               or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN"))
        if tok:
            return tok
        try:  # picks up `huggingface-cli login` (~/.cache/huggingface/token)
            from huggingface_hub import get_token

            return get_token()
        except Exception:
            return None

    def _build(self):
        from pyannote.audio import Pipeline  # deferred heavy import
        from threadpoolctl import threadpool_limits
        import torch

        # Pass an explicit token if we have one (kwarg renamed across versions —
        # try both); otherwise fall back to pyannote's ambient HF auth.
        attempts = ([{"use_auth_token": self._token}, {"token": self._token}]
                    if self._token else [{}])
        pipe = err = None
        for kw in attempts:
            try:
                # WORKAROUND (2026-06): scope BLAS to 1 thread around the model
                # load ONLY. from_pretrained's PLDA/VBx setup does small-matrix
                # np.linalg.inv + scipy eigh, and on this box that intermittently
                # deadlocked forever inside OpenBLAS (futex wait, caught via
                # faulthandler: pyannote/audio/utils/vbx.py vbx_setup).
                #
                # Root cause: thread-pool oversubscription exposing an OpenBLAS
                # lost-wakeup race. threadpoolctl.threadpool_info() showed FOUR
                # all-cores-sized pools in this process on a 20-core box (numpy's
                # OpenBLAS + scipy's OpenBLAS + torch's libgomp + a vendored
                # libgomp ≈ 80 threads) — heavy preemption stretches the pool's
                # sleep/wake handshake window until a wakeup is lost and the
                # caller sleeps forever. Known bug class: numpy#30092,
                # OpenBLAS#1844, pyannote-audio discussion#1802.
                #
                # Why this scope: limits=1 makes OpenBLAS run the (tiny, ~µs)
                # load-time math inline on the calling thread — no worker
                # handshake, no race — and is restored on block exit. Nothing
                # else (torch, GPU stages, the diarization inference itself) is
                # throttled. NOT a global OPENBLAS_NUM_THREADS=1: that would
                # serialize BLAS pipeline-wide.
                #
                # REVISIT / revert triggers: (a) an upstream OpenBLAS/numpy fix
                # for the lost-wakeup ships, (b) we build a process-wide thread
                # -budget primitive (see parallelization-analysis.md) that
                # coordinates pool sizes properly, or (c) the duplicate BLAS/
                # OpenMP runtimes get deduped from the env. If a hang ever shows
                # up in diarize() inference (same futex signature), widen this
                # same guard around self._pipeline(...) rather than going global.
                with threadpool_limits(limits=1, user_api="blas"):
                    pipe = Pipeline.from_pretrained(self.model, **kw)
            except TypeError as e:           # this version doesn't take that kwarg
                err = e
                continue
            except Exception as e:
                err = e
                break
            if pipe is not None:             # from_pretrained returns None on auth failure
                return pipe.to(torch.device(self.device))
        msg = (f"Could not load gated pyannote model '{self.model}'. Accept its terms on "
               "huggingface.co, then authenticate here: `huggingface-cli login` (or set "
               "HF_TOKEN). Or use the `sidecar` backend.")
        raise RuntimeError(f"{msg} ({err!r})" if err else msg)

    def _speaker_hints(self, num_speakers, min_speakers, max_speakers) -> dict:
        """Per-call hints override config defaults; num_speakers (exact) wins over bounds."""
        num = num_speakers if num_speakers is not None else self._num_speakers
        lo = min_speakers if min_speakers is not None else self._min_speakers
        hi = max_speakers if max_speakers is not None else self._max_speakers
        if num is not None:
            return {"num_speakers": int(num)}
        hints = {}
        if lo is not None:
            hints["min_speakers"] = int(lo)
        if hi is not None:
            hints["max_speakers"] = int(hi)
        return hints

    def diarize(self, media_path: str, *, num_speakers=None,
                min_speakers=None, max_speakers=None) -> List[SpeakerTurn]:
        import torch

        wav = extract_wav(media_path)
        if wav is None:  # silent / no audio track
            return []
        # Load the WAV ourselves (stdlib `wave` + numpy in media.audio) and hand
        # pyannote a waveform tensor, bypassing its torchcodec/FFmpeg audio backend:
        # this box has no system FFmpeg shared libs (libavutil.so), so torchcodec
        # fails to decode. shape (channel, samples), float32 in [-1, 1].
        audio, sr = load_wav_mono(wav)
        waveform = torch.from_numpy(audio).unsqueeze(0)
        hints = self._speaker_hints(num_speakers, min_speakers, max_speakers)
        result = self._pipeline({"waveform": waveform, "sample_rate": sr}, **hints)
        # pyannote 4.x returns a DiarizeOutput (the Annotation is .speaker_diarization);
        # 3.x returned the Annotation directly. Support both.
        annotation = result if hasattr(result, "itertracks") else result.speaker_diarization
        turns: List[SpeakerTurn] = []
        for segment, _, speaker in annotation.itertracks(yield_label=True):
            turns.append(SpeakerTurn(
                start_time=float(segment.start),
                end_time=float(segment.end),
                speaker=str(speaker),
            ))
        turns.sort(key=lambda t: t.start_time)
        return turns
