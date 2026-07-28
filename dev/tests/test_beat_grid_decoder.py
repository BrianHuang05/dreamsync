"""Synthetic coverage for the offline stable beat-grid decoder."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

import numpy as np

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from beat_grid_decoder import (  # noqa: E402
    BeatCandidate,
    DecoderConfig,
    EventObservation,
    classify_subdivisions,
    decode_beat_grid,
    extract_brilliance_edges,
    extract_harmonic_novelty,
    extract_pooled_mel_spikes,
)
import beat_grid_decoder as grid_decoder  # noqa: E402


def _candidate(time: float, score: float = 1.0) -> BeatCandidate:
    event = EventObservation(t=time, family="mel_spike", score=score)
    return BeatCandidate(
        t=time,
        local_confidence=score,
        mel_score=score,
        transient_score=0.0,
        harmonic_score=0.0,
        corner_score=0.0,
        family_count=1,
        source_events=(event,),
    )


def _config() -> DecoderConfig:
    return DecoderConfig(
        minimum_period_seconds=0.30,
        maximum_period_seconds=0.80,
        period_step_seconds=0.025,
        snap_window_seconds=0.055,
        beam_width=32,
        octave_evidence_span=3,
    )


# Strong candidates from 15--45 s of the development-track overlay that
# exposed repeated 70/150 BPM octave flips.  Keeping the fixture compact and
# label-free makes it a decoder regression test rather than a tuned truth set.
IF_ONLY_FOR_TONIGHT_TRACE = (
    (15.94644, 3.660546), (16.82644, 3.531091), (17.68644, 2.518770),
    (18.12644, 3.184221), (18.52644, 3.792215), (19.22644, 2.437235),
    (19.44644, 2.999842), (19.88644, 2.738492), (20.28644, 3.669293),
    (20.98644, 3.627778), (21.84644, 3.254317), (22.04644, 3.636355),
    (22.48644, 2.738768), (22.92644, 2.498921), (23.36644, 3.180985),
    (23.72644, 3.160981), (24.18644, 3.112060), (24.48644, 3.652456),
    (25.44644, 2.819351), (25.96644, 2.650457), (26.22644, 2.999719),
    (26.64644, 2.029322), (26.86644, 2.922418), (27.44644, 2.094322),
    (27.60644, 2.442612), (27.80644, 2.050277), (28.20644, 2.456686),
    (28.42644, 2.389953), (28.92644, 3.580040), (29.18644, 2.397909),
    (29.98644, 2.217123), (30.16644, 2.720911), (30.40644, 2.990690),
    (30.80644, 3.922728), (31.30644, 3.249722), (31.68644, 4.344974),
    (32.44644, 4.026770), (32.84644, 2.225287), (33.22644, 3.120835),
    (33.44644, 3.821663), (33.88644, 3.482588), (34.28644, 3.015602),
    (35.14644, 2.343540), (35.38644, 2.506697), (35.60644, 2.891908),
    (36.02644, 3.550509), (36.26644, 3.370457), (36.86644, 4.131500),
    (37.12644, 2.429191), (37.34644, 2.318346), (37.54644, 3.229440),
    (38.10644, 3.179691), (38.48644, 2.482736), (38.68644, 3.957385),
    (39.12644, 3.366698), (39.46644, 3.564322), (39.94644, 3.283287),
    (40.22644, 3.608994), (40.46644, 2.459502), (40.68644, 2.552401),
    (40.88644, 2.718908), (41.14644, 3.271324), (41.28644, 3.109388),
    (41.54644, 3.380472), (41.98644, 2.118797), (42.22644, 2.081450),
    (42.42644, 2.938081), (42.64644, 3.060456), (43.04644, 3.245263),
    (43.36644, 2.480225), (43.72644, 3.323998), (44.12644, 3.544813),
    (44.80644, 3.053961),
)


def _trace_candidates(trace: tuple[tuple[float, float], ...]) -> tuple[BeatCandidate, ...]:
    return tuple(_candidate(time, score) for time, score in trace)


class ObservationExtractionTests(unittest.TestCase):
    def test_adjacent_mel_spikes_pool_to_one_event(self) -> None:
        times = np.arange(0.0, 2.0, 0.02)
        mel = np.zeros((len(times), 3))
        center = int(np.argmin(np.abs(times - 1.0)))
        mel[center, 0] = 12.0
        mel[center + 1, 1] = 10.0
        observations = extract_pooled_mel_spikes(times, mel, [26, 27, 28])
        nearby = [item for item in observations if abs(item.t - 1.0) < 0.06]
        self.assertEqual(len(nearby), 1)
        self.assertGreaterEqual(int(dict(nearby[0].metadata)["adjacent_band_count"]), 2)

    def test_broad_mel_plateau_scores_lower_than_narrow_impulse(self) -> None:
        times = np.arange(0.0, 3.0, 0.02)
        mel = np.zeros((len(times), 1))
        mel[int(0.8 / 0.02), 0] = 10.0
        mel[int(1.7 / 0.02) : int(1.9 / 0.02), 0] = 10.0
        observations = extract_pooled_mel_spikes(times, mel, [26])
        narrow = min(observations, key=lambda item: abs(item.t - 0.8))
        broad = min(observations, key=lambda item: abs(item.t - 1.8))
        self.assertGreater(narrow.score, broad.score)

    def test_expiry_boundary_is_downweighted(self) -> None:
        times = np.arange(0.0, 2.0, 0.02)
        pulse = np.zeros(len(times))
        pulse[(times >= 0.7) & (times <= 1.1)] = 8.0
        observations = extract_brilliance_edges(times, pulse, lag_seconds=0.4)
        rising = [item.score for item in observations if item.family == "brilliance_rise"]
        falling = [item.score for item in observations if item.family == "brilliance_fall"]
        self.assertTrue(rising and falling)
        self.assertGreater(max(rising), max(falling))

    def test_harmonic_novelty_is_invariant_to_uniform_chroma_gain(self) -> None:
        times = np.arange(0.0, 3.0, 0.02)
        chroma = np.tile(np.arange(1.0, 13.0), (len(times), 1))
        chroma[times >= 1.2] = np.roll(chroma[times >= 1.2], 3, axis=1)
        first = extract_harmonic_novelty(times, chroma)
        gained = extract_harmonic_novelty(times, chroma * 7.0)
        self.assertEqual([round(item.t, 4) for item in first], [round(item.t, 4) for item in gained])
        self.assertTrue(np.allclose([item.score for item in first], [item.score for item in gained]))


class MetricalGridTests(unittest.TestCase):
    def test_if_only_for_tonight_trace_does_not_oscillate_metrical_level(self) -> None:
        decoded = decode_beat_grid(
            _trace_candidates(IF_ONLY_FOR_TONIGHT_TRACE),
            start_time=15.0,
            end_time=45.2,
            config=_config(),
        )
        self.assertLessEqual(decoded.path_summary["octave_transitions"], 1)
        self.assertLessEqual(
            max(beat.transition_confidence for beat in decoded.beats),
            1.0,
        )

    def test_octave_evidence_is_normalized_before_transition_scoring(self) -> None:
        state = grid_decoder.MeterState(
            beat_time=0.0,
            period_seconds=0.8,
            tempo_slope=0.0,
            cumulative_score=4.0,
            previous_index=0,
            beat_count=1,
            initial_period_seconds=0.8,
        )
        candidates = tuple(
            _candidate(time, 4.0)
            for time in (0.0, 0.4, 0.8, 1.2, 1.6, 2.0, 2.4)
        )
        accepted, confidence = grid_decoder._subdivision_evidence(
            state,
            candidates,
            np.asarray([candidate.t for candidate in candidates]),
            _config(),
        )
        self.assertTrue(accepted)
        self.assertGreater(confidence, 0.9)
        self.assertLessEqual(confidence, 1.0)

    def test_quarter_eighth_triplet_and_sixteenth_candidates_classify(self) -> None:
        grid = np.arange(0.0, 3.0, 0.5)
        candidates = (_candidate(1.0), _candidate(1.125), _candidate(1.25), _candidate(1.0 + 1.0 / 6.0))
        classes = classify_subdivisions(candidates, grid, 0.5, tolerance_seconds=0.025)
        self.assertEqual(classes, ("beat", "sixteenth", "eighth", "triplet"))

    def test_interleaved_eighths_emit_quarter_grid_only(self) -> None:
        quarters = np.arange(0.0, 8.01, 0.5)
        eighths = np.arange(0.25, 8.0, 0.5)
        decoded = decode_beat_grid(
            tuple(_candidate(float(time), 1.0) for time in quarters)
            + tuple(_candidate(float(time), 0.42) for time in eighths),
            start_time=0.0,
            end_time=8.0,
            config=_config(),
        )
        predicted = np.asarray([beat.t for beat in decoded.beats])
        self.assertGreater(len(predicted), 13)
        self.assertLess(len(predicted), 20)
        self.assertLess(np.median(np.abs(np.diff(predicted) - 0.5)), 0.07)

    def test_sustained_double_tempo_change_is_accepted(self) -> None:
        before = np.arange(0.0, 3.01, 0.6)
        after = np.arange(3.3, 7.81, 0.3)
        decoded = decode_beat_grid(
            tuple(_candidate(float(time), 1.0) for time in np.r_[before, after]),
            start_time=0.0,
            end_time=7.8,
            config=_config(),
        )
        predicted = np.asarray([beat.t for beat in decoded.beats])
        late_intervals = np.diff(predicted[predicted >= 4.0])
        self.assertLess(np.median(late_intervals), 0.40)
        self.assertGreaterEqual(decoded.path_summary["octave_transitions"], 1)

    def test_isolated_double_time_candidates_do_not_flip_tempo(self) -> None:
        quarters = np.arange(0.0, 7.81, 0.6)
        isolated = np.asarray([2.1, 5.1])
        decoded = decode_beat_grid(
            tuple(_candidate(float(time), 1.0) for time in quarters)
            + tuple(_candidate(float(time), 0.70) for time in isolated),
            start_time=0.0,
            end_time=7.8,
            config=_config(),
        )
        predicted = np.asarray([beat.t for beat in decoded.beats])
        self.assertGreater(np.median(np.diff(predicted)), 0.50)
        self.assertEqual(decoded.path_summary["octave_transitions"], 0)

    def test_ritard_grows_period_without_large_phase_error(self) -> None:
        periods = np.linspace(0.45, 0.68, 15)
        beats = np.r_[0.0, np.cumsum(periods)]
        decoded = decode_beat_grid(
            tuple(_candidate(float(time), 1.0) for time in beats),
            start_time=0.0,
            end_time=float(beats[-1]),
            config=_config(),
        )
        predicted = np.asarray([beat.t for beat in decoded.beats])
        decoded_periods = np.asarray([beat.period_seconds for beat in decoded.beats])
        self.assertGreater(np.median(decoded_periods[-4:]), np.median(decoded_periods[:4]))
        nearest = np.min(np.abs(predicted[:, None] - beats[None, :]), axis=1)
        self.assertLess(np.max(nearest), 0.11)

    def test_missing_onset_retains_a_low_confidence_grid_beat(self) -> None:
        beats = np.arange(0.0, 5.01, 0.5)
        candidates = [_candidate(float(time), 1.0) for time in beats if not np.isclose(time, 2.0)]
        decoded = decode_beat_grid(candidates, start_time=0.0, end_time=5.0, config=_config())
        missing_grid = min(decoded.beats, key=lambda item: abs(item.t - 2.0))
        self.assertLess(abs(missing_grid.t - 2.0), 0.10)
        self.assertFalse(missing_grid.candidate_snapped)


if __name__ == "__main__":
    unittest.main()
