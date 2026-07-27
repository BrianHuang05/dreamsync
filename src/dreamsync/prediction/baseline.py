"""Adapter for the original absolute-chord live progression predictor."""

from __future__ import annotations

from dreamsync.dsp.harmonic import LiveChordProgressionPredictor

from .models import (
    LiveMusicalObservation,
    PredictedMusicalEvent,
    PredictionAlternative,
    PredictionEvidence,
)


class BaselinePredictionAdapter:
    MODEL_VERSION = "absolute-chord-baseline-v1"

    def __init__(self) -> None:
        self._predictor = LiveChordProgressionPredictor()
        self._sequence = 0

    def reset(self) -> None:
        self._predictor.reset()
        self._sequence = 0

    def observe(
        self, observation: LiveMusicalObservation
    ) -> tuple[PredictedMusicalEvent, ...]:
        if observation.chord_change and observation.absolute_chord:
            self._predictor.observe(
                t=observation.t,
                chord=observation.absolute_chord,
                beat_period=observation.beat_period,
            )
        prediction = self._predictor.prediction
        if prediction is None:
            return ()
        self._sequence += 1
        return (
            PredictedMusicalEvent(
                prediction_id=f"baseline-{self._sequence}",
                created_t=observation.t,
                target_t=prediction.t,
                timing_sigma=max(0.1, (observation.beat_period or 0.5) * 0.3),
                event_type="chord_change",
                target_function=prediction.chord,
                target_section=None,
                direction=None,
                probability=prediction.confidence,
                alternatives=(
                    PredictionAlternative(prediction.chord, prediction.confidence),
                    PredictionAlternative("unknown", 1.0 - prediction.confidence),
                ),
                evidence=(
                    PredictionEvidence(
                        prediction.source,
                        prediction.confidence,
                        f"context={prediction.pattern_length}",
                    ),
                ),
                model_version=self.MODEL_VERSION,
                raw_probability=prediction.confidence,
            ),
        )
