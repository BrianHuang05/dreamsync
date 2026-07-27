"""Tests for causal harmonic analysis used only by reactive live mode."""

from __future__ import annotations

import numpy as np
import pytest

from dreamsync.dsp.harmonic import (
    LiveBarChordHistory,
    LiveChordHistory,
    LiveHarmonicAnalyzer,
    LiveChordProgressionPredictor,
    LiveHarmonicState,
    chord_tones,
    detected_non_chord_tones,
)
from dreamsync.dsp.meter import LiveMeterState
from dreamsync.live import (
    LiveStructureConfig,
    _harmonic_accent_strength,
    _qualifies_harmonic_accent,
)


def _chord(
    frequencies: tuple[float, float, float],
    *,
    sample_rate: int = 44_100,
    frame_size: int = 4096,
    phase: float = 0.0,
) -> np.ndarray:
    t = (np.arange(frame_size, dtype=np.float32) / sample_rate) + phase
    signal = sum(np.sin(2.0 * np.pi * frequency * t) for frequency in frequencies)
    return (0.12 * signal).astype(np.float32)


def _harmonic_tone(
    midi_note: int,
    *,
    amplitude: float = 1.0,
    sample_rate: int = 44_100,
    frame_size: int = 4096,
    partials: int = 16,
) -> np.ndarray:
    frequency = 440.0 * (2.0 ** ((midi_note - 69) / 12.0))
    t = np.arange(frame_size, dtype=np.float32) / sample_rate
    signal = np.zeros(frame_size, dtype=np.float32)
    for harmonic in range(1, partials + 1):
        if frequency * harmonic > 2000.0:
            break
        signal += (
            amplitude
            / (harmonic ** 0.75)
            * np.sin(
                2.0
                * np.pi
                * frequency
                * harmonic
                * t
            )
        ).astype(np.float32)
    return signal


def test_live_harmonic_analyzer_identifies_stable_major_triad() -> None:
    analyzer = LiveHarmonicAnalyzer()
    c_major = _chord((261.63, 329.63, 392.00))

    state = None
    for index in range(8):
        state = analyzer.update(c_major, t=index * 0.1)

    assert state is not None
    assert state.chord == "C"
    assert state.tonal_confidence > 0.35
    assert state.chord_confidence > 0.35
    assert not state.harmonic_change
    assert abs(sum(state.chroma) - 1.0) < 1e-4


def test_live_harmonic_analyzer_infers_first_complete_window_immediately() -> None:
    analyzer = LiveHarmonicAnalyzer()

    state = analyzer.update(
        _chord((261.63, 329.63, 392.00)),
        t=4096 / 44_100,
    )

    assert state.chord == "C"
    assert state.chord_confidence > 0.35
    assert state.tonal_confidence > 0.35


def test_live_harmonic_analyzer_emits_one_persistent_chord_change() -> None:
    analyzer = LiveHarmonicAnalyzer(minimum_change_interval=0.2)
    c_major = _chord((261.63, 329.63, 392.00))
    f_major = _chord((174.61, 220.00, 261.63))

    for index in range(8):
        analyzer.update(c_major, t=index * 0.1)
    states = [
        analyzer.update(f_major, t=0.8 + (index * 0.1))
        for index in range(10)
    ]

    changes = [state for state in states if state.harmonic_change]
    assert len(changes) == 1
    assert changes[0].chord == "F"
    assert changes[0].novelty >= changes[0].novelty_threshold


def test_streaming_chord_change_is_visible_within_100_ms_without_reacquiring() -> None:
    sample_rate = 44_100
    frame_size = 4096
    hop_size = 512
    transition_sample = sample_rate // 2

    def render(
        frequencies: tuple[float, float, float],
        sample_count: int,
        *,
        offset: int,
    ) -> np.ndarray:
        t = (
            np.arange(sample_count, dtype=np.float32) + offset
        ) / sample_rate
        signal = sum(
            np.sin(2.0 * np.pi * frequency * t)
            for frequency in frequencies
        )
        return (0.12 * signal).astype(np.float32)

    audio = np.concatenate(
        (
            render(
                (261.63, 329.63, 392.00),
                transition_sample,
                offset=0,
            ),
            render(
                (174.61, 220.00, 261.63),
                transition_sample,
                offset=transition_sample,
            ),
        )
    )
    analyzer = LiveHarmonicAnalyzer(
        minimum_change_interval=0.2,
    )
    states = []
    for start in range(
        0,
        audio.size - frame_size + 1,
        hop_size,
    ):
        states.append(
            analyzer.update(
                audio[start : start + frame_size],
                t=(start + frame_size) / sample_rate,
            )
        )

    after_transition = [
        state for state in states if state.t >= 0.5
    ]
    first_f = next(
        state for state in after_transition if state.chord == "F"
    )
    committed_f = next(
        state
        for state in after_transition
        if state.chord == "F" and state.harmonic_change
    )

    assert (first_f.t - 0.5) * 1000.0 <= 100.0
    assert (committed_f.t - 0.5) * 1000.0 <= 110.0
    assert all(state.chord for state in after_transition)


@pytest.mark.parametrize("midi_note", (48, 52, 55, 57))
def test_single_harmonic_rich_note_does_not_complete_a_false_chord(
    midi_note: int,
) -> None:
    analyzer = LiveHarmonicAnalyzer(debug_spectrum=True)

    state = analyzer.update(
        (0.08 * _harmonic_tone(midi_note)).astype(np.float32),
        t=4096 / 44_100,
    )

    assert state.chord == ""
    assert state.chord_confidence < 0.30
    pitch_names = (
        "C",
        "C#",
        "D",
        "D#",
        "E",
        "F",
        "F#",
        "G",
        "G#",
        "A",
        "A#",
        "B",
    )
    played_pitch = pitch_names[midi_note % 12]
    non_fundamental_evidence = [
        value
        for pitch, value in analyzer.debug_spectrum
        if pitch != played_pitch
    ]
    assert max(non_fundamental_evidence) < 0.08


def test_all_major_minor_roots_are_inversion_invariant_with_harmonics() -> None:
    pitch_names = (
        "C",
        "C#",
        "D",
        "D#",
        "E",
        "F",
        "F#",
        "G",
        "G#",
        "A",
        "A#",
        "B",
    )
    failures = []
    for root in range(12):
        for quality, third in (("", 4), ("m", 3)):
            root_midi = 48 + root
            voicings = (
                (
                    root_midi,
                    root_midi + third,
                    root_midi + 7,
                ),
                (
                    root_midi + third,
                    root_midi + 7,
                    root_midi + 12,
                ),
                (
                    root_midi + 7,
                    root_midi + 12,
                    root_midi + 12 + third,
                ),
            )
            expected = f"{pitch_names[root]}{quality}"
            for inversion, notes in enumerate(voicings):
                frame = sum(
                    (
                        _harmonic_tone(
                            note,
                            amplitude=(
                                2.5 if index == 0 else 1.0
                            ),
                        )
                        for index, note in enumerate(notes)
                    ),
                    np.zeros(4096, dtype=np.float32),
                )
                state = LiveHarmonicAnalyzer().update(
                    (0.08 * frame).astype(np.float32),
                    t=4096 / 44_100,
                )
                if state.chord != expected:
                    failures.append(
                        (
                            expected,
                            inversion,
                            state.chord,
                        )
                    )

    assert failures == []


def test_ambiguous_chord_display_hold_lasts_350_ms() -> None:
    analyzer = LiveHarmonicAnalyzer()
    c_major = _chord((261.63, 329.63, 392.00))
    noise = np.random.default_rng(17).normal(
        0.0,
        0.12,
        4096,
    ).astype(np.float32)

    assert analyzer.update(c_major, t=0.0).chord == "C"
    assert analyzer.update(noise, t=0.30).chord == "C"
    assert analyzer.update(noise, t=0.36).chord == ""


@pytest.mark.parametrize(
    ("target_frequencies", "expected_chord"),
    (
        # F-A-D is the first inversion of D minor, not an F chord.
        ((174.61, 220.00, 293.66), "Dm"),
        ((130.81, 164.81, 196.00), "C"),
        ((185.00, 233.08, 277.18), "F#"),
    ),
)
def test_related_and_chromatic_transitions_leave_f(
    target_frequencies: tuple[float, float, float],
    expected_chord: str,
) -> None:
    analyzer = LiveHarmonicAnalyzer(minimum_change_interval=0.2)
    f_major = _chord((174.61, 220.00, 261.63))
    target = _chord(target_frequencies)

    for index in range(8):
        analyzer.update(f_major, t=index * 0.1)
    states = [
        analyzer.update(target, t=0.8 + (index * 0.1))
        for index in range(6)
    ]

    changes = [state for state in states if state.harmonic_change]
    assert len(changes) == 1
    assert changes[0].chord == expected_chord
    assert states[-1].chord == expected_chord


def test_live_harmonic_analyzer_abstains_on_silence() -> None:
    analyzer = LiveHarmonicAnalyzer()
    state = analyzer.update(np.zeros(4096, dtype=np.float32), t=1.0)

    assert state.chord == ""
    assert state.tonal_confidence == 0.0
    assert not state.harmonic_change


def test_chord_tones_reports_assumed_major_and_minor_triads() -> None:
    assert chord_tones("F") == ("F", "A", "C")
    assert chord_tones("Dm") == ("D", "F", "A")
    assert chord_tones("F#") == ("F#", "A#", "C#")
    assert chord_tones("") == ()


def test_detected_non_chord_tones_separates_melody_from_triad() -> None:
    chroma = (
        1.0,
        0.0,
        0.35,
        0.0,
        0.8,
        0.0,
        0.0,
        0.75,
        0.0,
        0.0,
        0.0,
        0.0,
    )

    assert detected_non_chord_tones(chroma, "C") == ("D",)


def test_debug_spectrum_is_opt_in_octave_folded_and_note_ordered() -> None:
    c_major = _chord((261.63, 329.63, 392.00))
    normal = LiveHarmonicAnalyzer()
    debug = LiveHarmonicAnalyzer(
        debug_spectrum=True,
        debug_spectrum_bins=72,
    )

    normal.update(c_major, t=0.0)
    debug.update(c_major, t=0.0)

    assert normal.debug_spectrum == ()
    assert len(debug.debug_spectrum) == 12
    notes = [note for note, _value in debug.debug_spectrum]
    amplitudes = [value for _frequency, value in debug.debug_spectrum]
    assert notes == [
        "C",
        "C#",
        "D",
        "D#",
        "E",
        "F",
        "F#",
        "G",
        "G#",
        "A",
        "A#",
        "B",
    ]
    assert max(amplitudes) == pytest.approx(1.0)
    evidence = dict(debug.debug_spectrum)
    assert evidence["C"] > 0.75
    assert evidence["E"] > 0.75
    assert evidence["G"] > 0.75


def test_debug_spectrum_condenses_c_across_octaves_into_one_note() -> None:
    analyzer = LiveHarmonicAnalyzer(debug_spectrum=True)
    octave_c = _chord((130.81, 261.63, 523.25))

    state = analyzer.update(octave_c, t=0.0)

    evidence = dict(analyzer.debug_spectrum)
    assert len(evidence) == 12
    assert max(evidence, key=evidence.get) == "C"
    assert evidence["C"] == pytest.approx(1.0)
    # One pitch class across octaves is tonal evidence, not a complete chord.
    assert state.chord == ""


def test_harmonic_analyzer_rejects_low_and_high_out_of_range_tones() -> None:
    analyzer = LiveHarmonicAnalyzer(debug_spectrum=True)
    out_of_range = _chord((45.0, 3500.0, 6000.0))

    state = analyzer.update(out_of_range, t=0.0)

    assert state.chord == ""
    assert state.tonal_confidence == 0.0
    assert all(value == 0.0 for _note, value in analyzer.debug_spectrum)


def test_harmonic_analyzer_rejects_broadband_noise_and_spectral_smear() -> None:
    analyzer = LiveHarmonicAnalyzer(debug_spectrum=True)
    rng = np.random.default_rng(19)
    white_noise = rng.normal(0.0, 0.12, 4096).astype(np.float32)
    t = np.arange(4096, dtype=np.float32) / 44_100
    smear = np.zeros(4096, dtype=np.float32)
    for frequency in np.linspace(180.0, 420.0, 64):
        smear += np.sin(
            (2.0 * np.pi * frequency * t)
            + rng.uniform(0.0, 2.0 * np.pi)
        ).astype(np.float32)
    smear *= 0.02

    noise_state = analyzer.update(white_noise, t=0.0)
    smear_state = analyzer.update(smear, t=0.1)

    assert noise_state.chord == ""
    assert noise_state.tonal_confidence == 0.0
    assert smear_state.chord == ""
    assert smear_state.tonal_confidence == 0.0
    assert all(value == 0.0 for _note, value in analyzer.debug_spectrum)


def test_narrow_triad_peaks_survive_moderate_broadband_noise() -> None:
    analyzer = LiveHarmonicAnalyzer(debug_spectrum=True)
    rng = np.random.default_rng(23)
    noisy_c_major = _chord((261.63, 329.63, 392.00))
    noisy_c_major += rng.normal(0.0, 0.03, 4096).astype(np.float32)

    state = None
    for index in range(4):
        state = analyzer.update(noisy_c_major, t=index * 0.1)

    assert state is not None
    assert state.chord == "C"
    evidence = dict(analyzer.debug_spectrum)
    assert evidence["C"] > 0.70
    assert evidence["E"] > 0.70
    assert evidence["G"] > 0.70


def test_live_structure_config_validates_fft_controls() -> None:
    assert LiveStructureConfig().harmonic_hop_multiplier == 1
    with pytest.raises(ValueError, match="power of two"):
        LiveStructureConfig(harmonic_frame_size=3000)
    with pytest.raises(ValueError, match="positive"):
        LiveStructureConfig(harmonic_hop_multiplier=0)
    with pytest.raises(ValueError, match="between 0 and 1"):
        LiveStructureConfig(sensitivity=1.1)


def test_phase_e_accent_requires_high_confidence_change() -> None:
    weak = LiveHarmonicState(
        harmonic_change=True,
        tonal_confidence=0.2,
        chord_confidence=0.8,
    )
    strong = LiveHarmonicState(
        harmonic_change=True,
        tonal_confidence=0.8,
        chord_confidence=0.8,
    )

    assert not _qualifies_harmonic_accent(weak)
    assert _qualifies_harmonic_accent(strong)


def test_phase_e_accent_is_small_and_reversible() -> None:
    assert _harmonic_accent_strength(10.0, None) == 0.0
    assert _harmonic_accent_strength(10.0, 10.0) == pytest.approx(0.12)
    assert 0.0 < _harmonic_accent_strength(10.12, 10.0) < 0.12
    assert _harmonic_accent_strength(10.25, 10.0) == 0.0


def test_live_chord_history_keeps_current_and_previous_three() -> None:
    history = LiveChordHistory()
    chords = ("C", "Am", "F", "G", "Em")
    for index, chord in enumerate(chords):
        history.observe(
            LiveHarmonicState(
                t=float(index),
                tonal_confidence=0.8,
                chord=chord,
                chord_confidence=0.8,
                harmonic_change=index > 0,
            )
        )

    assert history.current == "Em"
    assert history.chords == ("Am", "F", "G", "Em")
    assert history.changes[-1] == (4.0, "Em")


def test_bar_chord_history_locks_completed_bars_and_keeps_duplicates() -> None:
    history = LiveBarChordHistory()

    def meter(*, downbeat: bool, phase: int) -> LiveMeterState:
        return LiveMeterState(
            beat=True,
            downbeat=downbeat,
            bar_phase=phase,
            phase_confidence=0.9,
            meter_confident=True,
        )

    history.observe_beat(
        meter(downbeat=True, phase=0),
        chord="C",
    )
    history.observe_beat(
        meter(downbeat=True, phase=0),
        chord="C",
    )
    history.observe_beat(
        meter(downbeat=True, phase=0),
        chord="F",
    )
    assert history.previous_three == ("C", "C")

    # The first confirmed change after the downbeat corrects only the current
    # bar. Previously completed slots stay locked.
    history.observe_beat(
        meter(downbeat=False, phase=1),
        chord="G",
        chord_change=True,
    )
    assert history.previous_three == ("C", "C")
    assert history.current == "G"

    history.observe_beat(
        meter(downbeat=True, phase=0),
        chord="G",
    )
    assert history.previous_three == ("C", "C", "G")


def test_live_chord_history_ignores_unconfirmed_flicker_and_prunes_markers() -> None:
    history = LiveChordHistory()
    assert not history.observe(
        LiveHarmonicState(
            t=0.0,
            tonal_confidence=0.8,
            chord="C",
            chord_confidence=0.8,
        )
    )
    assert not history.observe(
        LiveHarmonicState(
            t=1.0,
            tonal_confidence=0.8,
            chord="C#",
            chord_confidence=0.8,
            harmonic_change=False,
        )
    )
    assert history.observe(
        LiveHarmonicState(
            t=2.0,
            tonal_confidence=0.8,
            chord="F",
            chord_confidence=0.8,
            harmonic_change=True,
        )
    )

    history.prune_changes_before(2.1)
    assert history.chords == ("C", "F")
    assert history.changes == ()


def test_chord_change_snaps_back_to_recent_confident_downbeat() -> None:
    history = LiveChordHistory()
    history.observe(
        LiveHarmonicState(
            t=0.0,
            tonal_confidence=0.8,
            chord="C",
            chord_confidence=0.8,
        )
    )
    meter = LiveMeterState(
        t=2.0,
        beat=True,
        downbeat=True,
        bar_phase=0,
        phase_confidence=0.8,
        meter_confident=True,
    )

    changed = history.observe(
        LiveHarmonicState(
            t=2.16,
            tonal_confidence=0.8,
            chord="F",
            chord_confidence=0.8,
            harmonic_change=True,
        ),
        meter=meter,
        bpm=120.0,
    )

    assert changed
    assert history.changes[-1] == (2.0, "F")
    assert history.last_change is not None
    assert history.last_change.anchor == "downbeat"
    assert history.last_change.observed_t == 2.16


def test_off_grid_chord_change_waits_for_next_beat() -> None:
    history = LiveChordHistory()
    history.observe(
        LiveHarmonicState(
            t=0.0,
            tonal_confidence=0.8,
            chord="C",
            chord_confidence=0.8,
        )
    )
    old_beat = LiveMeterState(t=1.0, beat=True)
    assert not history.observe(
        LiveHarmonicState(
            t=1.7,
            tonal_confidence=0.8,
            chord="G",
            chord_confidence=0.8,
            harmonic_change=True,
        ),
        meter=old_beat,
        bpm=120.0,
    )

    assert history.current == "C"
    assert history.observe_beat(
        LiveMeterState(t=2.0, beat=True),
        bpm=120.0,
    )
    assert history.current == "G"
    assert history.changes[-1] == (2.0, "G")
    assert history.last_change is not None
    assert history.last_change.anchor == "beat"


def test_off_grid_candidate_is_cancelled_if_harmony_reverts_before_beat() -> None:
    history = LiveChordHistory()
    history.observe(
        LiveHarmonicState(
            t=0.0,
            tonal_confidence=0.8,
            chord="C",
            chord_confidence=0.8,
        )
    )
    assert not history.observe(
        LiveHarmonicState(
            t=1.7,
            tonal_confidence=0.8,
            chord="F",
            chord_confidence=0.8,
            harmonic_change=True,
        ),
        meter=LiveMeterState(t=1.0, beat=True),
        bpm=120.0,
    )
    history.observe(
        LiveHarmonicState(
            t=1.9,
            tonal_confidence=0.8,
            chord="C",
            chord_confidence=0.8,
        ),
        meter=LiveMeterState(t=1.0, beat=True),
        bpm=120.0,
    )

    assert not history.observe_beat(
        LiveMeterState(t=2.0, beat=True),
        bpm=120.0,
    )
    assert history.current == "C"
    assert history.changes == ()


def test_progression_predictor_repeats_section_pattern_and_flags_mismatch() -> None:
    predictor = LiveChordProgressionPredictor()
    for t, chord in enumerate(("C", "F", "G", "C", "F")):
        predictor.observe(
            t=float(t),
            chord=chord,
            beat_period=0.5,
        )

    prediction = predictor.prediction
    assert prediction is not None
    assert prediction.chord == "G"
    assert prediction.t == 5.0
    assert prediction.source == "section_pattern"

    predictor.observe(t=5.0, chord="Am", beat_period=0.5)
    mismatch = predictor.last_mismatch
    assert mismatch is not None
    assert mismatch.tone_mismatch
    assert not mismatch.timing_mismatch
    assert mismatch.predicted_chord == "G"
    assert mismatch.actual_chord == "Am"


def test_progression_predictor_reuses_matching_prior_section() -> None:
    predictor = LiveChordProgressionPredictor()
    predictor.observe(t=0.0, chord="C")
    predictor.observe(t=1.0, chord="F")
    predictor.observe(t=2.0, chord="G")
    predictor.start_section(t=3.0)
    predictor.observe(t=3.0, chord="C")
    predictor.observe(t=4.0, chord="F")

    prediction = predictor.prediction
    assert prediction is not None
    assert prediction.chord == "G"
    assert prediction.t == 5.0
    assert prediction.source == "repeated_section"
