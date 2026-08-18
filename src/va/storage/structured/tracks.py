"""TrackStore — write/query the `object_tracks` table.

The distinct-count query lives here: COUNT of tracks per class = "how many
distinct X appeared" (each track is one followed object instance).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence
from uuid import UUID

from va.contracts.track import ObjectTrack
from va.storage.structured.schema import connect


@dataclass
class DistinctCount:
    video_id: UUID
    object_class: str
    distinct: int          # number of tracks = distinct object instances
    first_seen: float
    last_seen: float


@dataclass(frozen=True)
class WindowAnchoring:
    """What a windowed track query CAN see in this workdir (typed-query tier).

    `placed_videos` = DONE videos carrying a wall-clock anchor (start_epoch
    NOT NULL) — zero means NO track anywhere in the workdir is windowable.
    `unplaced_tracks` = tracks matching the queried classes (past the same
    min_frames filter) that sit on UN-anchored videos and are therefore
    invisible to any windowed count. Both exist so a count can DISCLOSE the
    exclusion instead of shipping a confident false 0 (plan §4)."""
    placed_videos: int
    unplaced_tracks: int


@dataclass(frozen=True)
class PlacedTrack:
    """A track joined onto its video's wall-clock placement (typed-query tier).

    Absolute times follow the repo's translation rule (`pipeline/timeline.py`):
    absolute = `videos.start_epoch` + the stored video-relative seconds.
    """
    track: ObjectTrack
    camera: Optional[str]          # videos.camera_id ("nvr-ch<n>"); None = uncameraed
    first_seen_epoch: float        # UTC epoch seconds
    last_seen_epoch: float


class TrackStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._conn = connect(self.path)

    def close(self) -> None:
        self._conn.close()

    def replace_tracks(self, video_id: UUID, tracks: List[ObjectTrack]) -> None:
        """Idempotent: clear this video's tracks, then insert."""
        self._conn.execute("DELETE FROM object_tracks WHERE video_id = ?", (str(video_id),))
        self._conn.executemany(
            "INSERT INTO object_tracks (id, video_id, object_class, track_confidence, "
            "first_seen, last_seen, frame_count, appearance_ref) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (str(t.id), str(t.video_id), t.object_class, t.track_confidence,
                 t.first_seen, t.last_seen, t.frame_count, t.appearance_ref)
                for t in tracks
            ],
        )
        self._conn.commit()

    def count(self, video_id: UUID) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM object_tracks WHERE video_id = ?", (str(video_id),)
        ).fetchone()[0]

    def get_tracks(self, video_id: UUID) -> List[ObjectTrack]:
        rows = self._conn.execute(
            "SELECT * FROM object_tracks WHERE video_id = ? ORDER BY first_seen",
            (str(video_id),),
        ).fetchall()
        return [
            ObjectTrack(
                id=UUID(r["id"]), video_id=UUID(r["video_id"]),
                object_class=r["object_class"], track_confidence=r["track_confidence"],
                first_seen=r["first_seen"], last_seen=r["last_seen"],
                frame_count=r["frame_count"], appearance_ref=r["appearance_ref"],
            )
            for r in rows
        ]

    def select_placed(
        self, classes: Sequence[str], epoch_start: float, epoch_end: float,
        cameras: Optional[Sequence[str]] = None,
    ) -> List[PlacedTrack]:
        """Tracks of the given classes whose ABSOLUTE start falls inside the
        half-open window [epoch_start, epoch_end) — the typed-query tier's
        selection primitive.

        Membership is by the track's absolute start (`videos.start_epoch` +
        `first_seen`), so adjacent windows partition tracks without double
        counting. Videos with no `start_epoch` (A-EV, relative-only) are
        skipped by construction. `cameras` restricts to those `camera_id`s.

        The epoch bounds MUST be Python-computed numbers. Never build them
        with SQLite `strftime('%s', ...)`: it returns TEXT, SQLite orders
        every number below any text, and the comparison silently yields zero
        rows (the false-0 bug the typed-query tier exists to prevent). The
        type check below makes that failure loud.
        """
        for name, bound in (("epoch_start", epoch_start), ("epoch_end", epoch_end)):
            if isinstance(bound, bool) or not isinstance(bound, (int, float)):
                raise TypeError(
                    f"{name} must be a NUMBER of UTC epoch seconds, got "
                    f"{type(bound).__name__} ({bound!r}) — a text bound (e.g. "
                    f"from strftime('%s',...)) compares above every number in "
                    f"SQLite and silently matches nothing")
        # An EMPTY camera subset means "no cameras selected" -> no rows —
        # mirroring classes=[] — never "all cameras" (a falsy-guard slip here
        # would silently inflate a filtered count to the unfiltered total).
        # None keeps its distinct meaning: no camera restriction.
        if not classes or (cameras is not None and len(cameras) == 0):
            return []
        marks = ", ".join("?" for _ in classes)
        sql = (
            "SELECT t.id, t.video_id, t.object_class, t.track_confidence, "
            "t.first_seen, t.last_seen, t.frame_count, t.appearance_ref, "
            "v.camera_id, v.start_epoch "
            "FROM object_tracks t JOIN videos v ON v.id = t.video_id "
            "WHERE v.start_epoch IS NOT NULL "
            f"AND lower(t.object_class) IN ({marks}) "
            "AND (v.start_epoch + t.first_seen) >= ? "
            "AND (v.start_epoch + t.first_seen) < ?"
        )
        params: list = [c.lower() for c in classes] + [epoch_start, epoch_end]
        if cameras:
            cam_marks = ", ".join("?" for _ in cameras)
            sql += f" AND v.camera_id IN ({cam_marks})"
            params.extend(cameras)
        sql += " ORDER BY (v.start_epoch + t.first_seen), t.id"
        out: List[PlacedTrack] = []
        for r in self._conn.execute(sql, params).fetchall():
            track = ObjectTrack(
                id=UUID(r["id"]), video_id=UUID(r["video_id"]),
                object_class=r["object_class"], track_confidence=r["track_confidence"],
                first_seen=r["first_seen"], last_seen=r["last_seen"],
                frame_count=r["frame_count"], appearance_ref=r["appearance_ref"],
            )
            out.append(PlacedTrack(
                track=track, camera=r["camera_id"],
                first_seen_epoch=r["start_epoch"] + r["first_seen"],
                last_seen_epoch=r["start_epoch"] + r["last_seen"],
            ))
        return out

    def window_anchoring(
        self, classes: Sequence[str], min_frames: int = 1,
    ) -> WindowAnchoring:
        """Coverage facts for a windowed query's disclosure (see
        WindowAnchoring). Placed videos are counted DONE-only (mirroring
        `Catalog.footage_domains`' load-bearing restriction — a failed ingest
        row must not make a workdir look windowable); excluded tracks are
        counted from the rows that actually exist, class- and
        min_frames-matched so the number is comparable to the count that
        excluded them."""
        placed = self._conn.execute(
            "SELECT COUNT(*) FROM videos WHERE start_epoch IS NOT NULL "
            "AND ingest_status = 'done'").fetchone()[0]
        unplaced = 0
        if classes:
            marks = ", ".join("?" for _ in classes)
            unplaced = self._conn.execute(
                "SELECT COUNT(*) FROM object_tracks t "
                "JOIN videos v ON v.id = t.video_id "
                "WHERE v.start_epoch IS NULL "
                f"AND lower(t.object_class) IN ({marks}) "
                "AND t.frame_count >= ?",
                [c.lower() for c in classes] + [min_frames],
            ).fetchone()[0]
        return WindowAnchoring(placed_videos=placed, unplaced_tracks=unplaced)

    def distinct_counts(
        self, classes: Sequence[str], video_id: Optional[UUID] = None,
        min_frames: int = 1,
    ) -> List[DistinctCount]:
        """Distinct object instances per video & class ("how many different X").

        min_frames filters single-frame tracks (often detector flicker)."""
        if not classes:
            return []
        marks = ", ".join("?" for _ in classes)
        sql = (
            "SELECT video_id, object_class, COUNT(*) AS n, "
            "MIN(first_seen) AS first_seen, MAX(last_seen) AS last_seen "
            "FROM object_tracks "
            f"WHERE lower(object_class) IN ({marks}) AND frame_count >= ?"
        )
        params: list = [c.lower() for c in classes] + [min_frames]
        if video_id is not None:
            sql += " AND video_id = ?"
            params.append(str(video_id))
        sql += " GROUP BY video_id, object_class ORDER BY n DESC"
        return [
            DistinctCount(
                video_id=UUID(r["video_id"]), object_class=r["object_class"],
                distinct=r["n"], first_seen=r["first_seen"], last_seen=r["last_seen"],
            )
            for r in self._conn.execute(sql, params).fetchall()
        ]
