from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Mood(str, Enum):
    CHILL = "chill"
    GROOVE = "groove"
    HYPE = "hype"
    DROP = "drop"


@dataclass(frozen=True)
class MoodConfig:
    # --- Entry thresholds (higher than exit to create hysteresis) ---
    chill_rms_ceiling: float = 0.10
    groove_rms_ceiling: float = 0.24
    stability_threshold: float = 0.07
    min_bpm_for_groove: float = 70.0

    # --- Exit thresholds (lower than entry) ---
    chill_rms_exit: float = 0.13       # must exceed this to leave CHILL
    groove_rms_exit_low: float = 0.07  # drop below this to fall to CHILL
    groove_rms_exit_high: float = 0.27 # exceed this to rise to HYPE
    hype_rms_exit: float = 0.20        # drop below this to fall to GROOVE
    stability_exit: float = 0.09       # exceed this to lose stable-beat requirement

    # --- DROP detection ---
    drop_rms_spike: float = 0.15   # RMS must jump by this much
    drop_rms_dip: float = 0.10     # RMS must have been below this recently
    drop_window: float = 0.5       # spike must happen within this many seconds of dip
    drop_duration: float = 3.0     # DROP auto-expires after this many seconds
    drop_cooldown: float = 10.0    # minimum seconds between DROP detections

    # --- Dwell ---
    min_dwell_seconds: float = 4.0  # minimum time in any mood before switching


class MoodClassifier:
    """Classifies audio into CHILL / GROOVE / HYPE / DROP moods.

    Wraps Director state (ema_rms, stability, effective_bpm) into discrete
    mood states with hysteresis and minimum dwell time.
    """

    def __init__(self, config: MoodConfig | None = None) -> None:
        self.config = config or MoodConfig()
        self.mood: Mood = Mood.CHILL
        self._mood_entered_at: float = -1e9
        self._drop_entered_at: float = -1e9
        self._last_drop_at: float = -1e9

        # DROP detection: track recent RMS dip
        self._dip_seen = False
        self._dip_at: float = -1e9
        self._prev_ema_rms: float = 0.0

    def _can_switch(self, t: float) -> bool:
        return (t - self._mood_entered_at) >= self.config.min_dwell_seconds

    def _set_mood(self, mood: Mood, t: float) -> None:
        self.mood = mood
        self._mood_entered_at = t

    def _check_drop(self, ema_rms: float, t: float) -> bool:
        """Detect DROP: RMS spikes by >= drop_rms_spike after a recent dip."""
        # Track dips
        if ema_rms < self.config.drop_rms_dip:
            self._dip_seen = True
            self._dip_at = t

        # Check for spike after dip
        if (
            self._dip_seen
            and (t - self._dip_at) <= self.config.drop_window
            and (ema_rms - self._prev_ema_rms) >= self.config.drop_rms_spike
            and (t - self._last_drop_at) >= self.config.drop_cooldown
        ):
            self._dip_seen = False
            return True

        # Expire stale dip
        if self._dip_seen and (t - self._dip_at) > self.config.drop_window:
            self._dip_seen = False

        return False

    def _classify_steady_state(
        self, ema_rms: float, stability: float, bpm: float
    ) -> Mood:
        """Determine the target mood ignoring hysteresis/dwell (raw classification)."""
        stable_beat = stability <= self.config.stability_threshold
        if ema_rms < self.config.chill_rms_ceiling:
            return Mood.CHILL
        if stability > self.config.stability_exit and bpm < self.config.min_bpm_for_groove:
            return Mood.CHILL
        if ema_rms >= self.config.groove_rms_ceiling and stable_beat:
            return Mood.HYPE
        if stable_beat and bpm >= self.config.min_bpm_for_groove:
            return Mood.GROOVE
        return Mood.CHILL

    def update(
        self, ema_rms: float, stability: float, bpm: float, t: float,
    ) -> Mood:
        # --- DROP detection (always checked, overrides everything) ---
        is_drop = self._check_drop(ema_rms, t)
        self._prev_ema_rms = ema_rms

        if is_drop:
            self._set_mood(Mood.DROP, t)
            self._last_drop_at = t
            self._drop_entered_at = t
            return self.mood

        # --- DROP auto-expiry ---
        if self.mood == Mood.DROP:
            if (t - self._drop_entered_at) >= self.config.drop_duration:
                # Transition to whatever the current audio warrants
                target = self._classify_steady_state(ema_rms, stability, bpm)
                self._set_mood(target, t)
            return self.mood

        # --- Dwell guard ---
        if not self._can_switch(t):
            return self.mood

        # --- Hysteresis-aware transitions ---
        target = self._classify_steady_state(ema_rms, stability, bpm)

        if self.mood == Mood.CHILL:
            # Need to exceed exit threshold to leave CHILL
            if target in (Mood.GROOVE, Mood.HYPE) and ema_rms >= self.config.chill_rms_exit:
                self._set_mood(target, t)

        elif self.mood == Mood.GROOVE:
            if target == Mood.HYPE and ema_rms >= self.config.groove_rms_exit_high:
                self._set_mood(Mood.HYPE, t)
            elif target == Mood.CHILL and ema_rms < self.config.groove_rms_exit_low:
                self._set_mood(Mood.CHILL, t)
            elif target == Mood.CHILL and stability > self.config.stability_exit:
                self._set_mood(Mood.CHILL, t)

        elif self.mood == Mood.HYPE:
            if target in (Mood.GROOVE, Mood.CHILL) and ema_rms < self.config.hype_rms_exit:
                self._set_mood(target, t)

        return self.mood
