"""Simple frequency-only lighting for Raw Visualizer live mode."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence

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


def _parse_hex_color(value: str) -> tuple[int, int, int]:
    text = str(value).strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"invalid palette color: {value}")
    return tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))


def sample_palette_colors(
    colors: Sequence[str],
    point_count: int,
) -> tuple[str, ...]:
    """Sample a saved color palette across Raw's frequency control points."""

    source = tuple(str(color) for color in colors if str(color).strip())
    count = max(1, min(5, int(point_count)))
    if not source:
        source = RAW_VISUALIZER_COLORS
    parsed = tuple(_parse_hex_color(color) for color in source)
    if count == 1:
        sampled = (parsed[0],)
    elif len(parsed) == 1:
        sampled = parsed * count
    else:
        sampled_values: list[tuple[int, int, int]] = []
        for index in range(count):
            position = index * (len(parsed) - 1) / (count - 1)
            left = int(math.floor(position))
            right = min(len(parsed) - 1, left + 1)
            fraction = position - left
            sampled_values.append(
                tuple(
                    int(
                        round(
                            parsed[left][channel] * (1.0 - fraction)
                            + parsed[right][channel] * fraction
                        )
                    )
                    for channel in range(3)
                )
            )
        sampled = tuple(sampled_values)
    return tuple(
        f"#{red:02x}{green:02x}{blue:02x}"
        for red, green, blue in sampled
    )


class RawPaletteRuntime:
    """Tempo-free hot-swap and timed palette pool for Raw Visualizer."""

    def __init__(self, base_colors: Sequence[str]) -> None:
        self._base_colors = tuple(base_colors) or RAW_VISUALIZER_COLORS
        self._pool: tuple[tuple[str, tuple[str, ...]], ...] = ()
        self._selected_index: int | None = None
        self._auto_chain = False
        self._interval_seconds = 16.0
        self._next_switch_t: float | None = None
        self._revision = -1

    def update(
        self,
        t: float,
        control: Mapping[str, object] | None = None,
    ) -> None:
        control = control or {}
        revision = int(control.get("revision", 0) or 0)
        if revision != self._revision:
            normalized_pool: list[tuple[str, tuple[str, ...]]] = []
            raw_pool = control.get("pool", ())
            if isinstance(raw_pool, (list, tuple)):
                for entry in raw_pool:
                    if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                        continue
                    name = str(entry[0]).strip()
                    colors = tuple(
                        str(color)
                        for color in (
                            entry[1]
                            if isinstance(entry[1], (list, tuple))
                            else ()
                        )
                        if str(color).strip()
                    )
                    if name and colors:
                        normalized_pool.append((name, colors))
            current_name = self.active_name
            self._pool = tuple(normalized_pool)
            selected_name = str(
                control.get("selected_name", "") or ""
            ).strip()
            names = tuple(name for name, _colors in self._pool)
            self._auto_chain = bool(control.get("auto_chain", False))
            preferred = selected_name or current_name
            if (
                bool(control.get("use_custom", False))
                and not self._auto_chain
            ):
                self._selected_index = None
            elif preferred in names:
                self._selected_index = names.index(preferred)
            else:
                self._selected_index = (
                    0 if self._auto_chain and names else None
                )
            self._interval_seconds = max(
                0.25,
                float(control.get("interval_seconds", 16.0) or 16.0),
            )
            self._next_switch_t = (
                float(t) + self._interval_seconds
                if self._auto_chain and len(self._pool) > 1
                else None
            )
            self._revision = revision

        if (
            self._auto_chain
            and self._pool
            and self._next_switch_t is not None
            and float(t) >= self._next_switch_t
        ):
            elapsed = float(t) - self._next_switch_t
            steps = 1 + int(elapsed // self._interval_seconds)
            self._selected_index = (
                (self._selected_index or 0) + steps
            ) % len(self._pool)
            self._next_switch_t += steps * self._interval_seconds

    @property
    def active_name(self) -> str:
        if not self._pool or self._selected_index is None:
            return "custom_frequency_gradient"
        return self._pool[self._selected_index][0]

    @property
    def queue(self) -> tuple[str, ...]:
        if not self._pool:
            return ()
        if self._selected_index is None:
            return tuple(name for name, _colors in self._pool)
        return tuple(
            self._pool[
                (self._selected_index + offset) % len(self._pool)
            ][0]
            for offset in range(len(self._pool))
        )

    def colors(self, point_count: int) -> tuple[str, ...]:
        source = (
            self._pool[self._selected_index][1]
            if self._pool and self._selected_index is not None
            else self._base_colors
        )
        return sample_palette_colors(source, point_count)

    def seconds_until_next(self, t: float) -> float | None:
        if self._next_switch_t is None:
            return None
        return max(0.0, self._next_switch_t - float(t))


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
