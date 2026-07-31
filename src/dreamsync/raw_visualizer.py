"""Simple frequency-only lighting for Raw Visualizer live mode."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

from dreamsync.director import EffectMode, LightingIntent


DEFAULT_RAW_VISUALIZER_GRADIENT: tuple[tuple[float, str], ...] = (
    (80.0, "#ff0000"),
    (1000.0, "#00ff00"),
    (8000.0, "#8f00ff"),
)
RAW_VISUALIZER_COLORS = tuple(
    color for _frequency, color in DEFAULT_RAW_VISUALIZER_GRADIENT
)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True)
class RawVisualizerFrame:
    levels: tuple[float, ...]
    colors: tuple[str, ...] = RAW_VISUALIZER_COLORS
    frequencies: tuple[float, ...] = tuple(
        frequency
        for frequency, _color in DEFAULT_RAW_VISUALIZER_GRADIENT
    )

    @property
    def intensity(self) -> float:
        return max(self.levels)

    @property
    def bass(self) -> float:
        return self.levels[0]

    @property
    def mids(self) -> float:
        return self.levels[len(self.levels) // 2]

    @property
    def highs(self) -> float:
        return self.levels[-1]

    def render_params(self) -> dict[str, object]:
        return {
            "_render_mode": "solid",
            "spatial_preset": "raw_visualizer_center",
            "raw_visualizer_levels": self.levels,
            "raw_visualizer_colors": self.colors,
            "raw_visualizer_frequencies": self.frequencies,
        }


class RawFrequencyVisualizer:
    """Map RMS loudness and FFT band ratios directly to center-out fill levels.

    This intentionally contains no onset, beat, meter, or tempo state.  RMS
    controls how much of the strips is lit while the spectral distribution
    decides how strongly each configured frequency/color point contributes.
    """

    def __init__(
        self,
        *,
        silence_rms: float = 0.004,
        full_scale_rms: float = 0.18,
        attack: float = 0.62,
        release: float = 0.22,
        gradient_points: Sequence[tuple[float, str]] = (
            DEFAULT_RAW_VISUALIZER_GRADIENT
        ),
    ) -> None:
        self.silence_rms = max(0.0, float(silence_rms))
        self.full_scale_rms = max(
            self.silence_rms + 1e-6,
            float(full_scale_rms),
        )
        self.attack = _clamp01(attack)
        self.release = _clamp01(release)
        normalized_points = sorted(
            (
                max(20.0, min(20_000.0, float(frequency))),
                str(color),
            )
            for frequency, color in gradient_points
        )[:5]
        self.gradient_points = tuple(
            normalized_points
            if normalized_points
            else DEFAULT_RAW_VISUALIZER_GRADIENT
        )
        self._levels = [0.0] * len(self.gradient_points)

    def reset(self) -> None:
        self._levels[:] = (0.0,) * len(self.gradient_points)

    def update(
        self,
        *,
        rms: float,
        band_ratios: Sequence[float],
        magnitude: Sequence[float] = (),
        frequencies: Sequence[float] = (),
    ) -> RawVisualizerFrame:
        if len(magnitude) and len(frequencies):
            energy = [0.0] * len(self.gradient_points)
            point_frequencies = tuple(
                point_frequency
                for point_frequency, _color in self.gradient_points
            )
            for frequency, value in zip(frequencies, magnitude):
                if frequency < 20.0 or frequency > 20_000.0:
                    continue
                nearest = min(
                    range(len(point_frequencies)),
                    key=lambda index: abs(
                        math.log(max(1.0, frequency))
                        - math.log(point_frequencies[index])
                    ),
                )
                energy[nearest] += max(0.0, float(value))
            total_energy = sum(energy)
            shares = tuple(
                value / total_energy if total_energy > 0.0 else 0.0
                for value in energy
            )
        else:
            padded = (
                tuple(float(value) for value in band_ratios)
                + (0.0,) * 7
            )
            legacy_shares = (
                sum(max(0.0, value) for value in padded[0:3]),
                sum(max(0.0, value) for value in padded[3:5]),
                sum(max(0.0, value) for value in padded[5:7]),
            )
            shares = tuple(
                legacy_shares[
                    round(
                        index * 2 / max(1, len(self.gradient_points) - 1)
                    )
                ]
                for index in range(len(self.gradient_points))
            )
        loudness = _clamp01(
            (float(rms) - self.silence_rms)
            / (self.full_scale_rms - self.silence_rms)
        )
        # A mild gamma gives useful strip travel at ordinary listening levels
        # without allowing quiet room noise to light the center.
        loudness = math.pow(loudness, 0.72)

        reference_share = 1.0 / len(self.gradient_points)
        targets = [
            loudness
            * _clamp01(
                math.sqrt(share / reference_share)
                if reference_share > 0.0
                else 0.0
            )
            for share in shares
        ]
        for index, target in enumerate(targets):
            coefficient = (
                self.attack if target >= self._levels[index] else self.release
            )
            self._levels[index] += coefficient * (
                target - self._levels[index]
            )

        levels = tuple(_clamp01(value) for value in self._levels)
        colors = tuple(color for _frequency, color in self.gradient_points)
        point_frequencies = tuple(
            frequency for frequency, _color in self.gradient_points
        )
        return RawVisualizerFrame(
            levels=levels,
            colors=colors,
            frequencies=point_frequencies,
        )

    @staticmethod
    def intent(frame: RawVisualizerFrame) -> LightingIntent:
        return LightingIntent(
            mode=EffectMode.AMBIENT,
            intensity=frame.intensity,
            speed=0.0,
            bpm=0.0,
            color=frame.colors[0],
        )
