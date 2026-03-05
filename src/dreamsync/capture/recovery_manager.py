"""Failure handling and recovery for capture and encoder processes."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class RecoveryConfig:
    """Tunable recovery parameters."""

    capture_restart_delay: float = 0.2
    max_capture_retries: int = 5
    encoder_retry_count: int = 1
    gap_strategy: str = "leave"  # "leave" or "pad_silence"


@dataclass
class GapRecord:
    """Records a gap in the audio stream caused by a capture failure."""

    start_frame: int
    end_frame: int = 0
    duration_frames: int = 0
    timestamp: float = 0.0


class RecoveryManager:
    """Handle capture and encoder process failures with automatic recovery.

    Parameters
    ----------
    capture_manager:
        Object with ``start(device_name)``, ``stop()``, ``is_alive()``
        methods (e.g. :class:`CaptureProcessManager`).
    config:
        Recovery configuration.
    """

    def __init__(
        self,
        capture_manager=None,
        config: RecoveryConfig | None = None,
    ) -> None:
        self._capture = capture_manager
        self._config = config or RecoveryConfig()
        self._consecutive_capture_failures: int = 0
        self._gaps: list[GapRecord] = []
        self._total_capture_restarts: int = 0
        self._total_encoder_failures: int = 0

    @property
    def gaps(self) -> list[GapRecord]:
        return list(self._gaps)

    @property
    def total_capture_restarts(self) -> int:
        return self._total_capture_restarts

    @property
    def total_encoder_failures(self) -> int:
        return self._total_encoder_failures

    # ------------------------------------------------------------------
    # Capture failure
    # ------------------------------------------------------------------

    def handle_capture_failure(
        self,
        frame_position: int,
        device_name: str | None = None,
    ) -> bool:
        """Attempt to restart the capture process.

        Returns True if recovery succeeded, False if retries exhausted.
        """
        self._consecutive_capture_failures += 1

        if self._consecutive_capture_failures > self._config.max_capture_retries:
            logger.error(
                "Max capture retries (%d) exhausted — giving up",
                self._config.max_capture_retries,
            )
            return False

        gap = GapRecord(start_frame=frame_position, timestamp=time.time())
        logger.warning(
            "Capture failure #%d at frame %d — restarting in %.1fs",
            self._consecutive_capture_failures,
            frame_position,
            self._config.capture_restart_delay,
        )

        time.sleep(self._config.capture_restart_delay)

        try:
            if self._capture is not None:
                self._capture.stop()
                self._capture.start(device_name=device_name)
            self._total_capture_restarts += 1
            gap.end_frame = frame_position  # Gap covers the restart period
            gap.duration_frames = 0  # Unknown actual gap length
            self._gaps.append(gap)
            logger.info("Capture restarted successfully (attempt #%d)",
                        self._consecutive_capture_failures)
            return True
        except Exception:
            logger.exception("Capture restart failed")
            return self.handle_capture_failure(frame_position, device_name)

    def reset_capture_failure_count(self) -> None:
        """Call after a successful chunk read to reset the retry counter."""
        self._consecutive_capture_failures = 0

    # ------------------------------------------------------------------
    # Encoder failure
    # ------------------------------------------------------------------

    def handle_encoder_failure(
        self,
        segment_info: dict,
        start_encoder_fn=None,
    ) -> str:
        """Handle encoder failure with retry → fallback → skip cascade.

        Returns ``"retried"``, ``"fallback"``, or ``"skipped"``.
        """
        self._total_encoder_failures += 1
        seg_idx = segment_info.get("segment_index", "?")
        logger.warning("Encoder failure for segment %s", seg_idx)

        # 1. Retry
        for attempt in range(self._config.encoder_retry_count):
            logger.info("Encoder retry %d/%d for segment %s",
                        attempt + 1, self._config.encoder_retry_count, seg_idx)
            try:
                if start_encoder_fn is not None:
                    encoder = start_encoder_fn(segment_info)
                    if encoder is not None:
                        return "retried"
            except Exception:
                logger.exception("Encoder retry %d failed", attempt + 1)

        # 2. Fallback: write raw PCM
        output_path = segment_info.get("output_path")
        pcm_data = segment_info.get("pcm_data")
        if output_path and pcm_data:
            raw_path = output_path.replace(".mp3", ".raw")
            try:
                with open(raw_path, "wb") as f:
                    f.write(pcm_data)
                logger.info("Fallback: wrote raw PCM to %s", raw_path)
                return "fallback"
            except Exception:
                logger.exception("Fallback raw write failed")

        # 3. Skip
        logger.error("Skipping segment %s after all recovery attempts", seg_idx)
        return "skipped"
