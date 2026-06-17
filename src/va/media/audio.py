"""Audio extraction via the bundled ffmpeg (imageio-ffmpeg).

Used by speech backends that need a raw waveform (e.g. Whisper). Returns a
16 kHz mono PCM wav path, or None if the video has no decodable audio track.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional, Tuple

import numpy as np


def _ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def extract_wav(video_path: str, sample_rate: int = 16000) -> Optional[str]:
    """Extract mono PCM wav next to the video. Returns the path, or None if the
    source has no audio."""
    src = Path(video_path)
    out = src.with_suffix(".audio.wav")
    cmd = [
        _ffmpeg(), "-y", "-i", str(src),
        "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "wav", str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out.exists() or out.stat().st_size <= 44:
        # Non-zero exit, or only a wav header (no audio stream).
        return None
    return str(out)


def load_wav_mono(wav_path: str) -> Tuple[np.ndarray, int]:
    """Load a PCM wav into a float32 array in [-1, 1] using only the stdlib."""
    import wave

    with wave.open(wav_path, "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return audio, sr
