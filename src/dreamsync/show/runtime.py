"""Show Playback Runtime — synchronise lighting cues with audio playback."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.output.roles import adapt_render_mode, DeviceType
from dreamsync.render import RenderMode
from dreamsync.show.models import ShowCue, ShowTimeline

# Mapping from ShowCue.render_mode → (EffectMode, RenderMode)
_MODE_MAP: dict[str, tuple[EffectMode, RenderMode]] = {
    "solid": (EffectMode.AMBIENT, RenderMode.SOLID),
    "breathe": (EffectMode.AMBIENT, RenderMode.BREATHE),
    "scroll": (EffectMode.MOTION, RenderMode.SCROLL),
    "pulse": (EffectMode.PULSE, RenderMode.PULSE),
    "wave": (EffectMode.MOTION, RenderMode.WAVE),
    "gradient": (EffectMode.AMBIENT, RenderMode.GRADIENT),
}


class ShowPlaybackRuntime:
    """Advance a show timeline tick-by-tick, mapping cues → lighting output."""

    def __init__(
        self,
        timeline: ShowTimeline,
        multi_adapter,  # MultiGoveeLanAdapter (duck-typed for testability)
        *,
        beat_tolerance: float | None = None,
        color_cycle_mode: str = "downbeat",
    ) -> None:
        self._timeline = timeline
        self._multi_adapter = multi_adapter

        self._current_cue: ShowCue | None = None
        self._color_index: int = 0
        self._prev_color_index: int = 0
        self._beat_fired: bool = False
        self._color_cycle_mode = color_cycle_mode

        # Fade state
        self._fade_start_t: float = 0.0
        self._fade_end_t: float = 0.0
        self._fade_from_cue: ShowCue | None = None

        # Color blend state
        self._color_blend_start_t: float = 0.0
        self._color_blend_duration: float = 2 * (60.0 / timeline.bpm)  # blend over 2 beats

        # Beat tolerance: 30% of beat interval, capped at 40ms
        if beat_tolerance is not None:
            self._beat_tolerance = beat_tolerance
        else:
            beat_interval = 60.0 / timeline.bpm
            self._beat_tolerance = min(0.040, beat_interval * 0.30)

        # Stats
        self._frames_sent: int = 0
        self._cues_played: int = 0
        self._beats_hit: int = 0

    def tick(self, t: float) -> bool:
        """Advance the show to time *t*.

        Returns True if a frame was sent, False otherwise.
        """
        cue = self._timeline.cue_at(t)
        if cue is None:
            return False

        # Handle cue change
        if cue is not self._current_cue:
            self._on_cue_change(cue, t)

        # Build LightingIntent
        intent = self._build_intent(cue, t)

        # Check beat and downbeat grids
        beat = self._timeline.is_beat(t, tolerance=self._beat_tolerance)
        downbeat = self._timeline.is_downbeat(t, tolerance=self._beat_tolerance)

        # Determine if color should cycle based on mode
        if self._color_cycle_mode == "beat":
            should_cycle = beat and not self._beat_fired
            cycle_reset = not beat
        else:  # "downbeat" (default)
            should_cycle = downbeat and not self._beat_fired
            cycle_reset = not downbeat

        if should_cycle:
            self._prev_color_index = self._color_index
            self._color_index = (self._color_index + 1) % len(cue.color_palette)
            self._color_blend_start_t = t
            self._beat_fired = True
            self._beats_hit += 1
        elif cycle_reset:
            self._beat_fired = False

        # Render + send
        sent = self._multi_adapter.send_frame(t, intent, beat=beat, params=cue.params)
        if sent:
            self._frames_sent += 1
        return sent

    @property
    def current_cue(self) -> ShowCue | None:
        return self._current_cue

    @property
    def stats(self) -> dict[str, int]:
        return {
            "frames_sent": self._frames_sent,
            "cues_played": self._cues_played,
            "beats_hit": self._beats_hit,
        }

    # -- Internal ------------------------------------------------------------

    def _on_cue_change(self, cue: ShowCue, t: float) -> None:
        """Handle a transition from the current cue to *cue*."""
        old_cue = self._current_cue

        # Set up fade if requested
        if cue.transition == "fade" and cue.transition_beats > 0 and old_cue is not None:
            fade_duration = cue.transition_beats * 60.0 / self._timeline.bpm
            # Cap at 16 beats
            max_fade = 16 * 60.0 / self._timeline.bpm
            fade_duration = min(fade_duration, max_fade)
            self._fade_from_cue = old_cue
            self._fade_start_t = t
            self._fade_end_t = t + fade_duration
        else:
            self._fade_from_cue = None

        # Switch render mode on all device renderers (device-type aware)
        if hasattr(self._multi_adapter, "devices"):
            for _adapter, renderer, _role, *_ in self._multi_adapter.devices:
                dev_type = getattr(renderer, "device_type", None)
                if dev_type is not None:
                    try:
                        dt = DeviceType(dev_type)
                    except ValueError:
                        dt = None
                    if dt is not None:
                        adapted = adapt_render_mode(cue.render_mode, dt)
                        _, rm = _MODE_MAP.get(adapted, (EffectMode.AMBIENT, RenderMode.SOLID))
                        renderer.mode = rm
                        continue
                _, rm = _MODE_MAP.get(cue.render_mode, (EffectMode.AMBIENT, RenderMode.SOLID))
                renderer.mode = rm

        self._current_cue = cue
        self._cues_played += 1

    def _cue_duration(self, cue: ShowCue) -> float:
        """Duration of this cue (time until next cue or song end)."""
        cue_times = [c.t for c in self._timeline.cues]
        idx = cue_times.index(cue.t)
        if idx + 1 < len(cue_times):
            return cue_times[idx + 1] - cue.t
        return self._timeline.duration - cue.t

    def _build_intent(self, cue: ShowCue, t: float) -> LightingIntent:
        """Construct a LightingIntent from the active cue and current time."""
        intensity = cue.intensity
        speed = cue.speed

        # Apply intensity_start ramp if set
        if cue.intensity_start is not None:
            cue_dur = self._cue_duration(cue)
            if cue_dur > 0:
                progress = min(1.0, (t - cue.t) / cue_dur)
                intensity = _lerp(cue.intensity_start, cue.intensity, progress)

        # Interpolate during fade window
        if (
            self._fade_from_cue is not None
            and self._fade_start_t <= t < self._fade_end_t
        ):
            fade_len = self._fade_end_t - self._fade_start_t
            if fade_len > 0:
                progress = (t - self._fade_start_t) / fade_len
                intensity = _lerp(self._fade_from_cue.intensity, cue.intensity, progress)
                speed = _lerp(self._fade_from_cue.speed, cue.speed, progress)
        elif t >= self._fade_end_t:
            # Fade finished — clear state
            self._fade_from_cue = None

        effect_mode, _ = _MODE_MAP.get(cue.render_mode, (EffectMode.AMBIENT, RenderMode.SOLID))

        # Interpolate between previous and current palette color
        blend_progress = 1.0
        if self._color_blend_duration > 0:
            elapsed = t - self._color_blend_start_t
            blend_progress = min(1.0, elapsed / self._color_blend_duration)

        if blend_progress < 1.0 and len(cue.color_palette) > 1:
            prev_color = cue.color_palette[self._prev_color_index % len(cue.color_palette)]
            curr_color = cue.color_palette[self._color_index % len(cue.color_palette)]
            color = _interpolate_hex(prev_color, curr_color, blend_progress)
        else:
            color = cue.color_palette[self._color_index % len(cue.color_palette)]

        return LightingIntent(
            mode=effect_mode,
            intensity=intensity,
            speed=speed,
            bpm=self._timeline.bpm,
            color=color,
        )


def _lerp(a: float, b: float, t: float) -> float:
    """Linear interpolation from *a* to *b* at fraction *t* ∈ [0, 1]."""
    return a + (b - a) * t


def _parse_hex(h: str) -> tuple[int, int, int]:
    """Parse a hex color string to (r, g, b)."""
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _interpolate_hex(a: str, b: str, t: float) -> str:
    """Linear RGB interpolation between two hex colors."""
    ra, ga, ba = _parse_hex(a)
    rb, gb, bb = _parse_hex(b)
    r = int(ra + (rb - ra) * t)
    g = int(ga + (gb - ga) * t)
    b_val = int(ba + (bb - ba) * t)
    return f"#{r:02x}{g:02x}{b_val:02x}"


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------

def run_show_playback(
    mp3_path: Path,
    show_path: Path,
    multi_adapter,
    *,
    sample_rate: int = 44100,
    audio_device: int | None = None,
    stop_event: threading.Event | None = None,
    debug: bool = False,
) -> dict[str, Any]:
    """Play a show: mp3 audio + lighting timeline → speakers + Govee devices.

    Returns a summary dict when playback finishes or is interrupted.
    """
    from dreamsync.show.player import AudioPlayer

    # 1. Load show timeline
    timeline = ShowTimeline.from_json(show_path)

    # 2. Create audio player
    player = AudioPlayer(mp3_path, sample_rate=sample_rate, device=audio_device)

    # 3. Create runtime
    runtime = ShowPlaybackRuntime(timeline, multi_adapter)

    # 4. Activate devices
    multi_adapter.activate(brightness=100)

    if debug:
        print(f"[show] Playing: {timeline.song_path}")
        print(f"[show] BPM={timeline.bpm:.1f}, {len(timeline.cues)} cues, "
              f"{len(timeline.beat_times)} beats")

    # 5. Start audio playback
    player.play()
    start_wall = time.monotonic()

    # 6. Main loop
    last_cue = None
    try:
        while not player.finished:
            if stop_event is not None and stop_event.is_set():
                break
            t = player.position_seconds
            runtime.tick(t)

            if debug and runtime.current_cue is not last_cue:
                cue = runtime.current_cue
                if cue is not None:
                    print(f"[show] t={t:.1f}s  cue: {cue.render_mode} "
                          f"intensity={cue.intensity:.2f} speed={cue.speed:.2f}")
                last_cue = runtime.current_cue

            time.sleep(0.005)  # ~200 Hz tick
    except KeyboardInterrupt:
        pass

    # 7. Cleanup
    player.stop()
    multi_adapter.deactivate()

    elapsed = time.monotonic() - start_wall
    summary = {
        "duration": round(player.duration, 2),
        "elapsed": round(elapsed, 2),
        **runtime.stats,
    }

    if debug:
        print(f"[show] Done. {summary}")

    return summary
