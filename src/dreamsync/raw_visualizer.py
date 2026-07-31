"""Simple frequency-only lighting for Raw Visualizer live mode."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

from dreamsync.director import EffectMode, LightingIntent


RAW_VISUALIZER_COLORS: tuple[str, str, str] = (
    "#ff0000",  # bass
    "#00ff00",  # mids
    "#8f00ff",  # highs / violet
)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True)
class RawVisualizerFrame:
    bass: float
    mids: float
    highs: float

    @property
    def levels(self) -> tuple[float, float, float]:
        return (self.bass, self.mids, self.highs)

    @property
    def intensity(self) -> float:
        return max(self.levels)

    def render_params(self) -> dict[str, object]:
        return {
            "_render_mode": "solid",
            "spatial_preset": "raw_visualizer_center",
            "raw_visualizer_levels": self.levels,
            "raw_visualizer_colors": RAW_VISUALIZER_COLORS,
            "raw_visualizer_bands": ("bass", "mids", "highs"),
        }


class RawFrequencyVisualizer:
    """Map RMS loudness and FFT band ratios directly to center-out fill levels.

    This intentionally contains no onset, beat, meter, or tempo state.  RMS
    controls how much of the strips is lit while the spectral distribution
    decides how strongly bass, mids, and highs contribute.
    """

    _REFERENCE_SHARES = (0.34, 0.38, 0.28)

    def __init__(
        self,
        *,
        silence_rms: float = 0.004,
        full_scale_rms: float = 0.18,
        attack: float = 0.62,
        release: float = 0.22,
    ) -> None:
        self.silence_rms = max(0.0, float(silence_rms))
        self.full_scale_rms = max(
            self.silence_rms + 1e-6,
            float(full_scale_rms),
        )
        self.attack = _clamp01(attack)
        self.release = _clamp01(release)
        self._levels = [0.0, 0.0, 0.0]

    def reset(self) -> None:
        self._levels[:] = (0.0, 0.0, 0.0)

    def update(
        self,
        *,
        rms: float,
        band_ratios: Sequence[float],
    ) -> RawVisualizerFrame:
        padded = tuple(float(value) for value in band_ratios) + (0.0,) * 7
        shares = (
            sum(max(0.0, value) for value in padded[0:3]),
            sum(max(0.0, value) for value in padded[3:5]),
            sum(max(0.0, value) for value in padded[5:7]),
        )
        loudness = _clamp01(
            (float(rms) - self.silence_rms)
            / (self.full_scale_rms - self.silence_rms)
        )
        # A mild gamma gives useful strip travel at ordinary listening levels
        # without allowing quiet room noise to light the center.
        loudness = math.pow(loudness, 0.72)

        targets = [
            loudness
            * _clamp01(
                math.sqrt(share / reference)
                if reference > 0.0
                else 0.0
            )
            for share, reference in zip(shares, self._REFERENCE_SHARES)
        ]
        for index, target in enumerate(targets):
            coefficient = (
                self.attack if target >= self._levels[index] else self.release
            )
            self._levels[index] += coefficient * (
                target - self._levels[index]
            )

        return RawVisualizerFrame(*(_clamp01(value) for value in self._levels))

    @staticmethod
    def intent(frame: RawVisualizerFrame) -> LightingIntent:
        return LightingIntent(
            mode=EffectMode.AMBIENT,
            intensity=frame.intensity,
            speed=0.0,
            bpm=0.0,
            color=RAW_VISUALIZER_COLORS[0],
        )
