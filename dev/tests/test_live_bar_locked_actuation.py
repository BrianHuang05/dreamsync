from __future__ import annotations

from dreamsync.effects import EffectCycler
from dreamsync.prediction.cue_policy import CuePolicyConfig, PredictiveCuePolicy
from dreamsync.prediction.models import (
    CueProposal,
    PredictedMusicalEvent,
    PredictionEvidence,
)
from dreamsync.prediction.structure_models import StructuralCueTarget
from dreamsync.prediction.visual_actuator import (
    StructuralActuatorConfig,
    StructuralVisualActuator,
)


def _target() -> StructuralCueTarget:
    return StructuralCueTarget(
        target_beat_index=16,
        target_bar_index=4,
        target_t=8.0,
        requires_downbeat=True,
        timing_sigma=0.05,
    )


def _event() -> PredictedMusicalEvent:
    return PredictedMusicalEvent(
        prediction_id="structure-1",
        created_t=6.0,
        target_t=8.0,
        timing_sigma=0.05,
        event_type="section_transition",
        target_function=None,
        target_section="B",
        direction=None,
        probability=0.95,
        alternatives=(),
        evidence=(PredictionEvidence("section_prefix", 0.9),),
        model_version="test",
        target=_target(),
    )


def _scheduled_policy() -> PredictiveCuePolicy:
    policy = PredictiveCuePolicy(
        CuePolicyConfig(
            cues_enabled=True,
            high_impact_enabled=True,
            shadow_mode=False,
            allowed_cue_classes=("section_transition",),
        )
    )
    policy.update(
        (_event(),),
        now_t=6.0,
        meter_confident=True,
        enabled_effects=("gradient_flow", "fast_scroll"),
        brightness_limit=1.0,
    )
    return policy


def test_structural_cue_cannot_commit_from_time_or_secondary_beat() -> None:
    policy = _scheduled_policy()
    assert policy.commit_due(8.0) == ()
    assert policy.commit_on_downbeat(
        now_t=8.0,
        beat_index=16,
        bar_index=4,
        downbeat=False,
        meter_confident=True,
    ) == ()
    committed = policy.commit_on_downbeat(
        now_t=8.0,
        beat_index=16,
        bar_index=4,
        downbeat=True,
        meter_confident=True,
    )
    assert len(committed) == 1
    assert committed[0].target == _target()


def test_requested_enabled_effect_is_actually_applied() -> None:
    cue = _scheduled_policy().commit_on_downbeat(
        now_t=8.0,
        beat_index=16,
        bar_index=4,
        downbeat=True,
        meter_confident=True,
    )[0]
    cycler = EffectCycler(seed=7)
    actuator = StructuralVisualActuator(
        StructuralActuatorConfig(section_actions_enabled=True)
    )
    result = actuator.apply(
        cue,
        now_t=8.0,
        beat_index=16,
        bar_index=4,
        downbeat=True,
        meter_confident=True,
        effect_cycler=cycler,
        enabled_effects=("gradient_flow", "fast_scroll"),
    )
    assert result.outcome == "applied"
    assert result.requested_effect == "gradient_flow"
    assert result.applied_effect == "gradient_flow"
    assert cycler.current_effect == "gradient_flow"


def test_bar_preserves_effect_and_phrase_changes_effect_and_palette() -> None:
    cycler = EffectCycler(
        seed=7,
        show_palette_cycle=("cool", "sunset"),
    )
    initial = cycler.apply_structural_action(
        cue_class="section_transition",
        effect_name="wave_drift",
        color_action=None,
        target_bar=0,
        now_t=0.0,
    )
    assert initial.name == "wave_drift"
    assert cycler.current_palette == "cool"

    bar = cycler.apply_structural_action(
        cue_class="bar_marker",
        effect_name="color_scroll",
        color_action="small_color_move",
        target_bar=1,
        now_t=2.0,
    )
    assert bar.name == "wave_drift"
    assert cycler.current_effect == "wave_drift"
    assert cycler.current_palette == "cool"

    phrase = cycler.apply_structural_action(
        cue_class="phrase_reset",
        effect_name="slow_breathe",
        color_action="advance_approved_palette",
        target_bar=4,
        now_t=8.0,
    )
    assert phrase.name == "slow_breathe"
    assert cycler.current_effect == "slow_breathe"
    assert cycler.current_palette == "sunset"
    assert phrase.params["structure_phase_reset"] is True


def test_disabled_effect_and_wrong_identity_are_rejected() -> None:
    cue = _scheduled_policy().commit_on_downbeat(
        now_t=8.0,
        beat_index=16,
        bar_index=4,
        downbeat=True,
        meter_confident=True,
    )[0]
    actuator = StructuralVisualActuator(
        StructuralActuatorConfig(section_actions_enabled=True)
    )
    wrong_identity = actuator.apply(
        cue,
        now_t=8.0,
        beat_index=15,
        bar_index=4,
        downbeat=True,
        meter_confident=True,
        effect_cycler=EffectCycler(seed=1),
        enabled_effects=("gradient_flow",),
    )
    assert wrong_identity.outcome == "rejected"
    disabled = actuator.apply(
        cue,
        now_t=8.0,
        beat_index=16,
        bar_index=4,
        downbeat=True,
        meter_confident=True,
        effect_cycler=EffectCycler(seed=1),
        enabled_effects=("fast_scroll",),
    )
    assert disabled.outcome == "rejected"
    assert disabled.applied_effect is None


def test_structure_control_blocks_mood_and_drop_effect_replacement() -> None:
    cycler = EffectCycler(seed=4)
    initial = cycler.update(
        mood=__import__("dreamsync.mood", fromlist=["Mood"]).Mood.GROOVE,
        t=0.0,
        beat=True,
        bpm=120.0,
        energy=0.5,
    )
    held = cycler.update(
        mood=__import__("dreamsync.mood", fromlist=["Mood"]).Mood.DROP,
        t=20.0,
        beat=True,
        bpm=120.0,
        energy=1.0,
        structure_controlled=True,
    )
    assert held.name == initial.name
