"""Real Role-8 backend: OpenAI Whisper (reuses the torch already used by SigLIP).

Extracts audio with the bundled ffmpeg, loads it as a numpy waveform (so Whisper
never needs ffmpeg on PATH), and transcribes with segment timestamps. Loaded once
via the ModelManager. Requires the `whisper` extra. Select via config:
speech_to_text.model = whisper.
"""
from __future__ import annotations

from typing import Any, List

from va.contracts.transcript import TranscriptLine
from va.media.audio import extract_wav, load_wav_mono
from va.runtime.device import resolve_device
from va.runtime.manager import MANAGER


class WhisperSTT:
    def __init__(self, load: dict[str, Any] | None = None):
        load = load or {}
        self.model_size = load.get("model", "large-v3")
        self.device = resolve_device(load.get("device"))
        self._model = MANAGER.get(f"whisper::{self.model_size}::{self.device}", self._build)

    def _build(self):
        import whisper  # deferred heavy import

        return whisper.load_model(self.model_size, device=self.device)

    def transcribe(self, media_path: str) -> List[TranscriptLine]:
        wav = extract_wav(media_path)
        if wav is None:
            return []  # no audio track
        audio, _sr = load_wav_mono(wav)
        result = self._model.transcribe(audio, fp16=(self.device != "cpu"))
        return [
            TranscriptLine(
                start_time=float(seg["start"]),
                end_time=float(seg["end"]),
                text=seg["text"].strip(),
            )
            for seg in result.get("segments", [])
            if seg.get("text", "").strip()
        ]
