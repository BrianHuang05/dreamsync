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
    auto_cycle: bool = True
    cycle_interval: float = 16.0
    debug_mood: bool = False
    telemetry_dir: str = ""
    crossfade_detect: bool = False
    profile_strategy: str = "active_profile"
    profile_override_path: str = ""
    rotation_profiles: tuple[str, ...] = field(default_factory=tuple)
    rotation_interval: float = 300.0
    auto_palette: bool = False
    smart_rotation: bool = False
    chain_blend_seconds: float = 8.0

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
        if self.rotation_interval <= 0:
            errors.append("Profile rotation interval must be greater than 0.")
        if self.chain_blend_seconds <= 0:
            errors.append("Chain blend duration must be greater than 0.")
        if self.profile_strategy not in {
            "active_profile",
            "override_profile",
            "auto_profile",
            "profile_rotation",
            "smart_rotation",
        }:
            errors.append("Reactive profile strategy is invalid.")
        if self.profile_strategy == "override_profile" and not self.profile_override_path.strip():
            errors.append("Choose a reactive profile override path or switch back to the active profile.")
        if self.profile_strategy in {"profile_rotation", "smart_rotation"} and not self.rotation_profiles:
            errors.append("Provide one or more rotation profiles before starting reactive rotation.")
        return tuple(errors)
