"""Runtime playback for baked frame artifacts."""

from __future__ import annotations

from bisect import bisect_left
import time
from typing import Any

from dreamsync.show.baked_frames import BakedFrame, BakedFrameArtifact
from dreamsync.show.runtime_control import runtime_control_to_dict


class BakedFramePlaybackRuntime:
    """Play precomputed frame colors through an adapter."""

    def __init__(
        self,
        artifact: BakedFrameArtifact,
        multi_adapter,
        *,
        control_state_getter=None,
    ) -> None:
        self._artifact = artifact
        self._multi_adapter = multi_adapter
        self._frame_times = tuple(frame.t for frame in artifact.frames)
        self._frames_sent = 0
        self._lookup_count = 0
        self._lookup_total_seconds = 0.0
        self._lookup_max_seconds = 0.0
        self._control_state_getter = control_state_getter

    @property
    def stats(self) -> dict[str, Any]:
        average_lookup_ms = (
            self._lookup_total_seconds / self._lookup_count * 1000.0
            if self._lookup_count
            else 0.0
        )
        return {
            "frames_sent": self._frames_sent,
            "frame_count": len(self._artifact.frames),
            "node_count": len(self._artifact.nodes),
            "frame_lookup_count": self._lookup_count,
            "frame_lookup_avg_ms": round(average_lookup_ms, 4),
            "frame_lookup_max_ms": round(self._lookup_max_seconds * 1000.0, 4),
        }

    def frame_at(self, t: float) -> BakedFrame | None:
        """Return the nearest baked frame for playback time *t*."""
        started_at = time.perf_counter()
        try:
            if not self._artifact.frames:
                return None
            index = bisect_left(self._frame_times, t)
            if index <= 0:
                return self._artifact.frames[0]
            if index >= len(self._artifact.frames):
                return self._artifact.frames[-1]
            before = self._artifact.frames[index - 1]
            after = self._artifact.frames[index]
            if abs(t - before.t) <= abs(after.t - t):
                return before
            return after
        finally:
            elapsed = time.perf_counter() - started_at
            self._lookup_count += 1
            self._lookup_total_seconds += elapsed
            if elapsed > self._lookup_max_seconds:
                self._lookup_max_seconds = elapsed

    def tick(self, t: float) -> bool:
        frame = self.frame_at(t)
        if frame is None:
            return False
        send_baked_frame = getattr(self._multi_adapter, "send_baked_frame", None)
        if not callable(send_baked_frame):
            raise TypeError("Adapter does not support send_baked_frame")
        node_colors = {
            node.key: color
            for node, color in zip(self._artifact.nodes, frame.colors)
        }
        params = None
        if self._control_state_getter is not None:
            state = self._control_state_getter()
            if state is not None:
                params = {"runtime_control": runtime_control_to_dict(state)}
        sent = bool(
            send_baked_frame(t, node_colors, params=params)
            if params is not None
            else send_baked_frame(t, node_colors)
        )
        if sent:
            self._frames_sent += 1
        return sent
