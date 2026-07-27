"""Explicit structural visual actuation with applied/degraded/rejected results."""

from __future__ import annotations

from dataclasses import dataclass

from dreamsync.effects import EffectCycler

from .models import CueProposal
from .structure_models import StructuralActionResult


@dataclass(frozen=True)
class StructuralActuatorConfig:
    bar_actions_enabled: bool = False
    phrase_actions_enabled: bool = False
    section_actions_enabled: bool = False


class StructuralVisualActuator:
    """Apply a committed structural cue while preserving non-visual layout state."""

    def __init__(self, config: StructuralActuatorConfig | None = None) -> None:
        self.config = config or StructuralActuatorConfig()
        self.reset()

    def reset(self) -> None:
        self._section_motifs: dict[str, tuple[str, str | None]] = {}
        self._history: list[StructuralActionResult] = []

    @property
    def history(self) -> tuple[StructuralActionResult, ...]:
        return tuple(self._history[-128:])

    def apply(
        self,
        cue: CueProposal,
        *,
        now_t: float,
        beat_index: int | None,
        bar_index: int | None,
        downbeat: bool,
        meter_confident: bool,
        effect_cycler: EffectCycler | None,
        enabled_effects: tuple[str, ...],
    ) -> StructuralActionResult:
        result = self._apply(
            cue,
            now_t=now_t,
            beat_index=beat_index,
            bar_index=bar_index,
            downbeat=downbeat,
            meter_confident=meter_confident,
            effect_cycler=effect_cycler,
            enabled_effects=enabled_effects,
        )
        self._history.append(result)
        return result

    def _apply(
        self,
        cue: CueProposal,
        *,
        now_t: float,
        beat_index: int | None,
        bar_index: int | None,
        downbeat: bool,
        meter_confident: bool,
        effect_cycler: EffectCycler | None,
        enabled_effects: tuple[str, ...],
    ) -> StructuralActionResult:
        target_bar = cue.target.target_bar_index if cue.target else None
        common = {
            "cue_id": cue.cue_id,
            "requested_effect": cue.requested_effect,
            "requested_palette_action": cue.color_action,
            "target_bar": target_bar,
            "committed_beat": beat_index,
        }
        if cue.state != "committed":
            return StructuralActionResult(
                outcome="rejected",
                applied_effect=None,
                applied_palette=None,
                reason="cue is not committed",
                **common,
            )
        if cue.target is None:
            return StructuralActionResult(
                outcome="rejected",
                applied_effect=None,
                applied_palette=None,
                reason="structural cue has no musical target",
                **common,
            )
        if (
            not downbeat
            or not meter_confident
            or beat_index != cue.target.target_beat_index
            or bar_index != cue.target.target_bar_index
        ):
            return StructuralActionResult(
                outcome="rejected",
                applied_effect=None,
                applied_palette=None,
                reason="commit did not consume the matching confident downbeat",
                **common,
            )
        tier_enabled = (
            self.config.bar_actions_enabled
            if cue.cue_class == "bar_marker"
            else self.config.phrase_actions_enabled
            if cue.cue_class == "phrase_reset"
            else self.config.section_actions_enabled
        )
        if not tier_enabled:
            return StructuralActionResult(
                outcome="rejected",
                applied_effect=None,
                applied_palette=None,
                reason=f"{cue.cue_class} actions are disabled",
                **common,
            )
        if effect_cycler is None:
            return StructuralActionResult(
                outcome="rejected",
                applied_effect=None,
                applied_palette=None,
                reason="no effect cycler is active",
                **common,
            )
        enabled = set(enabled_effects)
        requested = cue.requested_effect or (
            cue.effect_candidates[0] if cue.effect_candidates else None
        )
        palette_name: str | None = None
        if cue.cue_class == "section_recall" and cue.section_id:
            motif = self._section_motifs.get(cue.section_id)
            if motif is not None:
                requested, palette_name = motif
        if requested is not None and requested not in enabled:
            return StructuralActionResult(
                outcome="rejected",
                applied_effect=None,
                applied_palette=None,
                reason="requested effect is no longer enabled",
                **common,
            )
        try:
            preset = effect_cycler.apply_structural_action(
                cue_class=cue.cue_class,
                effect_name=requested,
                color_action=cue.color_action,
                target_bar=cue.target.target_bar_index,
                now_t=now_t,
                palette_name=palette_name,
            )
        except (KeyError, ValueError) as exc:
            return StructuralActionResult(
                outcome="rejected",
                applied_effect=None,
                applied_palette=None,
                reason=str(exc),
                **common,
            )
        applied_effect = effect_cycler.current_effect
        applied_palette = effect_cycler.current_palette
        if cue.section_id and cue.cue_class in {
            "section_recall",
            "section_transition",
        }:
            self._section_motifs[cue.section_id] = (
                applied_effect or preset.name,
                applied_palette,
            )
        outcome = "applied" if requested in {None, applied_effect} else "degraded"
        return StructuralActionResult(
            outcome=outcome,
            applied_effect=applied_effect,
            applied_palette=applied_palette,
            reason=(
                "structural action applied on target downbeat"
                if outcome == "applied"
                else "structural action fell back to the active effect"
            ),
            preset=preset,
            **common,
        )
