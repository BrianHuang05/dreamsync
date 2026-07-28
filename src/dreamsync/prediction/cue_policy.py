"""Confidence-gated predictive visual cue policy and lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .models import CueProposal, PredictedMusicalEvent


CUE_POLICY_VERSION = "dreamsync-cue-policy-v1"

EVENT_POLICY: dict[str, tuple[str, tuple[str, ...], str | None, bool]] = {
    "bar_marker": (
        "bar_marker",
        ("wave_drift", "color_scroll", "wave", "scroll"),
        "small_color_move",
        False,
    ),
    "chord_change": (
        "chord_accent",
        ("beat_pulse", "color_scroll", "wave_drift", "pulse", "scroll", "wave"),
        "small_color_move",
        False,
    ),
    "harmonic_resolution": (
        "resolution_bloom",
        ("drop_blast", "beat_pulse", "pulse"),
        "advance_approved_palette",
        True,
    ),
    "phrase_boundary": (
        "phrase_reset",
        (
            "wave_drift",
            "color_scroll",
            "gradient_flow",
            "slow_breathe",
            "color_breathe",
            "beat_pulse",
        ),
        "advance_approved_palette",
        False,
    ),
    "section_repeat": (
        "section_recall",
        ("gradient_flow", "color_scroll", "gradient", "scroll"),
        "recall_palette",
        False,
    ),
    "section_transition": (
        "section_transition",
        ("gradient_flow", "fast_scroll", "drop_blast", "gradient", "scroll"),
        "advance_approved_palette",
        True,
    ),
    "section_entrance": (
        "section_transition",
        ("gradient_flow", "fast_scroll", "gradient", "scroll"),
        "advance_approved_palette",
        True,
    ),
    "build_release": (
        "section_transition",
        ("drop_blast", "fast_scroll", "beat_pulse", "pulse", "scroll"),
        "advance_approved_palette",
        True,
    ),
    "chorus_entrance": (
        "chorus_lift",
        ("drop_blast", "fast_scroll", "beat_pulse", "pulse", "scroll"),
        "advance_approved_palette",
        True,
    ),
}


@dataclass(frozen=True)
class CuePolicyConfig:
    cues_enabled: bool = False
    high_impact_enabled: bool = False
    shadow_mode: bool = True
    prepare_threshold: float = 0.54
    schedule_threshold: float = 0.68
    high_impact_threshold: float = 0.80
    maximum_timing_sigma: float = 0.24
    minimum_lead_time: float = 0.08
    commit_window: float = 0.05
    cooldown_seconds: float = 2.0
    maximum_intensity: float = 0.28
    allowed_cue_classes: tuple[str, ...] = (
        "bar_marker",
        "chord_accent",
        "resolution_bloom",
        "phrase_reset",
        "section_recall",
        "section_transition",
        "chorus_lift",
    )

    def __post_init__(self) -> None:
        thresholds = (
            self.prepare_threshold,
            self.schedule_threshold,
            self.high_impact_threshold,
            self.maximum_intensity,
        )
        if any(not 0.0 <= value <= 1.0 for value in thresholds):
            raise ValueError("cue thresholds and intensity must be between 0 and 1")
        if not self.prepare_threshold <= self.schedule_threshold <= self.high_impact_threshold:
            raise ValueError("cue confidence thresholds must be ordered")


class PredictiveCuePolicy:
    def __init__(self, config: CuePolicyConfig | None = None) -> None:
        self.config = config or CuePolicyConfig()
        self.reset()

    def reset(self) -> None:
        self._active: dict[str, CueProposal] = {}
        self._history: list[CueProposal] = []
        self._events_by_prediction: dict[str, PredictedMusicalEvent] = {}
        self._sequence = 0
        self._last_commit_by_class: dict[str, float] = {}

    @property
    def active(self) -> tuple[CueProposal, ...]:
        return tuple(self._active.values())

    @property
    def history(self) -> tuple[CueProposal, ...]:
        return tuple(self._history[-128:])

    def update(
        self,
        events: tuple[PredictedMusicalEvent, ...],
        *,
        now_t: float,
        meter_confident: bool,
        enabled_effects: tuple[str, ...],
        brightness_limit: float,
        current_effect: str | None = None,
    ) -> tuple[CueProposal, ...]:
        current_keys: set[str] = set()
        for event in events:
            policy = EVENT_POLICY.get(event.event_type)
            if policy is None:
                continue
            cue_class, candidates, color_action, high_impact = policy
            key = _cue_key(event)
            current_keys.add(key)
            eligible = tuple(
                effect for effect in candidates if effect in set(enabled_effects)
            )
            if (
                cue_class in {"bar_marker", "phrase_reset"}
                and current_effect in set(enabled_effects)
                and current_effect not in eligible
            ):
                # Small/medium structural actions can always modulate or reset
                # the active preset, even when its family is not one of the
                # preferred replacement candidates.
                eligible = (*eligible, current_effect)
            requested_effect = (
                None
                if cue_class == "bar_marker"
                else next(
                    (
                        effect
                        for effect in eligible
                        if effect != current_effect
                    ),
                    eligible[0] if eligible else None,
                )
            )
            explanation = ""
            state = "proposed"
            if cue_class not in self.config.allowed_cue_classes:
                explanation = "cue class disabled"
            elif not eligible:
                explanation = "no compatible enabled effect"
            elif not self.config.cues_enabled:
                explanation = "predictive cues disabled"
            elif self.config.shadow_mode:
                explanation = "shadow mode"
            elif high_impact and not self.config.high_impact_enabled:
                explanation = "high-impact cues disabled"
            elif high_impact and not meter_confident:
                explanation = "confident downbeat required"
            elif event.timing_sigma > self.config.maximum_timing_sigma:
                explanation = "timing uncertainty too broad"
            elif event.target_t - now_t < self.config.minimum_lead_time:
                explanation = "insufficient lead time"
            elif (
                now_t - self._last_commit_by_class.get(cue_class, -1e9)
                < self.config.cooldown_seconds
            ):
                explanation = "cue cooldown active"
            else:
                threshold = (
                    self.config.high_impact_threshold
                    if high_impact
                    else self.config.schedule_threshold
                )
                if event.probability >= threshold:
                    state = "scheduled"
                    explanation = "confidence and timing gates passed"
                elif event.probability >= self.config.prepare_threshold:
                    state = "armed"
                    explanation = "prepared below schedule threshold"
                else:
                    explanation = "below prepare threshold"
            existing = self._active.get(key)
            hold_seconds = (
                0.60
                if cue_class == "chorus_lift"
                else 0.45
                if cue_class == "resolution_bloom"
                else max(0.10, event.timing_sigma * 2.0)
            )
            proposal = CueProposal(
                cue_id=existing.cue_id if existing else self._new_id(),
                prediction_id=event.prediction_id,
                created_t=existing.created_t if existing else now_t,
                execute_t=event.target_t,
                expires_t=event.target_t + hold_seconds,
                cue_class=cue_class,
                effect_candidates=eligible,
                color_action=color_action,
                intensity=min(
                    self.config.maximum_intensity,
                    max(0.04, event.probability * self.config.maximum_intensity),
                    max(0.0, float(brightness_limit)),
                ),
                confidence=event.probability,
                state=state,
                explanation=explanation,
                target=event.target,
                requested_effect=requested_effect,
                section_id=event.target_section,
            )
            self._active[key] = proposal
            self._events_by_prediction[event.prediction_id] = event
        for key, proposal in tuple(self._active.items()):
            if key in current_keys or proposal.state in {
                "committed",
                "confirmed",
                "cancelled",
                "degraded",
            }:
                continue
            if (
                proposal.target is not None
                and proposal.state == "scheduled"
                and now_t
                <= proposal.target.target_t
                + max(
                    self.config.commit_window,
                    proposal.target.timing_sigma * 3.0,
                )
            ):
                # Once a bar-indexed cue is scheduled it survives ordinary
                # confidence re-arbitration until its target downbeat.
                continue
            terminal = replace(
                proposal,
                state=(
                    "degraded"
                    if proposal.state == "scheduled"
                    else "cancelled"
                ),
                explanation="prediction disappeared or confidence fell",
            )
            self._history.append(terminal)
            del self._active[key]
        return self.active

    def commit_due(self, now_t: float) -> tuple[CueProposal, ...]:
        committed: list[CueProposal] = []
        for key, proposal in tuple(self._active.items()):
            if proposal.state != "scheduled":
                continue
            if proposal.target is not None:
                # Structural targets require explicit musical identity.  A
                # timestamp-only render tick may never commit them.
                continue
            if now_t + self.config.commit_window < proposal.execute_t:
                continue
            if now_t > proposal.expires_t:
                terminal = replace(
                    proposal,
                    state="cancelled",
                    explanation="execution deadline expired",
                )
                self._history.append(terminal)
                del self._active[key]
                continue
            terminal = replace(
                proposal,
                state="committed",
                explanation="committed through live render path",
            )
            self._active[key] = terminal
            self._history.append(terminal)
            self._last_commit_by_class[proposal.cue_class] = now_t
            committed.append(terminal)
        return tuple(committed)

    def commit_on_downbeat(
        self,
        *,
        now_t: float,
        beat_index: int | None,
        bar_index: int | None,
        downbeat: bool,
        meter_confident: bool,
    ) -> tuple[CueProposal, ...]:
        committed: list[CueProposal] = []
        for key, proposal in tuple(self._active.items()):
            if proposal.state != "scheduled" or proposal.target is None:
                continue
            target = proposal.target
            if (
                beat_index is not None
                and beat_index > target.target_beat_index
            ) or (
                bar_index is not None
                and bar_index > target.target_bar_index
            ):
                self._history.append(
                    replace(
                        proposal,
                        state="cancelled",
                        explanation="target downbeat identity was missed",
                    )
                )
                del self._active[key]
                continue
            if not downbeat or not meter_confident:
                continue
            if (
                beat_index != target.target_beat_index
                or bar_index != target.target_bar_index
            ):
                continue
            timing_tolerance = max(
                self.config.commit_window,
                target.timing_sigma * 3.0,
            )
            if abs(now_t - target.target_t) > timing_tolerance:
                self._history.append(
                    replace(
                        proposal,
                        state="degraded",
                        explanation="downbeat identity matched but clock error was excessive",
                    )
                )
                del self._active[key]
                continue
            terminal = replace(
                proposal,
                state="committed",
                explanation="committed on matching confident downbeat identity",
            )
            self._active[key] = terminal
            self._history.append(terminal)
            self._last_commit_by_class[proposal.cue_class] = now_t
            committed.append(terminal)
        return tuple(committed)

    def active_committed(self, now_t: float) -> tuple[CueProposal, ...]:
        active: list[CueProposal] = []
        for key, proposal in tuple(self._active.items()):
            if proposal.state != "committed":
                continue
            if now_t <= proposal.expires_t:
                active.append(proposal)
                continue
            terminal = replace(
                proposal,
                state="degraded",
                explanation="commit window ended without observed confirmation",
            )
            self._history.append(terminal)
            del self._active[key]
        return tuple(active)

    def observe_outcome(
        self,
        *,
        now_t: float,
        target_function: str | None,
    ) -> tuple[CueProposal, ...]:
        outcomes: list[CueProposal] = []
        for key, proposal in tuple(self._active.items()):
            if proposal.state != "committed":
                continue
            event = self._events_by_prediction.get(proposal.prediction_id)
            if event is None:
                continue
            tolerance = max(0.12, event.timing_sigma * 2.0)
            if abs(now_t - event.target_t) > tolerance:
                continue
            expected = event.target_function
            matched = expected is None or expected == target_function
            terminal = replace(
                proposal,
                state="confirmed" if matched else "degraded",
                explanation=(
                    f"observed {target_function or 'unknown'} matched prediction"
                    if matched
                    else (
                        f"expected {expected or 'event'}, observed "
                        f"{target_function or 'unknown'}"
                    )
                ),
            )
            outcomes.append(terminal)
            self._history.append(terminal)
            del self._active[key]
        return tuple(outcomes)

    def confirm(
        self,
        *,
        prediction_id: str,
        matched: bool,
        reason: str,
    ) -> CueProposal | None:
        for key, proposal in tuple(self._active.items()):
            if proposal.prediction_id != prediction_id:
                continue
            terminal = replace(
                proposal,
                state="confirmed" if matched else "degraded",
                explanation=reason,
            )
            self._history.append(terminal)
            del self._active[key]
            self._events_by_prediction.pop(prediction_id, None)
            return terminal
        return None

    def disable(self, *, reason: str = "master toggle disabled") -> None:
        for proposal in self._active.values():
            self._history.append(
                replace(proposal, state="cancelled", explanation=reason)
            )
        self._active.clear()

    def _new_id(self) -> str:
        self._sequence += 1
        return f"cue-{self._sequence}"


def _cue_key(event: PredictedMusicalEvent) -> str:
    target = event.target_function or event.target_section or event.direction or ""
    musical_target = (
        f"bar-{event.target.target_bar_index}:beat-{event.target.target_beat_index}"
        if event.target is not None
        else f"time-{round(event.target_t, 1)}"
    )
    return f"{event.event_type}:{target}:{musical_target}"
