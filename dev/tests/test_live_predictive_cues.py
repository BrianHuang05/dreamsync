from __future__ import annotations

from dataclasses import replace

from dreamsync.prediction.cue_policy import (
    CuePolicyConfig,
    PredictiveCuePolicy,
)
from dreamsync.prediction.models import (
    PredictedMusicalEvent,
    PredictionAlternative,
    PredictionEvidence,
)
from dreamsync.prediction.runtime import (
    LivePredictiveRuntime,
    PredictiveRuntimeConfig,
)


def _event(
    *,
    event_type: str = "chord_change",
    probability: float = 0.9,
    target_t: float = 1.0,
    timing_sigma: float = 0.08,
) -> PredictedMusicalEvent:
    return PredictedMusicalEvent(
        prediction_id="p1",
        created_t=0.0,
        target_t=target_t,
        timing_sigma=timing_sigma,
        event_type=event_type,
        target_function="I",
        target_section=None,
        direction=None,
        probability=probability,
        alternatives=(PredictionAlternative("I", 1.0),),
        evidence=(PredictionEvidence("test", 1.0),),
        model_version="test",
    )


def _policy(**overrides) -> PredictiveCuePolicy:
    config = CuePolicyConfig(
        cues_enabled=True,
        shadow_mode=False,
        high_impact_enabled=True,
        cooldown_seconds=2.0,
        **overrides,
    )
    return PredictiveCuePolicy(config)


def test_only_enabled_effects_are_selected_and_no_spatial_action_exists() -> None:
    policy = _policy()
    cues = policy.update(
        (_event(),),
        now_t=0.0,
        meter_confident=True,
        enabled_effects=("wave_drift",),
        brightness_limit=0.5,
    )
    assert cues[0].effect_candidates == ("wave_drift",)
    assert not hasattr(cues[0], "spatial")
    suppressed = policy.update(
        (replace(_event(), target_t=2.0),),
        now_t=0.1,
        meter_confident=True,
        enabled_effects=("not-enabled",),
        brightness_limit=0.5,
    )
    assert suppressed[0].state == "proposed"


def test_bar_preserves_effect_while_phrase_selects_a_different_enabled_effect() -> None:
    policy = _policy(allowed_cue_classes=("bar_marker", "phrase_reset"))
    bar = policy.update(
        (_event(event_type="bar_marker"),),
        now_t=0.0,
        meter_confident=True,
        enabled_effects=("wave_drift", "slow_breathe"),
        brightness_limit=1.0,
        current_effect="wave_drift",
    )[0]
    assert bar.requested_effect is None
    assert bar.color_action == "small_color_move"

    phrase = policy.update(
        (_event(event_type="phrase_boundary", target_t=2.0),),
        now_t=0.1,
        meter_confident=True,
        enabled_effects=("wave_drift", "slow_breathe"),
        brightness_limit=1.0,
        current_effect="wave_drift",
    )[0]
    assert phrase.requested_effect == "slow_breathe"
    assert phrase.color_action == "advance_approved_palette"


def test_cue_schedules_commits_and_respects_brightness() -> None:
    policy = _policy(maximum_intensity=0.4)
    cues = policy.update(
        (_event(),),
        now_t=0.0,
        meter_confident=True,
        enabled_effects=("beat_pulse",),
        brightness_limit=0.2,
    )
    assert cues[0].state == "scheduled"
    assert cues[0].intensity <= 0.2
    assert not policy.commit_due(0.5)
    committed = policy.commit_due(0.96)
    assert committed[0].state == "committed"
    assert policy.active_committed(1.05)
    confirmed = policy.observe_outcome(
        now_t=1.05,
        target_function="I",
    )
    assert confirmed[0].state == "confirmed"


def test_falling_confidence_degrades_scheduled_cue() -> None:
    policy = _policy()
    policy.update(
        (_event(),),
        now_t=0.0,
        meter_confident=True,
        enabled_effects=("beat_pulse",),
        brightness_limit=1.0,
    )
    policy.update(
        (),
        now_t=0.4,
        meter_confident=True,
        enabled_effects=("beat_pulse",),
        brightness_limit=1.0,
    )
    assert policy.history[-1].state == "degraded"


def test_high_impact_requires_meter_and_master_toggle_is_immediate() -> None:
    policy = _policy()
    cues = policy.update(
        (_event(event_type="harmonic_resolution"),),
        now_t=0.0,
        meter_confident=False,
        enabled_effects=("drop_blast",),
        brightness_limit=1.0,
    )
    assert cues[0].state == "proposed"
    policy.disable()
    assert not policy.active


def test_live_runtime_master_toggle_cancels_without_resetting_analysis() -> None:
    runtime = LivePredictiveRuntime(
        PredictiveRuntimeConfig(
            analysis_enabled=True,
            cues_enabled=True,
            shadow_mode=False,
        )
    )
    runtime.cue_policy.update(
        (_event(),),
        now_t=0.0,
        meter_confident=True,
        enabled_effects=("beat_pulse",),
        brightness_limit=1.0,
    )
    assert runtime.cue_policy.active
    engine = runtime.engine
    runtime.set_cues_enabled(False)
    assert not runtime.cue_policy.active
    assert runtime.engine is engine
    assert runtime.config.analysis_enabled is True
