"""Reactive-live facade that owns observation consolidation and shadow state."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, replace

import numpy as np

from dreamsync.dsp.harmonic import LiveHarmonicState
from dreamsync.dsp.meter import LiveMeterState
from dreamsync.dsp.spectral_features import mfcc_from_magnitude

from .arbitration import arbitrate_predictions
from .bar_features import LiveBarFingerprintBuilder
from .boundary import StructurePredictionEngine
from .calibration import PredictionCalibrator
from .engine import PredictiveStructureEngine
from .event_log import PredictionEventLog
from .cue_policy import CuePolicyConfig, PredictiveCuePolicy
from .models import CueProposal, LiveMusicalObservation, PredictedMusicalEvent
from .similarity import MultiFeatureSimilarityMemory
from .structure_models import LiveBarFingerprint, LiveBeatStructureObservation
from .visual_actuator import StructuralActuatorConfig, StructuralVisualActuator


@dataclass(frozen=True)
class PredictiveRuntimeConfig:
    analysis_enabled: bool = False
    diagnostics_enabled: bool = False
    shadow_mode: bool = True
    cues_enabled: bool = False
    high_impact_cues_enabled: bool = False
    max_log_events: int = 4096
    beats_per_bar: int = 4
    sample_rate: int = 44100
    spectral_fft_size: int = 2048
    structure_similarity_enabled: bool = False
    structure_similarity_shadow_mode: bool = True
    structure_memory_bars: int = 256
    structure_min_meter_confidence: float = 0.22
    structure_bar_actions_enabled: bool = False
    structure_phrase_actions_enabled: bool = False
    structure_section_actions_enabled: bool = False
    structure_phrase_threshold: float = 0.48
    structure_section_threshold: float = 0.62
    cue_prepare_threshold: float = 0.54
    cue_schedule_threshold: float = 0.68
    cue_high_impact_threshold: float = 0.80
    maximum_anticipatory_intensity: float = 0.28
    cue_cooldown_seconds: float = 2.0
    allowed_cue_classes: tuple[str, ...] = (
        "chord_accent",
        "resolution_bloom",
        "phrase_reset",
        "section_recall",
        "chorus_lift",
    )


class LivePredictiveRuntime:
    """Runs only when called by the non-callback live analysis loop."""

    def __init__(self, config: PredictiveRuntimeConfig | None = None) -> None:
        self.config = config or PredictiveRuntimeConfig()
        self.engine = PredictiveStructureEngine()
        self.structure_bar_builder = LiveBarFingerprintBuilder(
            beats_per_bar=self.config.beats_per_bar,
            minimum_meter_confidence=self.config.structure_min_meter_confidence,
            nyquist_hz=self.config.sample_rate / 2.0,
        )
        self.structure_similarity = MultiFeatureSimilarityMemory(
            max_bars=self.config.structure_memory_bars,
        )
        self.structure_engine = StructurePredictionEngine(
            beats_per_bar=self.config.beats_per_bar,
            phrase_event_threshold=self.config.structure_phrase_threshold,
            section_event_threshold=self.config.structure_section_threshold,
        )
        self.calibrator = PredictionCalibrator()
        self.cue_policy = PredictiveCuePolicy(
            CuePolicyConfig(
                cues_enabled=self.config.cues_enabled,
                high_impact_enabled=self.config.high_impact_cues_enabled,
                shadow_mode=(
                    self.config.structure_similarity_shadow_mode
                    if self.config.structure_similarity_enabled
                    else self.config.shadow_mode
                ),
                prepare_threshold=self.config.cue_prepare_threshold,
                schedule_threshold=self.config.cue_schedule_threshold,
                high_impact_threshold=self.config.cue_high_impact_threshold,
                cooldown_seconds=self.config.cue_cooldown_seconds,
                maximum_intensity=self.config.maximum_anticipatory_intensity,
                allowed_cue_classes=self.config.allowed_cue_classes,
            )
        )
        self.structural_actuator = StructuralVisualActuator(
            StructuralActuatorConfig(
                bar_actions_enabled=self.config.structure_bar_actions_enabled,
                phrase_actions_enabled=self.config.structure_phrase_actions_enabled,
                section_actions_enabled=self.config.structure_section_actions_enabled,
            )
        )
        self.log = PredictionEventLog(self.config.max_log_events)
        self.reset()

    def reset(self) -> None:
        self.engine.reset()
        self.structure_bar_builder.reset()
        self.structure_similarity.reset()
        self.structure_engine.reset()
        self.structural_actuator.reset()
        self.cue_policy.reset()
        self.log.clear()
        self._beat_index = -1
        self._bar_index = -1
        self._last_energy = 0.0
        self._last_onset = 0.0
        self._last_centroid = 0.0
        self._last_chord_t: float | None = None
        self._last_predictions: tuple[PredictedMusicalEvent, ...] = ()
        self._latest_observation: LiveMusicalObservation | None = None
        self._latest_structure_observation: LiveBeatStructureObservation | None = None
        self._latest_structure_fingerprint: LiveBarFingerprint | None = None
        self._last_structure_predictions: tuple[PredictedMusicalEvent, ...] = ()

    @property
    def predictions(self) -> tuple[PredictedMusicalEvent, ...]:
        return self._last_predictions

    @property
    def observation(self) -> LiveMusicalObservation | None:
        return self._latest_observation

    @property
    def structure_observation(self) -> LiveBeatStructureObservation | None:
        return self._latest_structure_observation

    @property
    def structure_fingerprint(self) -> LiveBarFingerprint | None:
        return self._latest_structure_fingerprint

    def observe_committed(
        self,
        *,
        t: float,
        meter_state: LiveMeterState,
        harmonic_state: LiveHarmonicState,
        absolute_chord: str | None,
        chord_change: bool,
        energy: float,
        onset_density: float,
        spectral_centroid: float,
        bpm: float,
        enabled_effects: tuple[str, ...] = (),
        brightness_limit: float = 1.0,
        magnitude: np.ndarray | None = None,
        band_ratios: tuple[float, ...] = (),
        band_fluxes: tuple[float, ...] = (),
        bass_ratio: float = 0.0,
        harmonic_novelty: float | None = None,
    ) -> tuple[PredictedMusicalEvent, ...]:
        if not (
            self.config.analysis_enabled
            or self.config.structure_similarity_enabled
        ):
            return ()
        self._beat_index += 1
        if meter_state.downbeat:
            self._bar_index += 1
        elif self._bar_index < 0 and meter_state.meter_confident:
            self._bar_index = 0
        beat_period = 60.0 / bpm if bpm > 0.0 else None
        duration = None
        if chord_change and self._last_chord_t is not None and beat_period:
            duration = max(0.25, (t - self._last_chord_t) / beat_period)
        if chord_change:
            self._last_chord_t = t
        observation = LiveMusicalObservation(
            t=float(t),
            beat_index=self._beat_index,
            bar_index=self._bar_index if self._bar_index >= 0 else None,
            beat_in_bar=meter_state.bar_phase,
            meter=(
                (self.config.beats_per_bar, 4)
                if meter_state.meter_confident
                else None
            ),
            meter_confidence=meter_state.phase_confidence,
            downbeat=meter_state.downbeat,
            absolute_chord=absolute_chord or None,
            chord_confidence=harmonic_state.chord_confidence,
            chord_change=bool(chord_change),
            chord_duration_beats=duration,
            chroma=harmonic_state.chroma,
            tonal_confidence=harmonic_state.tonal_confidence,
            energy=float(energy),
            energy_delta=float(energy) - self._last_energy,
            onset_density=float(onset_density),
            onset_density_delta=float(onset_density) - self._last_onset,
            spectral_centroid=float(spectral_centroid),
            centroid_delta=float(spectral_centroid) - self._last_centroid,
            harmonic_rhythm=(1.0 / duration if duration and duration > 0.0 else 0.0),
            beat_period=beat_period,
        )
        self._last_energy = float(energy)
        self._last_onset = float(onset_density)
        self._last_centroid = float(spectral_centroid)
        self._latest_observation = observation
        safe_mfcc = (0.0,) * 13
        if magnitude is not None:
            safe_mfcc = mfcc_from_magnitude(
                magnitude,
                sample_rate=self.config.sample_rate,
                n_fft=self.config.spectral_fft_size,
            )
        self._latest_structure_observation = LiveBeatStructureObservation(
            t=float(t),
            beat_index=self._beat_index,
            bar_index=self._bar_index if self._bar_index >= 0 else None,
            beat_in_bar=meter_state.bar_phase,
            meter=(
                (self.config.beats_per_bar, 4)
                if meter_state.meter_confident
                else None
            ),
            meter_confidence=max(0.0, min(1.0, meter_state.phase_confidence)),
            downbeat=bool(meter_state.downbeat),
            beat_period=beat_period,
            chroma=tuple(float(value) for value in harmonic_state.chroma),
            chroma_confidence=max(
                0.0,
                min(1.0, float(harmonic_state.tonal_confidence)),
            ),
            harmonic_novelty=max(
                0.0,
                float(
                    harmonic_state.novelty
                    if harmonic_novelty is None
                    else harmonic_novelty
                ),
            ),
            mfcc=safe_mfcc,
            band_ratios=tuple(float(value) for value in band_ratios),
            band_fluxes=tuple(float(value) for value in band_fluxes),
            bass_ratio=max(0.0, float(bass_ratio)),
            energy=max(0.0, float(energy)),
            onset_strength=max(0.0, float(onset_density)),
            spectral_centroid=max(0.0, float(spectral_centroid)),
            chord_label=absolute_chord or None,
            chord_confidence=max(
                0.0,
                min(1.0, float(harmonic_state.chord_confidence)),
            ),
        )
        if self.config.structure_similarity_enabled:
            completed = self.structure_bar_builder.observe(
                self._latest_structure_observation
            )
            if completed is not None:
                self._latest_structure_fingerprint = completed
                similarities = self.structure_similarity.add(completed)
                sequence_matches = tuple(
                    match
                    for horizon in self.structure_similarity.horizons
                    if horizon > 1
                    for match in self.structure_similarity.top_matches(
                        horizon=horizon
                    )
                )
                self._last_structure_predictions = self.structure_engine.observe(
                    completed,
                    similarities=similarities,
                    sequence_matches=sequence_matches,
                    current_beat_index=self._latest_structure_observation.beat_index,
                    current_bar_index=int(
                        self._latest_structure_observation.bar_index
                        if self._latest_structure_observation.bar_index is not None
                        else completed.bar_index + 1
                    ),
                    downbeat_t=float(t),
                    beat_period=float(beat_period or 0.5),
                )
                self._last_predictions = arbitrate_predictions(
                    self._last_structure_predictions
                )
                self.cue_policy.update(
                    self._last_predictions,
                    now_t=t,
                    meter_confident=meter_state.meter_confident,
                    enabled_effects=enabled_effects,
                    brightness_limit=brightness_limit,
                )
                if self.config.diagnostics_enabled:
                    self.log.append("structure_bar", t=t, payload=completed)
                    for event in self._last_structure_predictions:
                        self.log.append("structure_prediction", t=t, payload=event)
        if not self.config.analysis_enabled:
            return self._last_structure_predictions
        raw = self.engine.observe(observation)
        if observation.chord_change:
            function = (
                self.engine.snapshot.functional_hypotheses[0].numeral
                if self.engine.snapshot.functional_hypotheses
                else None
            )
            self.cue_policy.observe_outcome(
                now_t=t,
                target_function=function,
            )
        calibrated = tuple(
            item
            for item in (self.calibrator.calibrate(event) for event in raw)
            if item is not None
        )
        if not self.config.structure_similarity_enabled:
            self._last_predictions = arbitrate_predictions(calibrated)
            self.cue_policy.update(
                self._last_predictions,
                now_t=t,
                meter_confident=meter_state.meter_confident,
                enabled_effects=enabled_effects,
                brightness_limit=brightness_limit,
            )
        if self.config.diagnostics_enabled:
            self.log.append("observation", t=t, payload=observation)
            for event in self._last_predictions:
                self.log.append("prediction", t=t, payload=event)
        return self._last_predictions

    def commit_due_cues(
        self,
        *,
        now_t: float,
        beat_index: int | None = None,
        bar_index: int | None = None,
        downbeat: bool = False,
        meter_confident: bool = False,
    ) -> tuple[CueProposal, ...]:
        if self.config.structure_similarity_enabled:
            return self.cue_policy.commit_on_downbeat(
                now_t=now_t,
                beat_index=beat_index,
                bar_index=bar_index,
                downbeat=downbeat,
                meter_confident=meter_confident,
            )
        return self.cue_policy.commit_due(now_t)

    def active_committed_cues(self, *, now_t: float) -> tuple[CueProposal, ...]:
        return self.cue_policy.active_committed(now_t)

    def set_cues_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self.cue_policy.config.cues_enabled == enabled:
            return
        self.cue_policy.config = replace(
            self.cue_policy.config,
            cues_enabled=enabled,
        )
        if not enabled:
            self.cue_policy.disable(reason="predictive cue master disabled")

    def set_beats_per_bar(self, beats_per_bar: int) -> None:
        """Rebuild meter-dependent structure state for a manually set meter."""

        value = int(beats_per_bar)
        if value < 2:
            raise ValueError("beats_per_bar must be at least 2")
        if value == self.config.beats_per_bar:
            self.reset()
            return
        self.config = replace(self.config, beats_per_bar=value)
        self.structure_bar_builder = LiveBarFingerprintBuilder(
            beats_per_bar=value,
            minimum_meter_confidence=self.config.structure_min_meter_confidence,
            nyquist_hz=self.config.sample_rate / 2.0,
        )
        self.structure_engine = StructurePredictionEngine(
            beats_per_bar=value,
            phrase_event_threshold=self.config.structure_phrase_threshold,
            section_event_threshold=self.config.structure_section_threshold,
        )
        self.reset()

    def set_structure_action_controls(
        self,
        *,
        shadow_mode: bool,
        bar_actions: bool,
        phrase_actions: bool,
        section_actions: bool,
    ) -> None:
        self.structural_actuator.config = replace(
            self.structural_actuator.config,
            bar_actions_enabled=bool(bar_actions),
            phrase_actions_enabled=bool(phrase_actions),
            section_actions_enabled=bool(section_actions),
        )
        enabled = bool(bar_actions or phrase_actions or section_actions)
        self.cue_policy.config = replace(
            self.cue_policy.config,
            cues_enabled=enabled,
            high_impact_enabled=bool(section_actions),
            shadow_mode=bool(shadow_mode),
        )
        if shadow_mode or not enabled:
            self.cue_policy.disable(
                reason=(
                    "structure shadow mode enabled"
                    if shadow_mode
                    else "all structure action tiers disabled"
                )
            )

    def diagnostics(self, *, now_t: float) -> dict[str, object]:
        snapshot = self.engine.snapshot
        structure_diagnostics = self.structure_similarity.diagnostics()
        upcoming_effect_cues = []
        for item in self.cue_policy.active:
            if item.state not in {"armed", "scheduled"}:
                continue
            target_t = (
                item.target.target_t
                if item.target is not None
                else item.execute_t
            )
            if target_t < now_t - 1e-6:
                continue
            effect = item.requested_effect or (
                item.effect_candidates[0]
                if item.effect_candidates
                else ""
            )
            upcoming_effect_cues.append(
                {
                    "t": target_t,
                    "effect": effect,
                    "cue_class": item.cue_class,
                    "state": item.state,
                    "confidence": item.confidence,
                    "target_bar": (
                        item.target.target_bar_index
                        if item.target is not None
                        else None
                    ),
                    "target_beat": (
                        item.target.target_beat_index
                        if item.target is not None
                        else None
                    ),
                }
            )
        upcoming_effect_cues.sort(key=lambda row: float(row["t"]))
        return {
            "predictive_analysis_enabled": self.config.analysis_enabled,
            "predictive_shadow_mode": self.config.shadow_mode,
            "predictive_model_version": self.engine.config.model_version,
            "predictive_key_hypotheses": tuple(
                dataclasses.asdict(item) for item in snapshot.key_hypotheses[:3]
            ),
            "predictive_function_hypotheses": tuple(
                {
                    "numeral": item.numeral,
                    "probability": item.probability,
                    "borrowed": item.borrowed,
                }
                for item in snapshot.functional_hypotheses[:3]
            ),
            "predictive_phrase_hypotheses": tuple(
                dataclasses.asdict(item) for item in snapshot.phrase_hypotheses[:4]
            ),
            "predictive_cycle": self._cycle_diagnostics(),
            "predictive_events": tuple(
                {
                    **dataclasses.asdict(item),
                    "seconds_until": max(0.0, item.target_t - now_t),
                }
                for item in self._last_predictions[:5]
            ),
            "predictive_section_alternatives": tuple(
                dataclasses.asdict(item) for item in snapshot.section_alternatives[:3]
            ),
            "predictive_momentum": tuple(
                dataclasses.asdict(item) for item in snapshot.momentum[:5]
            ),
            "predictive_cues": tuple(
                dataclasses.asdict(item)
                for item in self.cue_policy.active[:5]
            ),
            "predictive_cue_history": tuple(
                dataclasses.asdict(item)
                for item in self.cue_policy.history[-5:]
            ),
            "upcoming_effect_cues": tuple(upcoming_effect_cues[:8]),
            "structure_similarity_enabled": self.config.structure_similarity_enabled,
            "structure_similarity_shadow_mode": (
                self.cue_policy.config.shadow_mode
            ),
            "structure_bar_actions_enabled": (
                self.structural_actuator.config.bar_actions_enabled
            ),
            "structure_phrase_actions_enabled": (
                self.structural_actuator.config.phrase_actions_enabled
            ),
            "structure_section_actions_enabled": (
                self.structural_actuator.config.section_actions_enabled
            ),
            "structure_configured_meter": (self.config.beats_per_bar, 4),
            "structure_current_bar": (
                self._latest_structure_observation.bar_index
                if self._latest_structure_observation is not None
                else None
            ),
            "structure_last_fingerprint": (
                dataclasses.asdict(self._latest_structure_fingerprint)
                if self._latest_structure_fingerprint is not None
                else None
            ),
            "structure_similarity_memory_bars": structure_diagnostics.memory_bars,
            "structure_top_matches": tuple(
                dataclasses.asdict(item)
                for item in structure_diagnostics.top_bar_matches
            ),
            "structure_sequence_matches": tuple(
                dataclasses.asdict(item)
                for item in structure_diagnostics.top_sequence_matches
            ),
            "structure_boundary": (
                dataclasses.asdict(self.structure_engine.tracker.snapshot.observed)
                if self.structure_engine.tracker.snapshot.observed is not None
                else None
            ),
            "structure_upcoming_boundaries": tuple(
                dataclasses.asdict(item)
                for item in self.structure_engine.tracker.snapshot.upcoming
            ),
            "structure_phrase_hypotheses": tuple(
                dataclasses.asdict(item)
                for item in self.structure_engine.tracker.snapshot.phrase_hypotheses
            ),
            "structure_section_hypotheses": tuple(
                dataclasses.asdict(item)
                for item in self.structure_engine.tracker.snapshot.section_hypotheses
            ),
            "structure_section_id": (
                self.structure_engine.tracker.snapshot.current_section_id
            ),
            "structure_action_history": tuple(
                {
                    key: value
                    for key, value in dataclasses.asdict(item).items()
                    if key != "preset"
                }
                for item in self.structural_actuator.history[-8:]
            ),
        }

    def _cycle_diagnostics(self) -> dict[str, object]:
        observation = self._latest_observation
        snapshot = self.engine.snapshot
        meter = observation.meter if observation is not None else None
        base = {
            "available": False,
            "current_bar": None,
            "current_beat": None,
            "cycle_bars": None,
            "cycle_beats": None,
            "bars_to_next_cycle": None,
            "beats_to_next_cycle": None,
            "beats_since_cycle_start": None,
            "time_signature": meter,
            "confidence": 0.0,
        }
        if (
            observation is None
            or observation.bar_index is None
            or observation.beat_in_bar is None
            or meter is None
        ):
            return base
        hypothesis = next(
            (
                item
                for item in snapshot.phrase_hypotheses
                if item.expected_bars is not None
                and item.expected_bars > 0
            ),
            None,
        )
        if hypothesis is None:
            return base
        cycle_bars = int(hypothesis.expected_bars)
        beats_per_bar = int(meter[0])
        if beats_per_bar < 1:
            return base
        bar_offset = (
            int(observation.bar_index)
            - int(hypothesis.phrase_start_bar)
        ) % cycle_bars
        beat_in_bar = max(
            0,
            min(beats_per_bar - 1, int(observation.beat_in_bar)),
        )
        current_bar = bar_offset + 1
        cycle_beats = cycle_bars * beats_per_bar
        current_beat = (bar_offset * beats_per_bar) + beat_in_bar + 1
        return {
            "available": True,
            "current_bar": current_bar,
            "current_beat": current_beat,
            "cycle_bars": cycle_bars,
            "cycle_beats": cycle_beats,
            "bars_to_next_cycle": cycle_bars - current_bar,
            "beats_to_next_cycle": cycle_beats - current_beat,
            "beats_since_cycle_start": current_beat - 1,
            "time_signature": meter,
            "confidence": round(float(hypothesis.probability), 6),
        }
