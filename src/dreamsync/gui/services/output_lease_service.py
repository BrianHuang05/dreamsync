"""Runtime ownership guards for lighting and audible audio output."""

from __future__ import annotations

import threading
from dataclasses import dataclass


QUEUE_AUDIO_OWNER = "queue"


class AudioOutputInvariantError(RuntimeError):
    """Raised when a non-Queue component attempts audible output."""


@dataclass(frozen=True)
class LightingLeaseSnapshot:
    owner: str = ""
    mode: str = "idle"
    simulation_only: bool = True


@dataclass(frozen=True)
class AudioLeaseSnapshot:
    owner: str = ""
    mode: str = "idle"
    violation_count: int = 0
    last_violation: str = ""


class OutputLeaseService:
    """Keep lighting ownership separate from Queue-only audio ownership."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._lighting = LightingLeaseSnapshot()
        self._audio = AudioLeaseSnapshot()

    def acquire_lighting(
        self,
        *,
        owner: str,
        mode: str,
        simulation_only: bool,
    ) -> LightingLeaseSnapshot:
        requested_owner = str(owner or "").strip()
        if not requested_owner:
            raise ValueError("Lighting output requires a non-empty owner.")
        with self._lock:
            current = self._lighting
            if current.owner and current.owner != requested_owner:
                raise RuntimeError(
                    "Lighting output is already owned by "
                    f"{current.owner!r} ({current.mode})."
                )
            self._lighting = LightingLeaseSnapshot(
                owner=requested_owner,
                mode=str(mode or requested_owner),
                simulation_only=bool(simulation_only),
            )
            return self._lighting

    def release_lighting(self, *, owner: str | None = None) -> None:
        with self._lock:
            if owner is None or self._lighting.owner == owner:
                self._lighting = LightingLeaseSnapshot()

    def acquire_audio(self, *, owner: str, mode: str) -> AudioLeaseSnapshot:
        requested_owner = str(owner or "").strip()
        requested_mode = str(mode or requested_owner or "idle")
        with self._lock:
            if requested_owner != QUEUE_AUDIO_OWNER:
                message = (
                    "DreamSync audible output is Queue-only; "
                    f"{requested_owner or 'unknown'!r} requested it for {requested_mode!r}."
                )
                self._record_audio_violation(message)
                raise AudioOutputInvariantError(message)
            current = self._audio
            if current.owner and current.owner != requested_owner:
                message = (
                    f"Audio output is already owned by {current.owner!r} "
                    f"for {current.mode!r}."
                )
                self._record_audio_violation(message)
                raise AudioOutputInvariantError(message)
            self._audio = AudioLeaseSnapshot(
                owner=QUEUE_AUDIO_OWNER,
                mode=requested_mode,
                violation_count=current.violation_count,
                last_violation=current.last_violation,
            )
            return self._audio

    def require_queue_audio(self, *, mode: str) -> None:
        with self._lock:
            current = self._audio
            if current.owner == QUEUE_AUDIO_OWNER:
                return
            message = (
                "Queue audio player construction requires an active Queue audio lease; "
                f"no lease is held for {str(mode or 'unknown')!r}."
            )
            self._record_audio_violation(message)
            raise AudioOutputInvariantError(message)

    def release_audio(self, *, owner: str | None = None) -> None:
        with self._lock:
            if owner is None or self._audio.owner == owner:
                self._audio = AudioLeaseSnapshot(
                    violation_count=self._audio.violation_count,
                    last_violation=self._audio.last_violation,
                )

    def lighting_snapshot(self) -> LightingLeaseSnapshot:
        with self._lock:
            return self._lighting

    def audio_snapshot(self) -> AudioLeaseSnapshot:
        with self._lock:
            return self._audio

    def _record_audio_violation(self, message: str) -> None:
        current = self._audio
        self._audio = AudioLeaseSnapshot(
            owner=current.owner,
            mode=current.mode,
            violation_count=current.violation_count + 1,
            last_violation=message,
        )
