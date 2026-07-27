"""Causal harmonic features for the reactive live path.

This module intentionally contains analysis only.  It has no dependency on
audio playback, the offline compiler, or lighting output.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Literal

import numpy as np


_PITCH_NAMES: tuple[str, ...] = (
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

_HARMONIC_MIN_HZ = 80.0
_HARMONIC_MAX_HZ = 2000.0
_BROADBAND_FLATNESS_LIMIT = 0.68
_PEAK_SHOULDER_RATIO = 0.90
_PEAK_HALF_HEIGHT_MAX_BINS = 4


def chord_tones(chord: str) -> tuple[str, ...]:
    """Return root, third, and fifth pitch classes for a major/minor label."""

    label = str(chord or "").strip()
    for root_name in sorted(_PITCH_NAMES, key=len, reverse=True):
        if not label.startswith(root_name):
            continue
        suffix = label[len(root_name) :]
        if suffix not in {"", "m"}:
            return ()
        root = _PITCH_NAMES.index(root_name)
        third = 3 if suffix == "m" else 4
        return (
            _PITCH_NAMES[root],
            _PITCH_NAMES[(root + third) % 12],
            _PITCH_NAMES[(root + 7) % 12],
        )
    return ()


def detected_non_chord_tones(
    chroma: tuple[float, ...],
    chord: str,
    *,
    relative_threshold: float = 0.18,
) -> tuple[str, ...]:
    """Return audible pitch classes outside the assumed triad."""

    values = tuple(max(0.0, float(value)) for value in chroma)
    if len(values) != 12:
        return ()
    peak = max(values, default=0.0)
    if peak <= 1e-9:
        return ()
    assumed = set(chord_tones(chord))
    threshold = peak * max(0.05, min(0.95, float(relative_threshold)))
    return tuple(
        pitch
        for pitch, value in zip(_PITCH_NAMES, values)
        if pitch not in assumed and value >= threshold
    )


@dataclass(frozen=True)
class LiveHarmonicState:
    """Latest causal harmonic observation."""

    t: float = 0.0
    chroma: tuple[float, ...] = (0.0,) * 12
    tonal_confidence: float = 0.0
    chord: str = ""
    chord_confidence: float = 0.0
    novelty: float = 0.0
    novelty_threshold: float = 0.18
    harmonic_change: bool = False


@dataclass(frozen=True)
class LiveChordPrediction:
    """Next chord and time inferred from a repeating live progression."""

    chord: str
    t: float
    confidence: float
    pattern_length: int
    source: Literal["section_pattern", "repeated_section"]
    section_index: int


@dataclass(frozen=True)
class LiveChordPredictionMismatch:
    """A prediction error evaluated when the next actual chord arrives."""

    predicted_chord: str
    predicted_t: float
    actual_chord: str
    actual_t: float
    timing_error: float
    tone_mismatch: bool
    timing_mismatch: bool


@dataclass(frozen=True)
class LiveChordChange:
    """A committed chord change on the causal live beat grid."""

    t: float
    chord: str
    observed_t: float
    anchor: Literal["initial", "downbeat", "beat", "timeout"]


@dataclass(frozen=True)
class _ProgressionEvent:
    t: float
    chord: str


class LiveChordProgressionPredictor:
    """Learn repeating progressions within and across live sections."""

    def __init__(
        self,
        *,
        max_sections: int = 12,
        max_events_per_section: int = 64,
    ) -> None:
        self.max_sections = max(2, int(max_sections))
        self.max_events_per_section = max(8, int(max_events_per_section))
        self._sections: deque[list[_ProgressionEvent]] = deque(
            [list()],
            maxlen=self.max_sections,
        )
        self._section_index = 0
        self._prediction: LiveChordPrediction | None = None
        self._last_mismatch: LiveChordPredictionMismatch | None = None

    @property
    def prediction(self) -> LiveChordPrediction | None:
        return self._prediction

    @property
    def last_mismatch(self) -> LiveChordPredictionMismatch | None:
        return self._last_mismatch

    def reset(self) -> None:
        self._sections = deque([list()], maxlen=self.max_sections)
        self._section_index = 0
        self._prediction = None
        self._last_mismatch = None

    def start_section(self, *, t: float) -> None:
        """Start a new section, carrying a just-confirmed boundary chord."""

        carry: _ProgressionEvent | None = None
        current = self._sections[-1]
        if current and abs(current[-1].t - float(t)) <= 0.75:
            carry = current[-1]
        self._sections.append([carry] if carry is not None else [])
        self._section_index += 1
        self._prediction = None

    def observe(
        self,
        *,
        t: float,
        chord: str,
        beat_period: float | None = None,
    ) -> None:
        actual_t = float(t)
        actual_chord = str(chord)
        prediction = self._prediction
        self._last_mismatch = None
        if prediction is not None:
            timing_error = actual_t - prediction.t
            tolerance = max(
                0.18,
                min(
                    0.60,
                    0.30
                    * (
                        float(beat_period)
                        if beat_period is not None and beat_period > 0.0
                        else self._typical_interval()
                    ),
                ),
            )
            tone_mismatch = actual_chord != prediction.chord
            timing_mismatch = abs(timing_error) > tolerance
            if tone_mismatch or timing_mismatch:
                self._last_mismatch = LiveChordPredictionMismatch(
                    predicted_chord=prediction.chord,
                    predicted_t=prediction.t,
                    actual_chord=actual_chord,
                    actual_t=actual_t,
                    timing_error=round(timing_error, 6),
                    tone_mismatch=tone_mismatch,
                    timing_mismatch=timing_mismatch,
                )

        current = self._sections[-1]
        if not current or current[-1].chord != actual_chord:
            current.append(
                _ProgressionEvent(
                    t=actual_t,
                    chord=actual_chord,
                )
            )
            if len(current) > self.max_events_per_section:
                del current[: len(current) - self.max_events_per_section]
        self._prediction = self._predict_next()

    def _predict_next(self) -> LiveChordPrediction | None:
        current = self._sections[-1]
        count = len(current)
        if count < 2:
            return None

        # Prefer a period supported by at least two consecutive matches.
        for period in range(2, min(8, count - 1) + 1):
            comparable = min(period, count - period)
            if comparable < 2:
                continue
            start = count - comparable
            if not all(
                current[index].chord == current[index - period].chord
                for index in range(start, count)
            ):
                continue
            source_index = count - period
            predicted_chord = current[source_index].chord
            interval = self._interval_for_period_position(
                current,
                source_index,
            )
            return LiveChordPrediction(
                chord=predicted_chord,
                t=round(current[-1].t + interval, 6),
                confidence=round(
                    min(0.95, 0.55 + (0.08 * comparable)),
                    6,
                ),
                pattern_length=period,
                source="section_pattern",
                section_index=self._section_index,
            )

        # A returning verse/chorus can borrow its continuation from an older
        # section after two matching opening chords.
        prefix = tuple(event.chord for event in current)
        for previous in reversed(tuple(self._sections)[:-1]):
            if (
                len(prefix) >= 2
                and len(previous) > len(prefix)
                and tuple(
                    event.chord for event in previous[: len(prefix)]
                )
                == prefix
            ):
                predicted = previous[len(prefix)]
                interval = max(
                    0.25,
                    min(
                        8.0,
                        predicted.t - previous[len(prefix) - 1].t,
                    ),
                )
                return LiveChordPrediction(
                    chord=predicted.chord,
                    t=round(current[-1].t + interval, 6),
                    confidence=round(
                        min(0.90, 0.58 + (0.06 * len(prefix))),
                        6,
                    ),
                    pattern_length=len(previous),
                    source="repeated_section",
                    section_index=self._section_index,
                )
        return None

    def _interval_for_period_position(
        self,
        events: list[_ProgressionEvent],
        source_index: int,
    ) -> float:
        if source_index > 0:
            interval = events[source_index].t - events[source_index - 1].t
        else:
            interval = self._typical_interval()
        return max(0.25, min(8.0, float(interval)))

    def _typical_interval(self) -> float:
        intervals = [
            later.t - earlier.t
            for section in self._sections
            for earlier, later in zip(section, section[1:])
            if 0.1 <= later.t - earlier.t <= 12.0
        ]
        if not intervals:
            return 1.0
        ordered = sorted(intervals)
        return float(ordered[len(ordered) // 2])


class LiveChordHistory:
    """Bounded history of confidently established live chords."""

    def __init__(
        self,
        *,
        max_chords: int = 4,
        max_changes: int = 64,
        minimum_tonal_confidence: float = 0.35,
        minimum_chord_confidence: float = 0.30,
        snap_after_beat_seconds: float = 0.35,
        pending_timeout_seconds: float = 0.85,
    ) -> None:
        if max_chords < 1 or max_changes < 1:
            raise ValueError("chord and change capacities must be positive")
        self._chords: deque[str] = deque(maxlen=int(max_chords))
        self._changes: deque[tuple[float, str]] = deque(maxlen=int(max_changes))
        self.minimum_tonal_confidence = float(minimum_tonal_confidence)
        self.minimum_chord_confidence = float(minimum_chord_confidence)
        self.snap_after_beat_seconds = float(snap_after_beat_seconds)
        self.pending_timeout_seconds = float(pending_timeout_seconds)
        self._pending: LiveHarmonicState | None = None
        self._last_change: LiveChordChange | None = None
        self._last_committed_state: LiveHarmonicState | None = None
        self._predictor = LiveChordProgressionPredictor()

    @property
    def current(self) -> str:
        return self._chords[-1] if self._chords else ""

    @property
    def chords(self) -> tuple[str, ...]:
        """Return up to four chords in chronological order."""

        return tuple(self._chords)

    @property
    def changes(self) -> tuple[tuple[float, str], ...]:
        return tuple(self._changes)

    @property
    def last_change(self) -> LiveChordChange | None:
        return self._last_change

    @property
    def last_committed_state(self) -> LiveHarmonicState | None:
        return self._last_committed_state

    @property
    def prediction(self) -> LiveChordPrediction | None:
        return self._predictor.prediction

    @property
    def last_prediction_mismatch(
        self,
    ) -> LiveChordPredictionMismatch | None:
        return self._predictor.last_mismatch

    def reset(self) -> None:
        self._chords.clear()
        self._changes.clear()
        self._pending = None
        self._last_change = None
        self._last_committed_state = None
        self._predictor.reset()

    def start_section(self, *, t: float) -> None:
        self._predictor.start_section(t=float(t))

    def observe_beat(
        self,
        meter,
        *,
        bpm: float = 0.0,
    ) -> bool:
        """Commit a pending off-grid change on the next detected beat."""

        if self._pending is None or not bool(getattr(meter, "beat", False)):
            return False
        anchor = (
            "downbeat"
            if bool(getattr(meter, "downbeat", False))
            and bool(getattr(meter, "meter_confident", False))
            else "beat"
        )
        pending = self._pending
        self._pending = None
        return self._commit(
            pending,
            t=float(getattr(meter, "t", pending.t)),
            anchor=anchor,
            bpm=bpm,
        )

    def observe(
        self,
        state: LiveHarmonicState,
        *,
        meter=None,
        bpm: float = 0.0,
    ) -> bool:
        """Record a confirmed chord and return True for an actual change."""

        chord = str(state.chord or "").strip()
        if (
            not chord
            or state.tonal_confidence < self.minimum_tonal_confidence
            or state.chord_confidence < self.minimum_chord_confidence
        ):
            return False
        if (
            self._pending is not None
            and chord != str(self._pending.chord or "").strip()
        ):
            # The tonal state reverted or moved again before the grid anchor.
            # Do not publish a stale off-beat candidate on the next beat.
            self._pending = None
        if not self._chords:
            self._chords.append(chord)
            self._predictor.observe(
                t=float(state.t),
                chord=chord,
                beat_period=self._beat_period(bpm),
            )
            if state.harmonic_change:
                self._last_committed_state = state
                self._last_change = LiveChordChange(
                    t=float(state.t),
                    chord=chord,
                    observed_t=float(state.t),
                    anchor="initial",
                )
                return True
            return False
        if chord == self._chords[-1] or not state.harmonic_change:
            return self._commit_timeout_if_due(state, bpm=bpm)

        if meter is None:
            return self._commit(
                state,
                t=float(state.t),
                anchor="timeout",
                bpm=bpm,
            )

        meter_t = float(getattr(meter, "t", 0.0) or 0.0)
        age = float(state.t) - meter_t
        beat_period = self._beat_period(bpm)
        snap_window = min(
            self.snap_after_beat_seconds,
            max(0.12, 0.45 * beat_period)
            if beat_period is not None
            else self.snap_after_beat_seconds,
        )
        if (
            bool(getattr(meter, "beat", False))
            and 0.0 <= age <= snap_window
        ):
            anchor = (
                "downbeat"
                if bool(getattr(meter, "downbeat", False))
                and bool(getattr(meter, "meter_confident", False))
                else "beat"
            )
            return self._commit(
                state,
                t=meter_t,
                anchor=anchor,
                bpm=bpm,
            )

        self._pending = state
        return False

    def _commit_timeout_if_due(
        self,
        state: LiveHarmonicState,
        *,
        bpm: float,
    ) -> bool:
        pending = self._pending
        if (
            pending is None
            or float(state.t) - float(pending.t)
            < self.pending_timeout_seconds
        ):
            return False
        self._pending = None
        return self._commit(
            pending,
            t=float(state.t),
            anchor="timeout",
            bpm=bpm,
        )

    def _commit(
        self,
        state: LiveHarmonicState,
        *,
        t: float,
        anchor: Literal["downbeat", "beat", "timeout"],
        bpm: float,
    ) -> bool:
        chord = str(state.chord or "").strip()
        if not chord or (self._chords and chord == self._chords[-1]):
            return False
        self._chords.append(chord)
        self._changes.append((float(t), chord))
        self._last_change = LiveChordChange(
            t=float(t),
            chord=chord,
            observed_t=float(state.t),
            anchor=anchor,
        )
        self._last_committed_state = state
        self._predictor.observe(
            t=float(t),
            chord=chord,
            beat_period=self._beat_period(bpm),
        )
        return True

    def prune_changes_before(self, cutoff: float) -> None:
        while self._changes and self._changes[0][0] < float(cutoff):
            self._changes.popleft()

    @staticmethod
    def _beat_period(bpm: float) -> float | None:
        value = float(bpm)
        return 60.0 / value if value > 0.0 else None


class LiveBarChordHistory:
    """Downbeat-locked chord slots for completed live bars.

    Unlike change history, this intentionally preserves duplicate chords.
    The current bar may be corrected once when its first confirmed chord
    arrives after the downbeat; completed bars never change afterward.
    """

    def __init__(self, *, max_bars: int = 128) -> None:
        if max_bars < 4:
            raise ValueError("max_bars must be at least four")
        self._bars: deque[str] = deque(maxlen=int(max_bars))
        self._current_provisional = False

    @property
    def current(self) -> str:
        return self._bars[-1] if self._bars else ""

    @property
    def completed(self) -> tuple[str, ...]:
        return tuple(self._bars)[:-1]

    @property
    def previous_three(self) -> tuple[str, ...]:
        return self.completed[-3:]

    def reset(self) -> None:
        self._bars.clear()
        self._current_provisional = False

    def observe_beat(
        self,
        meter,
        *,
        chord: str,
        chord_change: bool = False,
    ) -> bool:
        """Update only at beats; return True when a bar slot changes."""

        if (
            not bool(getattr(meter, "beat", False))
            or not bool(getattr(meter, "meter_confident", False))
        ):
            return False
        normalized = str(chord or "").strip()
        if bool(getattr(meter, "downbeat", False)):
            self._bars.append(normalized)
            self._current_provisional = not bool(chord_change)
            return True
        if (
            self._bars
            and self._current_provisional
            and normalized
            and normalized != self._bars[-1]
        ):
            self._bars[-1] = normalized
            self._current_provisional = False
            return True
        if self._bars and self._current_provisional and chord_change:
            self._current_provisional = False
        return False


class LiveHarmonicAnalyzer:
    """Extract smoothed chroma, chord class, and harmonic change events.

    The analyzer consumes trailing, overlapping PCM frames.  All state is
    causal and bounded, making it suitable for the live worker thread.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 44100,
        frame_size: int = 4096,
        smoothing_frames: int = 3,
        novelty_history_frames: int = 96,
        minimum_change_interval: float = 0.45,
        silence_rms: float = 1e-4,
        sensitivity: float = 0.5,
        chord_display_hold_seconds: float = 0.35,
        debug_spectrum: bool = False,
        debug_spectrum_bins: int = 72,
    ) -> None:
        if sample_rate <= 0 or frame_size <= 0:
            raise ValueError("sample_rate and frame_size must be > 0")
        if smoothing_frames < 1:
            raise ValueError("smoothing_frames must be >= 1")
        if novelty_history_frames < 8:
            raise ValueError("novelty_history_frames must be >= 8")
        if not 0.0 <= sensitivity <= 1.0:
            raise ValueError("sensitivity must be between 0 and 1")
        if chord_display_hold_seconds < 0.0:
            raise ValueError(
                "chord_display_hold_seconds must be non-negative"
            )
        if not 12 <= debug_spectrum_bins <= 256:
            raise ValueError("debug_spectrum_bins must be between 12 and 256")

        self.sample_rate = int(sample_rate)
        self.frame_size = int(frame_size)
        self.minimum_change_interval = float(minimum_change_interval)
        self.silence_rms = float(silence_rms)
        self.sensitivity = float(sensitivity)
        self.chord_display_hold_seconds = float(
            chord_display_hold_seconds
        )
        self.debug_spectrum_enabled = bool(debug_spectrum)
        self.debug_spectrum_bins = int(debug_spectrum_bins)
        self._window = np.hanning(self.frame_size).astype(np.float32)
        # Zero padding doubles frequency sampling without adding capture
        # latency. This is important in the low register, where adjacent
        # chord tones can otherwise land only three FFT bins apart and look
        # like one broad feature.
        self._fft_size = self.frame_size * 2
        self._frequencies = np.fft.rfftfreq(
            self._fft_size,
            d=1.0 / float(self.sample_rate),
        )
        self._harmonic_mask = (
            (self._frequencies >= _HARMONIC_MIN_HZ)
            & (
                self._frequencies
                <= min(_HARMONIC_MAX_HZ, self.sample_rate / 2.0)
            )
        )
        self._projection = self._build_chroma_projection()
        self._templates, self._template_names = self._build_chord_templates()
        self._smooth: deque[np.ndarray] = deque(maxlen=int(smoothing_frames))
        self._novelty_history: deque[float] = deque(
            maxlen=int(novelty_history_frames)
        )
        self._baseline: np.ndarray | None = None
        self._stable_chord = ""
        self._candidate_chord = ""
        self._candidate_frames = 0
        self._last_change_t = -1e9
        self._last_display_chord = ""
        self._last_display_chord_t = -1e9
        self._state = LiveHarmonicState()
        self._debug_spectrum: tuple[tuple[str, float], ...] = ()

    @property
    def state(self) -> LiveHarmonicState:
        return self._state

    @property
    def debug_spectrum(self) -> tuple[tuple[str, float], ...]:
        """Current octave-folded, noise-rejected note evidence for the GUI."""

        return self._debug_spectrum

    def reset(self) -> None:
        self._smooth.clear()
        self._novelty_history.clear()
        self._baseline = None
        self._stable_chord = ""
        self._candidate_chord = ""
        self._candidate_frames = 0
        self._last_change_t = -1e9
        self._last_display_chord = ""
        self._last_display_chord_t = -1e9
        self._state = LiveHarmonicState()
        self._debug_spectrum = ()

    def update(self, frame: np.ndarray, *, t: float) -> LiveHarmonicState:
        """Analyze one mono PCM frame."""

        samples = np.asarray(frame, dtype=np.float32).reshape(-1)
        if samples.shape[0] != self.frame_size:
            raise ValueError(
                f"frame must contain {self.frame_size} samples; "
                f"got {samples.shape[0]}"
            )
        now = float(t)
        rms = float(np.sqrt(np.mean(samples * samples)))
        if not math.isfinite(rms) or rms < self.silence_rms:
            self._candidate_chord = ""
            self._candidate_frames = 0
            self._last_display_chord = ""
            self._last_display_chord_t = -1e9
            self._state = LiveHarmonicState(t=now)
            self._debug_spectrum = ()
            return self._state

        magnitude = np.abs(
            np.fft.rfft(
                samples * self._window,
                n=self._fft_size,
            )
        ).astype(
            np.float32,
            copy=False,
        )
        tonal_magnitude = self._isolate_tonal_peaks(magnitude)
        # A fractional power retains substantially more pitch/triad contrast
        # than log compression while still limiting live-room resonances.
        # Log compression flattened related triads so aggressively that
        # F-A-C -> F-A-D could remain classified as F.
        compressed = np.power(tonal_magnitude, 0.70)
        chroma = self._projection @ compressed
        # Any residual broadband component is approximately common to all
        # pitch classes. Remove that floor before normalization so a loud
        # smear cannot look like twelve weak notes.
        pitch_floor = float(np.percentile(chroma, 40.0))
        if pitch_floor > 0.0:
            chroma = np.maximum(
                chroma - (0.85 * pitch_floor),
                0.0,
            )
        total = float(np.sum(chroma))
        if total <= 1e-9:
            self._candidate_chord = ""
            self._candidate_frames = 0
            self._state = LiveHarmonicState(
                t=now,
                chord=self._held_display_chord(now),
            )
            if self.debug_spectrum_enabled:
                self._debug_spectrum = tuple(
                    (pitch, 0.0) for pitch in _PITCH_NAMES
                )
            return self._state
        chroma = (chroma / total).astype(np.float32, copy=False)
        self._smooth.append(chroma)
        smoothed = np.mean(np.stack(tuple(self._smooth)), axis=0)
        smoothed /= max(1e-9, float(np.sum(smoothed)))
        if self.debug_spectrum_enabled:
            self._debug_spectrum = self._summarize_debug_notes(smoothed)

        chord, chord_confidence, template_similarity = self._classify(smoothed)
        entropy = -float(
            np.sum(smoothed * np.log(np.maximum(smoothed, 1e-12)))
        )
        concentration = 1.0 - (entropy / math.log(12.0))
        tonal_confidence = max(
            0.0,
            min(1.0, (0.55 * template_similarity) + (0.45 * concentration)),
        )
        reported_chord = chord
        if chord:
            self._last_display_chord = chord
            self._last_display_chord_t = now
        else:
            reported_chord = self._held_display_chord(now)

        if self._baseline is None:
            novelty = 0.0
            self._baseline = smoothed.copy()
            self._stable_chord = chord
        else:
            novelty = 0.5 * float(np.sum(np.abs(smoothed - self._baseline)))

        threshold = self._novelty_threshold()
        harmonic_change = False
        if chord and chord != self._stable_chord:
            if chord == self._candidate_chord:
                self._candidate_frames += 1
            else:
                self._candidate_chord = chord
                self._candidate_frames = 1
            full_novelty_change = bool(
                self._candidate_frames >= 2 and novelty >= threshold
            )
            # Related chords often retain two of three tones. Once the best
            # complete triad has persisted, allow a lower vector-distance
            # threshold instead of requiring the same novelty as a chromatic
            # shift such as F -> F#.
            persistent_triad_change = bool(
                self._candidate_frames >= 3
                and novelty >= max(0.055, threshold * 0.60)
            )
            harmonic_change = bool(
                (full_novelty_change or persistent_triad_change)
                and chord_confidence >= 0.30
                and tonal_confidence >= 0.30
                and now - self._last_change_t >= self.minimum_change_interval
            )
        else:
            self._candidate_chord = ""
            self._candidate_frames = 0

        if harmonic_change:
            self._stable_chord = chord
            self._last_change_t = now
            self._candidate_chord = ""
            self._candidate_frames = 0
            self._baseline = smoothed.copy()
        elif self._baseline is not None:
            # Slow trailing reference.  Hold it nearly fixed while a new chord
            # candidate is accumulating so the change cannot self-cancel.
            alpha = 0.008 if self._candidate_frames else 0.025
            self._baseline = (
                ((1.0 - alpha) * self._baseline) + (alpha * smoothed)
            ).astype(np.float32, copy=False)
            self._baseline /= max(1e-9, float(np.sum(self._baseline)))

        self._novelty_history.append(novelty)
        state = LiveHarmonicState(
            t=now,
            chroma=tuple(round(float(value), 6) for value in smoothed),
            tonal_confidence=round(tonal_confidence, 6),
            chord=reported_chord,
            chord_confidence=round(chord_confidence, 6),
            novelty=round(novelty, 6),
            novelty_threshold=round(threshold, 6),
            harmonic_change=harmonic_change,
        )
        self._state = state
        return state

    def _held_display_chord(self, now: float) -> str:
        if (
            self._last_display_chord
            and float(now) - self._last_display_chord_t
            <= self.chord_display_hold_seconds
        ):
            return self._last_display_chord
        return ""

    def _build_chroma_projection(self) -> np.ndarray:
        freqs = self._frequencies
        projection = np.zeros((12, freqs.shape[0]), dtype=np.float32)
        indices = np.flatnonzero(self._harmonic_mask)
        if indices.size == 0:
            return projection
        midi = 69.0 + (12.0 * np.log2(freqs[indices] / 440.0))
        pitch = np.mod(midi, 12.0)
        # Use a narrow soft assignment around the nearest equal-tempered note.
        # Linear interpolation across a whole semitone made an ordinary
        # 15-20-cent FFT-bin error look like substantial adjacent-note energy.
        lower = np.floor(pitch).astype(np.int32)
        fraction = pitch - lower
        upper = np.mod(lower + 1, 12)
        kernel_sigma = 0.25
        lower_weight = np.exp(
            -0.5 * np.square(fraction / kernel_sigma)
        )
        upper_weight = np.exp(
            -0.5
            * np.square((1.0 - fraction) / kernel_sigma)
        )
        weight_total = np.maximum(
            lower_weight + upper_weight,
            1e-12,
        )
        lower_weight /= weight_total
        upper_weight /= weight_total
        spectral_weight = 1.0 / np.sqrt(
            np.maximum(freqs[indices], _HARMONIC_MIN_HZ)
        )
        projection[lower, indices] += (
            lower_weight * spectral_weight
        ).astype(np.float32)
        projection[upper, indices] += (
            upper_weight * spectral_weight
        ).astype(np.float32)
        return projection

    def _isolate_tonal_peaks(
        self,
        magnitude: np.ndarray,
    ) -> np.ndarray:
        """Keep narrow, locally prominent peaks inside the musical register.

        Broad spectra, low-frequency movement, and high-frequency hiss are
        removed before octave folding. A peak must be a local maximum, fall
        away at its shoulders, remain narrow at half height, and rise above
        a local sideband median.
        """

        cleaned = np.zeros_like(magnitude, dtype=np.float32)
        indices = np.flatnonzero(self._harmonic_mask)
        if indices.size < 17:
            return cleaned
        values = np.asarray(magnitude, dtype=np.float32)
        in_range = values[indices]
        mean = float(np.mean(in_range))
        if mean <= 1e-12:
            return cleaned
        global_peak = float(np.max(values))
        in_range_peak = float(np.max(in_range))
        if (
            global_peak <= 1e-12
            or in_range_peak < (0.03 * global_peak)
        ):
            return cleaned
        flatness = float(
            np.exp(
                np.mean(np.log(np.maximum(in_range, 1e-12)))
            )
            / mean
        )
        if (
            not math.isfinite(flatness)
            or flatness >= _BROADBAND_FLATNESS_LIMIT
        ):
            return cleaned

        radius = 8
        padded = np.pad(values, (radius, radius), mode="edge")

        def shifted(offset: int) -> np.ndarray:
            start = radius + offset
            return padded[start : start + values.size]

        local_maximum = (
            (values > shifted(-1))
            & (values >= shifted(1))
        )
        # A tonal FFT maximum drops rapidly just outside its Hann-window
        # main lobe. With the zero-padded grid, a three-bin shoulder remains
        # inside a semitone while the nearest triad tone is farther away.
        shoulder = np.maximum(
            shifted(-3),
            shifted(3),
        )
        narrow_shoulders = shoulder <= (
            values * _PEAK_SHOULDER_RATIO
        )
        narrow_width = np.zeros(values.shape, dtype=np.bool_)
        for peak_index in np.flatnonzero(
            local_maximum & self._harmonic_mask
        ):
            peak_value = float(values[peak_index])
            half_height = 0.5 * peak_value
            width = 1
            for direction in (-1, 1):
                for distance in range(1, 5):
                    neighbor = peak_index + (direction * distance)
                    if (
                        neighbor < 0
                        or neighbor >= values.size
                        or values[neighbor] < half_height
                    ):
                        break
                    width += 1
            narrow_width[peak_index] = (
                width <= _PEAK_HALF_HEIGHT_MAX_BINS
            )

        sidebands = np.stack(
            tuple(
                shifted(offset)
                for offset in (-4, -3, 3, 4)
            ),
            axis=0,
        )
        # The nearest valleys stay inside the interval to neighboring triad
        # tones in the low register. A broad smear has no such valleys.
        local_floor = np.median(sidebands, axis=0)
        prominence = np.maximum(
            values - (1.5 * local_floor),
            0.0,
        )
        peak_mask = (
            local_maximum
            & narrow_shoulders
            & narrow_width
            & (prominence > 0.0)
            & self._harmonic_mask
        )
        cleaned = np.where(
            peak_mask,
            prominence,
            0.0,
        ).astype(np.float32)
        retained_ratio = float(
            np.sum(cleaned[indices])
            / max(1e-12, float(np.sum(in_range)))
        )
        if retained_ratio < 0.045:
            cleaned.fill(0.0)
            return cleaned
        return self._suppress_natural_harmonics(cleaned)

    def _suppress_natural_harmonics(
        self,
        magnitude: np.ndarray,
    ) -> np.ndarray:
        """Reduce overtone partials without treating inversions as new roots.

        Octaves reinforce the same pitch class and are retained. Peaks close
        to non-octave integer multiples of an accepted lower fundamental are
        attenuated before chroma folding. Independently played chord tones in
        ordinary inversions have non-integer ratios and remain fundamentals.
        """

        cleaned = np.asarray(magnitude, dtype=np.float32).copy()
        peak_indices = np.flatnonzero(
            (cleaned > 0.0) & self._harmonic_mask
        )
        fundamentals: list[int] = []
        for peak_index in peak_indices:
            frequency = float(self._frequencies[peak_index])
            peak_value = float(cleaned[peak_index])
            harmonic_support = False
            for fundamental_index in fundamentals:
                fundamental_frequency = float(
                    self._frequencies[fundamental_index]
                )
                ratio = frequency / max(
                    fundamental_frequency,
                    1e-9,
                )
                harmonic_number = int(round(ratio))
                if not 2 <= harmonic_number <= 20:
                    continue
                cents_error = abs(
                    1200.0
                    * math.log2(
                        ratio / float(harmonic_number)
                    )
                )
                if cents_error > 55.0:
                    continue
                fundamental_value = float(
                    magnitude[fundamental_index]
                )
                if peak_value > fundamental_value * 1.10:
                    continue
                harmonic_support = True
                if harmonic_number & (harmonic_number - 1):
                    # Preserve a little evidence for genuinely doubled notes,
                    # but prevent 3rd/5th/7th/etc. partials from completing a
                    # chord template on behalf of one played fundamental.
                    cleaned[peak_index] *= 0.015
                break
            if not harmonic_support:
                fundamentals.append(int(peak_index))
        return cleaned

    @staticmethod
    def _summarize_debug_notes(
        chroma: np.ndarray,
    ) -> tuple[tuple[str, float], ...]:
        values = np.asarray(chroma, dtype=np.float32)
        peak = float(np.max(values)) if values.size else 0.0
        if peak > 1e-9:
            values = values / peak
        return tuple(
            (pitch, round(float(value), 5))
            for pitch, value in zip(_PITCH_NAMES, values)
        )

    @staticmethod
    def _build_chord_templates() -> tuple[np.ndarray, tuple[str, ...]]:
        templates: list[np.ndarray] = []
        names: list[str] = []
        for root in range(12):
            for quality, third in (("", 4), ("m", 3)):
                template = np.zeros(12, dtype=np.float32)
                template[root] = 1.0
                template[(root + third) % 12] = 0.8
                template[(root + 7) % 12] = 0.7
                template /= max(1e-9, float(np.linalg.norm(template)))
                templates.append(template)
                names.append(f"{_PITCH_NAMES[root]}{quality}")
        return np.stack(templates), tuple(names)

    def _classify(self, chroma: np.ndarray) -> tuple[str, float, float]:
        scores = np.zeros(len(self._template_names), dtype=np.float32)
        for index in range(len(self._template_names)):
            root = index // 2
            third_interval = 4 if index % 2 == 0 else 3
            third_energy = float(chroma[(root + third_interval) % 12])
            fifth_energy = float(chroma[(root + 7) % 12])
            triad_mass = (
                float(chroma[root])
                + third_energy
                + fifth_energy
            )
            # Geometric balance makes the distinguishing third tone matter:
            # two shared notes cannot fully compensate for a missing third.
            triad_balance = 3.0 * (
                max(
                    1e-12,
                    float(chroma[root])
                    * third_energy
                    * fifth_energy,
                )
                ** (1.0 / 3.0)
            )
            # Root identity comes from the interval pattern, not whichever
            # chord tone happens to be loudest or lowest in an inversion.
            scores[index] = (
                (0.58 * triad_mass)
                + (0.42 * triad_balance)
            )

        ranked = np.argsort(scores)
        best_index = int(ranked[-1])
        best = float(scores[best_index])
        second = float(scores[int(ranked[-2])])
        margin = max(0.0, best - second)
        relative_margin = margin / max(1e-9, best)
        triad_quality = max(0.0, min(1.0, best * 2.1))
        confidence = max(
            0.0,
            min(
                1.0,
                (0.75 * triad_quality)
                + (0.25 * min(1.0, relative_margin * 5.0)),
            ),
        )
        best_root = best_index // 2
        best_third = 4 if best_index % 2 == 0 else 3
        required_tones = (
            float(chroma[best_root]),
            float(chroma[(best_root + best_third) % 12]),
            float(chroma[(best_root + 7) % 12]),
        )
        peak_energy = max(1e-9, float(np.max(chroma)))
        triad_completeness = min(required_tones) / peak_energy
        if triad_completeness < 0.08:
            return (
                "",
                min(
                    confidence,
                    0.29 * (triad_completeness / 0.08),
                ),
                triad_quality,
            )
        return self._template_names[best_index], confidence, triad_quality

    def _novelty_threshold(self) -> float:
        if len(self._novelty_history) < 8:
            return 0.18
        values = np.asarray(self._novelty_history, dtype=np.float32)
        median = float(np.median(values))
        mad = float(np.median(np.abs(values - median)))
        raw_threshold = max(0.14, median + (3.0 * 1.4826 * mad))
        sensitivity_scale = 1.35 - (0.7 * self.sensitivity)
        return max(0.10, min(0.60, raw_threshold * sensitivity_scale))
