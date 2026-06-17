"""Local-file video source. source_key = sha256 of contents (so a moved or
renamed file is still recognized as already-ingested)."""
from __future__ import annotations

import hashlib
from pathlib import Path

from va.contracts.video import ResolvedVideo, SourceType


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class LocalSource:
    def resolve(self, uri: str) -> ResolvedVideo:
        path = Path(uri).resolve()
        if not path.exists():
            raise FileNotFoundError(uri)
        return ResolvedVideo(
            source_type=SourceType.local,
            source_uri=str(path),
            source_key=f"sha256:{_sha256(path)}",
            local_path=str(path),
        )

    def fetch(self, resolved: ResolvedVideo, cache_dir) -> ResolvedVideo:
        # Already local; just probe metadata.
        from va.media.frames import probe

        path = resolved.local_path or resolved.source_uri
        meta = probe(path)
        if meta.title is None:
            meta.title = Path(path).name
        return resolved.model_copy(update={"local_path": path, "metadata": meta})
