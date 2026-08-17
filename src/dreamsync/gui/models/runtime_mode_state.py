"""Runtime supervisor state models for the GUI control room."""

from __future__ import annotations

from dataclasses import dataclass, field

from .capture_settings import CaptureSettings, LearnedLiveSettings
from .reactive_settings import ReactiveSettings
from .runtime_routing_state import RuntimeRoutingState


@dataclass(frozen=True)
class LightingOutputLease:
    owner: str = ""
    mode: str = "idle"
    simulation_only: bool = True


@dataclass(frozen=True)
class AudioOutputLease:
    owner: str = ""
    mode: str = "idle"
    violation_count: int = 0
    last_violation: str = ""


# Compatibility name for code that still renders the former broad lease.
OutputLease = LightingOutputLease


@dataclass(frozen=True)
class CapturedShowItem:
    item_id: str = ""
    mp3_path: str = ""
    analysis_path: str | None = None
    show_path: str | None = None
    state: str = "captured"
    title: str = ""
    artist: str = ""
    duration: float | None = None
    error: str | None = None

    @property
    def display_label(self) -> str:
        title = self.title.strip()
        artist = self.artist.strip()
        if title and artist:
            return f"{artist} - {title}"
        if title:
            return title
        if artist:
            return artist
        return self.mp3_path


@dataclass(frozen=True)
class RuntimeModeState:
    active_output_mode: str = "idle"
    armed_output_mode: str = ""
    simulation_target: str = ""
    capture_state: str = "off"
    pipeline_state: str = "idle"
    spotify_state: str = "off"
    learned_live_strategy: str = "waiting"
    learned_live_badge: str = ""
    learned_track_source: str = "waiting"
    learned_track_source_detail: str = ""
    learning_state: str = "idle"
    learning_reason: str = ""
    learned_library_count: int = 0
    learned_cache_hits: int = 0
    learned_cache_misses: int = 0
    learned_existing_mp3_reuses: int = 0
    background_learning_items: tuple[str, ...] = field(default_factory=tuple)
    ready_queue_count: int = 0
    ready_items: tuple[CapturedShowItem, ...] = field(default_factory=tuple)
    lighting_output_lease: LightingOutputLease = field(default_factory=LightingOutputLease)
    audio_output_lease: AudioOutputLease = field(default_factory=AudioOutputLease)
    output_lease: OutputLease = field(default_factory=OutputLease)
    playback_status: str = "idle"
    current_track: str = ""
    local_source_path: str = ""
    saved_show_path: str = ""
    selected_show_path: str = ""
    reactive_effect_mode: str = ""
    device_status: str = ""
    audio_output: str = ""
    input_device: str = ""
    routing_state: RuntimeRoutingState = field(default_factory=RuntimeRoutingState)
    capture_settings: CaptureSettings = field(default_factory=CaptureSettings)
    learned_live_settings: LearnedLiveSettings = field(default_factory=LearnedLiveSettings)
    reactive_settings: ReactiveSettings = field(default_factory=ReactiveSettings)
    recent_saved_shows: tuple[str, ...] = field(default_factory=tuple)
    status_message: str = ""
    error_message: str = ""
