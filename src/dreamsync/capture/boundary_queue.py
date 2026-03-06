"""Mutable, sorted boundary queue with safety-margin enforcement."""

from __future__ import annotations

import bisect
import threading
from dataclasses import dataclass, field


@dataclass
class BoundaryEntry:
    """A single segment split point."""

    frame_position: int
    segment_index: int
    metadata: dict | None = None
    locked: bool = False

    def __lt__(self, other: BoundaryEntry) -> bool:  # type: ignore[override]
        return self.frame_position < other.frame_position


class BoundaryQueue:
    """Thread-safe sorted queue of upcoming segment boundaries.

    Parameters
    ----------
    safety_margin_frames:
        Boundaries within this many frames of ``current_frame`` are
        considered locked and cannot be modified.  Default 24 000
        frames = 0.5 s at 48 kHz.
    """

    def __init__(self, safety_margin_frames: int = 24_000, logger: object | None = None) -> None:
        self._entries: list[BoundaryEntry] = []
        self._lock = threading.Lock()
        self._safety_margin = safety_margin_frames
        self._logger = logger

    @property
    def safety_margin_frames(self) -> int:
        return self._safety_margin

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def peek_next(self) -> BoundaryEntry | None:
        """Return the next upcoming boundary without removing it."""
        with self._lock:
            return self._entries[0] if self._entries else None

    def pop_next(self) -> BoundaryEntry | None:
        """Return and remove the next boundary."""
        with self._lock:
            if not self._entries:
                return None
            entry = self._entries.pop(0)
        if self._logger is not None:
            self._logger.boundary_event(
                "pop_next",
                frame_position=entry.frame_position,
                metadata_present=entry.metadata is not None,
                remaining=len(self),
            )
        return entry

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def entries(self) -> list[BoundaryEntry]:
        """Return a snapshot of all entries."""
        with self._lock:
            return list(self._entries)

    # ------------------------------------------------------------------
    # Mutation operations (respect safety margin)
    # ------------------------------------------------------------------

    def add(self, entry: BoundaryEntry) -> None:
        """Insert a boundary, maintaining sorted order."""
        with self._lock:
            bisect.insort(self._entries, entry)

    def update(
        self,
        index: int,
        new_frame_position: int,
        current_frame: int,
    ) -> bool:
        """Move a boundary to a new position.  Returns False if locked."""
        with self._lock:
            if index < 0 or index >= len(self._entries):
                return False
            entry = self._entries[index]
            if self._is_locked(entry, current_frame):
                return False
            self._entries.pop(index)
            entry.frame_position = new_frame_position
            entry.locked = False
            bisect.insort(self._entries, entry)
            return True

    def remove(self, index: int, current_frame: int) -> bool:
        """Remove a boundary.  Returns False if locked."""
        with self._lock:
            if index < 0 or index >= len(self._entries):
                return False
            if self._is_locked(self._entries[index], current_frame):
                return False
            self._entries.pop(index)
            return True

    def replace_future(
        self,
        new_boundaries: list[BoundaryEntry],
        current_frame: int,
    ) -> None:
        """Replace all unlocked boundaries with *new_boundaries*.

        Locked entries (within safety margin) are preserved.
        """
        with self._lock:
            threshold = current_frame + self._safety_margin
            locked = [e for e in self._entries if e.frame_position <= threshold]
            for e in locked:
                e.locked = True
            merged = locked + list(new_boundaries)
            merged.sort()
            self._entries = merged
        if self._logger is not None:
            self._logger.boundary_event(
                "replace_future",
                frame_position=current_frame,
                num_locked=len(locked),
                num_new=len(new_boundaries),
                num_merged=len(merged),
            )

    def update_locks(self, current_frame: int) -> None:
        """Mark boundaries within safety margin as locked."""
        with self._lock:
            threshold = current_frame + self._safety_margin
            for entry in self._entries:
                if entry.frame_position <= threshold:
                    entry.locked = True
                else:
                    break

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _is_locked(self, entry: BoundaryEntry, current_frame: int) -> bool:
        return (
            entry.locked
            or entry.frame_position <= current_frame + self._safety_margin
        )
