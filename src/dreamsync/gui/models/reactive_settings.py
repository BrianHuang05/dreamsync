"""GUI state for reactive/live runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReactiveSettings:
    render_mode: str = "scroll"
    sample_rate: int = 44100
    channels: int = 1
    frame_size: int = 2048
    hop_size: int = 512
    blocksize: int = 1024
    half_time: bool = False
    max_brightness: bool = False
    mirror: bool = True
    master_brightness: float = 1.0
    auto_cycle: bool = True
    cycle_interval: float = 16.0
    debug_mood: bool = False
    telemetry_dir: str = ""
    crossfade_detect: bool = False
    harmonic_structure_enabled: bool = False
    beats_per_bar: int = 4
    bars_per_phrase: int = 4
    harmonic_frame_size: int = 4096
    harmonic_hop_multiplier: int = 1
    structure_sensitivity: float = 0.5
    downbeat_min_confidence: float = 0.22
    debug_harmonics: bool = False
    predictive_analysis_enabled: bool = False
    predictive_diagnostics_enabled: bool = False
    predictive_shadow_mode: bool = True
    predictive_cues_enabled: bool = False
    predictive_high_impact_cues_enabled: bool = False
    predictive_cue_prepare_threshold: float = 0.54
    predictive_cue_schedule_threshold: float = 0.68
    predictive_cue_high_impact_threshold: float = 0.80
    predictive_maximum_anticipatory_intensity: float = 0.28
    predictive_cue_cooldown_seconds: float = 2.0
    predictive_allowed_cue_classes: tuple[str, ...] = (
        "chord_accent",
        "resolution_bloom",
        "phrase_reset",
        "section_recall",
        "chorus_lift",
    )
    structure_similarity_enabled: bool = False
    structure_similarity_diagnostics: bool = False
    structure_similarity_shadow_mode: bool = True
    structure_bar_actions_enabled: bool = False
    structure_phrase_actions_enabled: bool = False
    structure_section_actions_enabled: bool = False
    structure_allow_secondary_beat_modulation: bool = False
    structure_use_tonal_sidecar: bool = False
    structure_memory_bars: int = 256
    structure_min_meter_confidence: float = 0.22
    structure_phrase_threshold: float = 0.48
    structure_section_threshold: float = 0.62
    structure_large_action_threshold: float = 0.80
    profile_strategy: str = "active_profile"
    profile_override_path: str = ""
    show_palette_set: str = ""
    rotation_profiles: tuple[str, ...] = field(default_factory=tuple)
    rotation_interval: float = 300.0
    auto_palette: bool = False
    smart_rotation: bool = False
    chain_blend_seconds: float = 8.0
    auto_palette_seed: int | None = None
    auto_palette_pool_size: int = 8
    chain_dwell_range_enabled: bool = False
    chain_min_dwell_seconds: float = 60.0
    chain_max_dwell_seconds: float = 180.0

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if self.render_mode not in {"solid", "pulse", "scroll", "breathe", "wave", "gradient"}:
            errors.append(
                "Reactive render mode must be one of solid, pulse, scroll, breathe, wave, or gradient."
            )
        if self.sample_rate <= 0:
            errors.append("Reactive sample rate must be greater than 0.")
        if self.channels <= 0:
            errors.append("Reactive channels must be greater than 0.")
        if self.frame_size <= 0 or self.hop_size <= 0 or self.blocksize <= 0:
            errors.append("Reactive frame, hop, and block sizes must be greater than 0.")
        if self.cycle_interval <= 0:
            errors.append("Reactive cycle interval must be greater than 0.")
        if self.beats_per_bar < 2 or self.bars_per_phrase < 1:
            errors.append("Reactive bar and phrase sizes are invalid.")
        if (
            self.harmonic_frame_size < self.frame_size
            or self.harmonic_frame_size
            & (self.harmonic_frame_size - 1)
        ):
            errors.append(
                "Reactive harmonic frame size must be a power of two "
                "at least as large as the ordinary frame."
            )
        if self.harmonic_hop_multiplier < 1:
            errors.append(
                "Reactive harmonic hop multiplier must be greater than 0."
            )
        if not 0.0 <= self.structure_sensitivity <= 1.0:
            errors.append(
                "Reactive structure sensitivity must be between 0 and 1."
            )
        if not 0.0 < self.downbeat_min_confidence < 1.0:
            errors.append(
                "Reactive downbeat confidence must be between 0 and 1."
            )
        if not (
            0.0
            <= self.predictive_cue_prepare_threshold
            <= self.predictive_cue_schedule_threshold
            <= self.predictive_cue_high_impact_threshold
            <= 1.0
        ):
            errors.append(
                "Predictive cue thresholds must be ordered between 0 and 1."
            )
        if not 0.0 <= self.predictive_maximum_anticipatory_intensity <= 1.0:
            errors.append("Predictive cue intensity must be between 0 and 1.")
        if self.predictive_cue_cooldown_seconds < 0.0:
            errors.append("Predictive cue cooldown cannot be negative.")
        if not 16 <= self.structure_memory_bars <= 2048:
            errors.append("Structure memory must contain between 16 and 2048 bars.")
        if not 0.0 < self.structure_min_meter_confidence < 1.0:
            errors.append("Structure meter confidence must be between 0 and 1.")
        if (
            self.structure_similarity_enabled
            and self.harmonic_structure_enabled
        ):
            errors.append(
                "Choose either legacy harmonic structure or structure similarity, not both."
            )
        if not 0.05 <= self.master_brightness <= 1.0:
            errors.append("Reactive master brightness must be between 0.05 and 1.0.")
        if self.rotation_interval <= 0:
            errors.append("Profile rotation interval must be greater than 0.")
        if self.chain_blend_seconds <= 0:
            errors.append("Chain blend duration must be greater than 0.")
        if not 2 <= self.auto_palette_pool_size <= 32:
            errors.append("Auto-palette pool size must be between 2 and 32.")
        if self.chain_dwell_range_enabled:
            if self.chain_min_dwell_seconds <= 0 or self.chain_max_dwell_seconds <= 0:
                errors.append("Chain dwell durations must be greater than 0.")
            elif self.chain_min_dwell_seconds > self.chain_max_dwell_seconds:
                errors.append("Minimum chain dwell duration cannot exceed the maximum.")
        if self.profile_strategy not in {
            "active_profile",
            "override_profile",
            "auto_profile",
            "song_change_rotation",
            "profile_rotation",
            "smart_rotation",
        }:
            errors.append("Reactive profile strategy is invalid.")
        if self.profile_strategy == "override_profile" and not self.profile_override_path.strip():
            errors.append("Choose a reactive profile override path or switch back to the active profile.")
        if self.profile_strategy in {
            "song_change_rotation",
            "profile_rotation",
            "smart_rotation",
        } and not self.rotation_profiles and not self.auto_palette:
            errors.append("Provide one or more profiles before starting Reactive profile cycling.")
        return tuple(errors)
