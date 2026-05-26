"""GUI state for capture-pipeline configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CaptureSettings:
    capture_dir: str = "captured_songs"
    naming_mode: str = "timestamp"
    max_capture_buffer: int = 0
    device_pattern: str = "CABLE Output"
    sample_rate: int = 44100
    channels: int = 2
    frame_size: int = 2048
    hop_size: int = 512
    blocksize: int = 1024
    pipeline_playback_device_id: int | None = None
    purge_after_playback: bool = False
    debug_pipeline: bool = False

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if self.naming_mode not in {"timestamp", "metadata"}:
            errors.append("Capture naming mode must be 'timestamp' or 'metadata'.")
        if self.max_capture_buffer < 0:
            errors.append("Capture buffer must be 0 or greater.")
        if not self.device_pattern.strip():
            errors.append("Capture device pattern is required.")
        if self.sample_rate <= 0:
            errors.append("Capture sample rate must be greater than 0.")
        if self.channels <= 0:
            errors.append("Capture channels must be greater than 0.")
        if self.frame_size <= 0 or self.hop_size <= 0 or self.blocksize <= 0:
            errors.append("Capture frame, hop, and block sizes must be greater than 0.")
        return tuple(errors)

