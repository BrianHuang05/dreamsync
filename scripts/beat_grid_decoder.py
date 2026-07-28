"""Reusable observations, metrical candidates, and stable beat-grid decoding.

The module deliberately has no dependency on verified labels.  It turns feature
frames into inspectable local observations, clusters them without deciding
meter, then decodes a single adaptive quarter-note grid.  It is used by the
offline analysis scripts; it is not part of DreamSync's realtime beat runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.signal import find_peaks


@dataclass(frozen=True)
class EventObservation:
    """One local feature event before candidate clustering."""

    t: float
    family: str
    score: float
    prominence: float = 0.0
    width_seconds: float = 0.0
    metadata: tuple[tuple[str, str | float | int], ...] = ()


@dataclass(frozen=True)
class BeatCandidate:
    """A temporally clustered local observation with family-level evidence."""

    t: float
    local_confidence: float
    mel_score: float
    transient_score: float
    harmonic_score: float
    corner_score: float
    family_count: int
    source_events: tuple[EventObservation, ...]


@dataclass(frozen=True)
class MeterState:
    """One beam-search state for the adaptive metrical grid."""

    beat_time: float
    period_seconds: float
    tempo_slope: float
    cumulative_score: float
    previous_index: int | None
    beat_count: int = 0
    initial_period_seconds: float = 0.0
    octave_transitions: int = 0
    octave_lockout_beats: int = 0
    transition_confidence: float = 0.0


@dataclass(frozen=True)
class DecodedBeat:
    """A grid beat, optionally refined by one nearby local candidate."""

    t: float
    period_seconds: float
    local_confidence: float
    grid_confidence: float
    candidate_snapped: bool
    candidate_index: int | None
    transition_confidence: float = 0.0

    @property
    def bpm(self) -> float:
        return 60.0 / max(self.period_seconds, 1e-9)


@dataclass(frozen=True)
class DecoderConfig:
    """Configuration for the label-free offline beat-grid decoder."""

    minimum_period_seconds: float = 0.30
    maximum_period_seconds: float = 1.00
    period_step_seconds: float = 0.025
    snap_window_seconds: float = 0.10
    start_end_grace_seconds: float = 0.35
    initial_seed_seconds: float = 2.0
    initial_seed_count: int = 8
    beam_width: int = 24
    max_tempo_change_per_beat: float = 0.055
    tempo_adaptation: float = 0.75
    phase_error_penalty: float = 0.45
    tempo_change_penalty: float = 1.35
    missing_observation_penalty: float = 0.28
    subdivision_penalty: float = 0.36
    octave_transition_penalty: float = 0.75
    octave_transition_bonus: float = 1.75
    octave_evidence_span: int = 3
    octave_transition_lockout_beats: int = 16
    octave_min_confidence: float = 0.55
    octave_min_relative_score: float = 0.90
    low_confidence_threshold: float = 0.40

    def validate(self) -> None:
        if not 0.0 < self.minimum_period_seconds <= self.maximum_period_seconds:
            raise ValueError("Decoder period range must be positive and ordered")
        if self.period_step_seconds <= 0 or self.snap_window_seconds <= 0:
            raise ValueError("Decoder period step and snap window must be positive")
        if self.beam_width < 1 or self.initial_seed_count < 1:
            raise ValueError("Decoder beam and seed counts must be at least one")
        if self.octave_evidence_span < 2:
            raise ValueError("Octave evidence must span at least two beats")
        if self.octave_transition_lockout_beats < 0:
            raise ValueError("Octave transition lockout cannot be negative")


@dataclass(frozen=True)
class DecodedTrack:
    beats: tuple[DecodedBeat, ...]
    final_state: MeterState | None
    candidate_classes: tuple[str, ...]
    path_summary: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class _BeamPath:
    state: MeterState
    beats: tuple[DecodedBeat, ...]


def robust_normalize(values: Sequence[float]) -> np.ndarray:
    """Median/MAD normalization that stays finite for quiet feature channels."""
    raw = np.nan_to_num(np.asarray(values, dtype=np.float64), nan=0.0)
    median = float(np.median(raw))
    mad = float(np.median(np.abs(raw - median)))
    scale = 1.4826 * mad
    if scale <= 1e-9:
        # Sparse impulse channels can have a zero MAD because their median is
        # silence.  Standard deviation preserves finite peak-width scoring.
        scale = max(float(np.std(raw)), float(np.mean(np.abs(raw - median))), 1e-9)
    return (raw - median) / scale


def _score_0_to_1(values: Sequence[float]) -> np.ndarray:
    normalized = robust_normalize(values)
    return np.clip(normalized / 6.0, 0.0, 1.0)


def _hop_seconds(times: np.ndarray) -> float:
    return float(np.median(np.diff(times))) if len(times) > 1 else 0.02


def _peak_observations(
    times: Sequence[float],
    values: Sequence[float],
    family: str,
    *,
    minimum_seconds: float,
    minimum_prominence: float = 0.70,
    score_cap: float = 1.0,
    metadata: Mapping[str, str | float | int] | None = None,
) -> list[EventObservation]:
    frame_times = np.asarray(times, dtype=np.float64)
    if len(frame_times) < 3:
        return []
    normalized = robust_normalize(values)
    hop = _hop_seconds(frame_times)
    indices, properties = find_peaks(
        normalized,
        distance=max(1, int(round(minimum_seconds / hop))),
        prominence=minimum_prominence,
        width=(0.0, None),
    )
    widths = np.asarray(properties.get("widths", np.ones(len(indices))), dtype=np.float64) * hop
    prominences = np.asarray(properties.get("prominences", np.zeros(len(indices))), dtype=np.float64)
    fixed_metadata = tuple(sorted((metadata or {}).items()))
    observations: list[EventObservation] = []
    for index, prominence, width in zip(indices, prominences, widths, strict=True):
        # Equal-height plateaus carry less timing information than narrow attacks.
        width_penalty = 1.0 + max(0.0, width - hop) / max(minimum_seconds, hop)
        score = min(score_cap, max(0.0, prominence / 4.0) / width_penalty)
        observations.append(
            EventObservation(
                t=float(frame_times[index]),
                family=family,
                score=float(score),
                prominence=float(prominence),
                width_seconds=float(width),
                metadata=fixed_metadata,
            )
        )
    return observations


def extract_pooled_mel_spikes(
    times: Sequence[float],
    mel_by_band: np.ndarray,
    band_numbers: Sequence[int],
    *,
    minimum_seconds: float = 0.12,
    neighborhood_seconds: float = 0.08,
) -> list[EventObservation]:
    """Find narrow peaks per mel band and retain the best adjacent-band peak."""
    frame_times = np.asarray(times, dtype=np.float64)
    mel = np.asarray(mel_by_band, dtype=np.float64)
    if mel.ndim != 2 or mel.shape[0] != len(frame_times) or mel.shape[1] != len(band_numbers):
        raise ValueError("mel_by_band must be frames by the supplied band_numbers")

    raw: list[EventObservation] = []
    for position, band in enumerate(band_numbers):
        raw.extend(
            _peak_observations(
                frame_times,
                mel[:, position],
                "mel_spike",
                minimum_seconds=minimum_seconds,
                metadata={"winning_band": int(band)},
            )
        )
    if not raw:
        return []
    raw.sort(key=lambda item: item.t)
    clusters: list[list[EventObservation]] = [[raw[0]]]
    for observation in raw[1:]:
        if observation.t - clusters[-1][-1].t <= neighborhood_seconds:
            clusters[-1].append(observation)
        else:
            clusters.append([observation])

    pooled: list[EventObservation] = []
    for cluster in clusters:
        winner = max(cluster, key=lambda item: item.score)
        winning_band = int(dict(winner.metadata)["winning_band"])
        # Only immediately adjacent bands may reinforce a pooled event.
        adjacent = [
            item for item in cluster
            if abs(int(dict(item.metadata)["winning_band"]) - winning_band) <= 1
        ]
        pooled_score = min(1.0, winner.score + 0.12 * (len(adjacent) - 1))
        pooled.append(
            EventObservation(
                t=winner.t,
                family="mel_spike",
                score=float(pooled_score),
                prominence=winner.prominence,
                width_seconds=winner.width_seconds,
                metadata=(
                    ("winning_band", winning_band),
                    ("adjacent_band_count", len(adjacent)),
                ),
            )
        )
    return pooled


def extract_brilliance_edges(
    times: Sequence[float],
    autocorrelation: Sequence[float],
    *,
    lag_seconds: float,
    minimum_seconds: float = 0.12,
) -> list[EventObservation]:
    """Emit differentiated rising/falling boundaries, downweighting expiries."""
    values = np.nan_to_num(np.asarray(autocorrelation, dtype=np.float64), nan=0.0)
    derivative = np.diff(values, prepend=values[0] if len(values) else 0.0)
    rising = _peak_observations(
        times,
        derivative,
        "brilliance_rise",
        minimum_seconds=minimum_seconds,
        metadata={"autocorrelation_lag_seconds": lag_seconds, "boundary": "entry"},
    )
    falling_raw = _peak_observations(
        times,
        -derivative,
        "brilliance_fall",
        minimum_seconds=minimum_seconds,
        metadata={"autocorrelation_lag_seconds": lag_seconds, "boundary": "expiry"},
    )
    falling = [
        EventObservation(
            t=item.t,
            family=item.family,
            score=float(item.score * 0.45),
            prominence=item.prominence,
            width_seconds=item.width_seconds,
            metadata=item.metadata,
        )
        for item in falling_raw
    ]
    return rising + falling


def extract_sub_corners(
    times: Sequence[float],
    values: Sequence[float],
    *,
    minimum_seconds: float = 0.12,
    score_cap: float = 0.45,
) -> list[EventObservation]:
    """Return second-derivative corners as explicitly secondary evidence."""
    raw = np.nan_to_num(np.asarray(values, dtype=np.float64), nan=0.0)
    first = np.diff(raw, prepend=raw[0] if len(raw) else 0.0)
    second = np.diff(first, prepend=first[0] if len(first) else 0.0)
    corner_score = np.abs(second) + 0.35 * np.abs(first)
    return _peak_observations(
        times,
        corner_score,
        "sub_corner",
        minimum_seconds=minimum_seconds,
        score_cap=score_cap,
    )


def _moving_average(values: np.ndarray, width: int) -> np.ndarray:
    if width <= 1:
        return values.copy()
    kernel = np.full(width, 1.0 / width, dtype=np.float64)
    return np.convolve(values, kernel, mode="same")


def extract_harmonic_novelty(
    times: Sequence[float],
    chroma: np.ndarray,
    *,
    spectral_centroid: Sequence[float] | None = None,
    spectral_rolloff: Sequence[float] | None = None,
    short_window_seconds: float = 0.12,
    medium_window_seconds: float = 0.45,
    long_window_seconds: float = 0.80,
    minimum_seconds: float = 0.16,
) -> list[EventObservation]:
    """Detect local tonal change after normalizing each chroma vector.

    Normalization makes the detector invariant to a uniform gain change in all
    chroma bins, which is important because chroma magnitude itself is not a
    tonal novelty signal.
    """
    frame_times = np.asarray(times, dtype=np.float64)
    matrix = np.nan_to_num(np.asarray(chroma, dtype=np.float64), nan=0.0)
    if matrix.ndim != 2 or matrix.shape[0] != len(frame_times):
        raise ValueError("chroma must be frames by chroma-bin matrix")
    if len(frame_times) < 3:
        return []
    normalized = matrix / np.maximum(matrix.sum(axis=1, keepdims=True), 1e-12)
    hop = _hop_seconds(frame_times)
    short = max(1, int(round(short_window_seconds / hop)))
    medium = max(short + 1, int(round(medium_window_seconds / hop)))
    delayed_short = np.vstack([normalized[:short], normalized[:-short]])
    delayed_medium = np.vstack([normalized[:medium], normalized[:-medium]])
    l1_novelty = 0.5 * (
        np.abs(normalized - delayed_short).sum(axis=1)
        + np.abs(normalized - delayed_medium).sum(axis=1)
    )
    cosine_novelty = 1.0 - np.sum(normalized * delayed_short, axis=1)
    entropy = -np.sum(normalized * np.log(normalized + 1e-12), axis=1)
    entropy_change = np.abs(np.diff(entropy, prepend=entropy[0]))
    dominant = np.argmax(normalized, axis=1)
    dominant_change = (dominant != np.r_[dominant[0], dominant[:-1]]).astype(np.float64)
    novelty = (
        0.42 * _score_0_to_1(l1_novelty)
        + 0.28 * _score_0_to_1(cosine_novelty)
        + 0.18 * _score_0_to_1(entropy_change)
        + 0.12 * dominant_change
    )
    long_width = max(1, int(round(long_window_seconds / hop)))
    for descriptor in (spectral_centroid, spectral_rolloff):
        if descriptor is not None:
            smooth = _moving_average(np.asarray(descriptor, dtype=np.float64), long_width)
            novelty += 0.08 * _score_0_to_1(np.abs(np.diff(smooth, prepend=smooth[0])))
    return _peak_observations(
        frame_times,
        novelty,
        "harmonic_novelty",
        minimum_seconds=minimum_seconds,
        minimum_prominence=0.35,
    )


def cluster_observations(
    observations: Iterable[EventObservation],
    *,
    agreement_seconds: float = 0.10,
) -> tuple[BeatCandidate, ...]:
    """Cluster nearby observations while retaining every contributing family."""
    if agreement_seconds <= 0:
        raise ValueError("agreement_seconds must be positive")
    ordered = sorted(observations, key=lambda item: item.t)
    if not ordered:
        return ()
    clusters: list[list[EventObservation]] = [[ordered[0]]]
    for observation in ordered[1:]:
        if observation.t - clusters[-1][-1].t <= agreement_seconds:
            clusters[-1].append(observation)
        else:
            clusters.append([observation])

    candidates: list[BeatCandidate] = []
    for cluster in clusters:
        best_by_family: dict[str, EventObservation] = {}
        for observation in cluster:
            # Rising and falling autocorrelation boundaries form one transient family.
            family = "transient" if observation.family.startswith("brilliance_") else observation.family
            existing = best_by_family.get(family)
            if existing is None or observation.score > existing.score:
                best_by_family[family] = observation
        winning = max(best_by_family.values(), key=lambda item: item.score)
        scores = {family: item.score for family, item in best_by_family.items()}
        family_count = len(scores)
        confidence = sum(scores.values()) + 0.35 * max(0, family_count - 1)
        candidates.append(
            BeatCandidate(
                t=winning.t,
                local_confidence=float(confidence),
                mel_score=float(scores.get("mel_spike", 0.0)),
                transient_score=float(scores.get("transient", 0.0)),
                harmonic_score=float(scores.get("harmonic_novelty", 0.0)),
                corner_score=float(scores.get("sub_corner", 0.0)),
                family_count=family_count,
                source_events=tuple(cluster),
            )
        )
    return tuple(sorted(candidates, key=lambda item: item.t))


def candidates_to_frame(candidates: Sequence[BeatCandidate]) -> pd.DataFrame:
    """Serialize candidates without discarding family/source diagnostics."""
    rows: list[dict[str, object]] = []
    for candidate in candidates:
        rows.append(
            {
                "t": candidate.t,
                "local_confidence": candidate.local_confidence,
                "mel_spike_score": candidate.mel_score,
                "brilliance_edge_score": candidate.transient_score,
                "harmonic_novelty_score": candidate.harmonic_score,
                "sub_corner_score": candidate.corner_score,
                "family_count": candidate.family_count,
                "source_families": ";".join(sorted({item.family for item in candidate.source_events})),
                "source_event_count": len(candidate.source_events),
                "source_events_json": json.dumps(
                    [
                        {
                            "t": item.t,
                            "family": item.family,
                            "score": item.score,
                            "prominence": item.prominence,
                            "width_seconds": item.width_seconds,
                            "metadata": dict(item.metadata),
                        }
                        for item in candidate.source_events
                    ],
                    ensure_ascii=False,
                ),
            }
        )
    return pd.DataFrame(rows)


def _nearest_candidate(
    candidate_times: np.ndarray,
    candidates: Sequence[BeatCandidate],
    target: float,
    window: float,
) -> tuple[int | None, float]:
    left = int(np.searchsorted(candidate_times, target - window, side="left"))
    right = int(np.searchsorted(candidate_times, target + window, side="right"))
    if left >= right:
        return None, 0.0
    positions = range(left, right)
    best = max(
        positions,
        key=lambda index: candidates[index].local_confidence
        - 0.35 * ((candidates[index].t - target) / window) ** 2,
    )
    return best, float(candidates[best].local_confidence)


def _grid_confidence(local_confidence: float, missing_penalty: float) -> float:
    if local_confidence <= 0:
        return 0.0
    return float(local_confidence / (local_confidence + max(missing_penalty, 1e-9)))


def _subdivision_evidence(
    state: MeterState,
    candidates: Sequence[BeatCandidate],
    candidate_times: np.ndarray,
    config: DecoderConfig,
) -> tuple[bool, float]:
    """Test whether intervening events are sustained and as strong as beats."""
    half_period = state.period_seconds / 2.0
    if half_period < config.minimum_period_seconds - 1e-9:
        return False, 0.0
    midpoint_scores: list[float] = []
    full_scores: list[float] = []
    for offset in range(config.octave_evidence_span):
        _, midpoint = _nearest_candidate(
            candidate_times,
            candidates,
            state.beat_time + (offset + 0.5) * state.period_seconds,
            config.snap_window_seconds,
        )
        _, full = _nearest_candidate(
            candidate_times,
            candidates,
            state.beat_time + (offset + 1.0) * state.period_seconds,
            config.snap_window_seconds,
        )
        midpoint_scores.append(midpoint)
        full_scores.append(full)
    midpoint_raw = float(np.median(midpoint_scores))
    full_raw = float(np.median(full_scores))
    # Candidate confidence sums independent feature-family evidence and is
    # therefore not naturally bounded: the real corpus reaches roughly four.
    # Normalize it before comparing it with the configuration threshold or
    # using it as an octave-transition reward, otherwise feature agreement can
    # overwhelm every stability penalty by several multiples.
    midpoint_confidence = _grid_confidence(midpoint_raw, config.missing_observation_penalty)
    full_confidence = _grid_confidence(full_raw, config.missing_observation_penalty)
    threshold = max(
        config.octave_min_confidence,
        full_confidence * config.octave_min_relative_score,
    )
    return midpoint_confidence >= threshold, midpoint_confidence


def _make_transition(
    path: _BeamPath,
    *,
    next_period: float,
    candidates: Sequence[BeatCandidate],
    candidate_times: np.ndarray,
    config: DecoderConfig,
    transition_confidence: float = 0.0,
    octave_transition: bool = False,
    unexplained_subdivision_score: float = 0.0,
) -> _BeamPath:
    previous = path.state
    predicted_time = previous.beat_time + next_period
    index, observation_score = _nearest_candidate(
        candidate_times, candidates, predicted_time, config.snap_window_seconds
    )
    snapped = index is not None
    snapped_time = candidates[index].t if index is not None else predicted_time
    phase_error = abs(snapped_time - predicted_time) / config.snap_window_seconds
    observed_interval = snapped_time - previous.beat_time
    relative_change = observed_interval / max(previous.period_seconds, 1e-9) - 1.0
    bounded_change = float(
        np.clip(
            relative_change,
            -config.max_tempo_change_per_beat,
            config.max_tempo_change_per_beat,
        )
    )
    adapted_period = next_period * (1.0 + config.tempo_adaptation * bounded_change) if snapped else next_period
    adapted_period = float(
        np.clip(adapted_period, config.minimum_period_seconds, config.maximum_period_seconds)
    )
    slope = math.log(adapted_period / max(previous.period_seconds, 1e-9))
    delta_score = observation_score if snapped else -config.missing_observation_penalty
    delta_score -= config.phase_error_penalty * phase_error * phase_error if snapped else 0.0
    delta_score -= config.tempo_change_penalty * abs(math.log(next_period / previous.period_seconds))
    delta_score -= config.subdivision_penalty * unexplained_subdivision_score
    if octave_transition:
        delta_score -= config.octave_transition_penalty
        # This branch requires several intervening observations as strong as
        # full-grid events, so reward its sustained transition evidence enough
        # to offset its one-time discontinuity cost.
        delta_score += config.octave_transition_bonus * transition_confidence
    state = MeterState(
        beat_time=float(snapped_time),
        period_seconds=adapted_period,
        tempo_slope=float(slope),
        cumulative_score=float(previous.cumulative_score + delta_score),
        previous_index=index,
        beat_count=previous.beat_count + 1,
        initial_period_seconds=previous.initial_period_seconds,
        octave_transitions=previous.octave_transitions + int(octave_transition),
        octave_lockout_beats=(
            config.octave_transition_lockout_beats
            if octave_transition
            else max(0, previous.octave_lockout_beats - 1)
        ),
        transition_confidence=transition_confidence,
    )
    beat = DecodedBeat(
        t=float(snapped_time),
        period_seconds=adapted_period,
        local_confidence=observation_score if snapped else 0.0,
        grid_confidence=_grid_confidence(observation_score, config.missing_observation_penalty),
        candidate_snapped=snapped,
        candidate_index=index,
        transition_confidence=transition_confidence,
    )
    return _BeamPath(state=state, beats=path.beats + (beat,))


def _path_rank(path: _BeamPath) -> float:
    # Every complete path spans nearly the same duration.  Mean evidence per
    # grid beat prevents a dense eighth-note path from winning merely by
    # collecting twice as many local candidate rewards.
    return path.state.cumulative_score / max(path.state.beat_count, 1)


def classify_subdivisions(
    candidates: Sequence[BeatCandidate],
    grid_times: Sequence[float],
    periods: Sequence[float] | float,
    *,
    tolerance_seconds: float = 0.08,
) -> tuple[str, ...]:
    """Classify candidate phase relative to the closest variable-period grid."""
    grid = np.asarray(grid_times, dtype=np.float64)
    if np.isscalar(periods):
        period_values = np.full(len(grid), float(periods), dtype=np.float64)
    else:
        period_values = np.asarray(periods, dtype=np.float64)
    if len(grid) != len(period_values):
        raise ValueError("grid_times and periods must have equal length")
    if len(grid) == 0:
        return tuple("off_grid" for _ in candidates)
    classes: list[str] = []
    targets = (("beat", (0.0,)), ("eighth", (0.5,)), ("triplet", (1 / 3, 2 / 3)), ("sixteenth", (0.25, 0.75)))
    for candidate in candidates:
        position = int(np.searchsorted(grid, candidate.t))
        nearby = [index for index in (position - 1, position) if 0 <= index < len(grid)]
        grid_index = min(nearby, key=lambda index: abs(grid[index] - candidate.t))
        period = max(float(period_values[grid_index]), 1e-9)
        phase = (candidate.t - grid[grid_index]) / period
        fractional = phase % 1.0
        matched = "off_grid"
        for label, targets_for_label in targets:
            distance = min(abs(fractional - target) for target in targets_for_label)
            distance = min(distance, 1.0 - distance)
            if distance * period <= tolerance_seconds:
                matched = label
                break
        classes.append(matched)
    return tuple(classes)


def decode_beat_grid(
    candidates: Sequence[BeatCandidate],
    *,
    start_time: float,
    end_time: float,
    config: DecoderConfig = DecoderConfig(),
) -> DecodedTrack:
    """Decode one stable, adaptive beat grid from clustered local candidates."""
    config.validate()
    if end_time <= start_time:
        raise ValueError("end_time must exceed start_time")
    ordered = tuple(sorted(candidates, key=lambda item: item.t))
    if not ordered:
        return DecodedTrack((), None, (), {"reason": "no_candidates"})
    candidate_times = np.asarray([item.t for item in ordered], dtype=np.float64)
    seed_limit = start_time + config.initial_seed_seconds
    seed_positions = np.flatnonzero(candidate_times <= seed_limit)
    if not seed_positions.size:
        seed_positions = np.asarray([0])
    seed_positions = sorted(
        seed_positions.tolist(), key=lambda index: ordered[index].local_confidence, reverse=True
    )[: config.initial_seed_count]
    periods = np.arange(
        config.minimum_period_seconds,
        config.maximum_period_seconds + config.period_step_seconds * 0.5,
        config.period_step_seconds,
    )
    beam: list[_BeamPath] = []
    for seed_index in seed_positions:
        seed = ordered[seed_index]
        for period in periods:
            state = MeterState(
                beat_time=seed.t,
                period_seconds=float(period),
                tempo_slope=0.0,
                cumulative_score=seed.local_confidence,
                previous_index=seed_index,
                beat_count=1,
                initial_period_seconds=float(period),
            )
            beat = DecodedBeat(
                t=seed.t,
                period_seconds=float(period),
                local_confidence=seed.local_confidence,
                grid_confidence=_grid_confidence(seed.local_confidence, config.missing_observation_penalty),
                candidate_snapped=True,
                candidate_index=seed_index,
            )
            beam.append(_BeamPath(state=state, beats=(beat,)))
    beam.sort(key=_path_rank, reverse=True)
    beam = beam[: config.beam_width]

    max_steps = int(math.ceil((end_time - start_time) / config.minimum_period_seconds)) + 4
    completed: list[_BeamPath] = []
    for _ in range(max_steps):
        expanded: list[_BeamPath] = []
        for path in beam:
            state = path.state
            if state.beat_time + state.period_seconds > end_time + 1e-9:
                completed.append(path)
                continue
            midpoint_index, midpoint_score = _nearest_candidate(
                candidate_times,
                ordered,
                state.beat_time + state.period_seconds / 2.0,
                config.snap_window_seconds,
            )
            _ = midpoint_index  # documents that evidence is deliberately not consumed here.
            expanded.append(
                _make_transition(
                    path,
                    next_period=state.period_seconds,
                    candidates=ordered,
                    candidate_times=candidate_times,
                    config=config,
                    unexplained_subdivision_score=max(0.0, midpoint_score - 0.80),
                )
            )
            octave_ok, octave_confidence = _subdivision_evidence(
                state, ordered, candidate_times, config)
            if octave_ok and state.octave_lockout_beats == 0:
                expanded.append(
                    _make_transition(
                        path,
                        next_period=state.period_seconds / 2.0,
                        candidates=ordered,
                        candidate_times=candidate_times,
                        config=config,
                        transition_confidence=octave_confidence,
                        octave_transition=True,
                    )
                )
            # A slower metrical level is considered only if the intermediate
            # grid point is weak, so it cannot skip a persistent beat stream.
            double_period = state.period_seconds * 2.0
            if double_period <= config.maximum_period_seconds and state.octave_lockout_beats == 0:
                _, skipped_grid_score = _nearest_candidate(
                    candidate_times,
                    ordered,
                    state.beat_time + state.period_seconds,
                    config.snap_window_seconds,
                )
                _, next_score = _nearest_candidate(
                    candidate_times,
                    ordered,
                    state.beat_time + double_period,
                    config.snap_window_seconds,
                )
                skipped_grid_confidence = _grid_confidence(
                    skipped_grid_score,
                    config.missing_observation_penalty,
                )
                next_confidence = _grid_confidence(
                    next_score,
                    config.missing_observation_penalty,
                )
                if (
                    skipped_grid_confidence < config.octave_min_confidence * 0.5
                    and next_confidence >= config.octave_min_confidence
                ):
                    expanded.append(
                        _make_transition(
                            path,
                            next_period=double_period,
                            candidates=ordered,
                            candidate_times=candidate_times,
                            config=config,
                            transition_confidence=next_confidence,
                            octave_transition=True,
                        )
                    )
        if not expanded:
            break
        expanded.sort(key=_path_rank, reverse=True)
        beam = expanded[: config.beam_width]
    completed.extend(beam)
    best = max(completed, key=_path_rank)

    # Preserve phase across a short leading analysis edge without inventing a
    # local event.  These beats remain low confidence by construction.
    leading: list[DecodedBeat] = []
    first = best.beats[0]
    previous_time = first.t - first.period_seconds
    while previous_time >= start_time - config.start_end_grace_seconds:
        leading.append(
            DecodedBeat(
                t=float(previous_time),
                period_seconds=first.period_seconds,
                local_confidence=0.0,
                grid_confidence=0.0,
                candidate_snapped=False,
                candidate_index=None,
            )
        )
        previous_time -= first.period_seconds
    beats = tuple(reversed(leading)) + best.beats
    periods_for_classes = [beat.period_seconds for beat in beats]
    classes = classify_subdivisions(
        ordered,
        [beat.t for beat in beats],
        periods_for_classes,
        tolerance_seconds=config.snap_window_seconds,
    )
    summary = {
        "initial_period_seconds": best.state.initial_period_seconds,
        "final_period_seconds": best.state.period_seconds,
        "final_bpm": 60.0 / max(best.state.period_seconds, 1e-9),
        "cumulative_score": best.state.cumulative_score,
        "mean_score_per_beat": _path_rank(best),
        "octave_transitions": best.state.octave_transitions,
        "beat_count": len(beats),
    }
    return DecodedTrack(beats, best.state, classes, summary)
