"""YouTube video source.

source_key = the 11-char video_id, so the same video via any URL form
(watch?v=, youtu.be/, shorts/, with extra params) dedupes to one catalog row.
Download is capped to <=480p — frame embeddings don't need full resolution, and
it keeps fetches fast/small.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from va.contracts.video import ResolvedVideo, SourceType, VideoMetadata

_ID = r"[0-9A-Za-z_-]{11}"
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}


def _ensure_ffmpeg_dir(cache: Path) -> str:
    """Return a dir containing an `ffmpeg` symlink to imageio-ffmpeg's binary,
    so yt-dlp (which looks for a file literally named `ffmpeg`) can merge."""
    import imageio_ffmpeg

    exe = imageio_ffmpeg.get_ffmpeg_exe()
    bindir = cache / ".bin"
    bindir.mkdir(parents=True, exist_ok=True)
    link = bindir / "ffmpeg"
    if not link.exists():
        link.symlink_to(exe)
    return str(bindir)


def is_youtube_url(uri: str) -> bool:
    try:
        host = (urlparse(uri).hostname or "").lower()
    except Exception:
        return False
    return host in _HOSTS


def extract_video_id(uri: str) -> str:
    """Return the 11-char video id, or raise ValueError."""
    u = urlparse(uri)
    host = (u.hostname or "").lower()
    if host in ("youtu.be", "www.youtu.be"):
        cand = u.path.lstrip("/").split("/")[0]
    elif u.path.startswith(("/shorts/", "/embed/", "/v/")):
        cand = u.path.split("/")[2]
    else:
        cand = (parse_qs(u.query).get("v") or [""])[0]
    if re.fullmatch(_ID, cand):
        return cand
    raise ValueError(f"could not extract YouTube video id from {uri!r}")


class YoutubeSource:
    def resolve(self, uri: str) -> ResolvedVideo:
        vid = extract_video_id(uri)
        return ResolvedVideo(
            source_type=SourceType.youtube,
            source_uri=f"https://www.youtube.com/watch?v={vid}",
            source_key=vid,
            local_path=None,
        )

    def fetch(self, resolved: ResolvedVideo, cache_dir) -> ResolvedVideo:
        import yt_dlp  # deferred: only needed when actually downloading

        cache = Path(cache_dir)
        cache.mkdir(parents=True, exist_ok=True)
        vid = resolved.source_key
        outtmpl = str(cache / f"{vid}.%(ext)s")
        opts = {
            "format": "bestvideo[height<=480]+bestaudio/best[height<=480]/best",
            "outtmpl": outtmpl,
            "merge_output_format": "mp4",
            # yt-dlp needs ffmpeg on PATH to merge video+audio; point it at the
            # binary bundled with imageio-ffmpeg (named oddly, so symlink it).
            "ffmpeg_location": _ensure_ffmpeg_dir(cache),
            "quiet": True,
            "noprogress": True,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(resolved.source_uri, download=True)
            local_path = ydl.prepare_filename(info)
        # prepare_filename may report the pre-merge extension; prefer the mp4.
        mp4 = cache / f"{vid}.mp4"
        if mp4.exists():
            local_path = str(mp4)

        meta = VideoMetadata(
            title=info.get("title"),
            duration_seconds=info.get("duration"),
            fps=info.get("fps"),
            resolution=(f"{info.get('width')}x{info.get('height')}"
                        if info.get("width") and info.get("height") else None),
            has_audio=info.get("acodec") not in (None, "none"),
        )
        return resolved.model_copy(update={"local_path": str(local_path), "metadata": meta})
