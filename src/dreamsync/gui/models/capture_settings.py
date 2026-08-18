"""GUI state for capture-pipeline configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dreamsync.capture.ffmpeg_device import default_capture_pattern


@dataclass(frozen=True)
class CaptureSettings:
    # ``capture_dir`` remains as a compatibility alias for older callers. New
    # GUI/runtime code writes live segments to ``temp_capture_root``.
    capture_dir: str = "library/temp"
    captured_audio_root: str = "library/audio"
    analysis_root: str = "library/analysis"
    compiled_show_root: str = "library/shows"
    temp_capture_root: str = "library/temp"
    temp_retention_hours: int = 24
    naming_mode: str = "timestamp"
    max_capture_buffer: int = 0
    device_pattern: str = field(default_factory=default_capture_pattern)
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
        roots = {
            "Captured audio root": self.captured_audio_root,
            "Analysis root": self.analysis_root,
            "Compiled show root": self.compiled_show_root,
            "Temporary capture root": self.temp_capture_root,
        }
        for label, value in roots.items():
            if not str(value).strip():
                errors.append(f"{label} is required.")
        normalized_roots = [
            str(Path(value).expanduser().resolve()).casefold()
            for value in roots.values() if str(value).strip()
        ]
        if len(set(normalized_roots)) != len(normalized_roots):
            errors.append("Storage roots must use four different directories.")
        if self.temp_retention_hours < 1:
            errors.append("Temporary capture retention must be at least 1 hour.")
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


@dataclass(frozen=True)
class LearnedLiveSettings:
    enabled: bool = False
    learning_enabled: bool = True
    profile_path: str = ""
    capture_device_pattern: str = "CABLE Output"
    mp3_retention_policy: str = "keep_recent"
    retained_mp3_limit: int = 10
    diagnostic_logging: bool = False

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if self.mp3_retention_policy not in {
            "keep_all", "keep_recent", "delete_after_verified_compile"
        }:
            errors.append("Learned-live MP3 retention policy is invalid.")
        if self.retained_mp3_limit < 0:
            errors.append("Learned-live retained MP3 limit must be 0 or greater.")
        if self.learning_enabled and not self.capture_device_pattern.strip():
            errors.append("Learned-live capture device pattern is required.")
        return tuple(errors)
