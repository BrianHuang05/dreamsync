"""Timeline Assembler — combine arc weights, treatments, and transitions into a ShowTimeline."""

from __future__ import annotations

from dreamsync.analyzer.features import FeatureRow
from dreamsync.analyzer.models import SongStructure
from dreamsync.analyzer.phrases import Phrase
from dreamsync.compiler.arc import ArcWeight
from dreamsync.compiler.treatments import Treatment
from dreamsync.compiler.transitions import TransitionPlan
from dreamsync.show.models import ShowCue, ShowTimeline


class TimelineAssembler:
    def assemble(
        self,
        structure: SongStructure,
        arc_weights: list[ArcWeight],
        treatments: list[Treatment],
        transition_plans: list[TransitionPlan],
        features: list[FeatureRow] | None = None,
    ) -> ShowTimeline:
        """Build a ShowTimeline from the compiler's intermediate outputs."""
        sections = structure.sections
        n = len(sections)
        if len(arc_weights) != n or len(treatments) != n or len(transition_plans) != n:
            raise ValueError(
                f"Input length mismatch: sections={n}, "
                f"arc_weights={len(arc_weights)}, "
                f"treatments={len(treatments)}, "
                f"transition_plans={len(transition_plans)}"
            )

        cues: list[ShowCue] = []
        for i in range(n):
            # D2: outro gets intensity_start ramp
            intensity_start = None
            if sections[i].label == "outro" and i > 0:
                intensity_start = arc_weights[i - 1].final_intensity

            cues.append(ShowCue(
                t=sections[i].start_t,
                render_mode=treatments[i].render_mode,
                color_palette=treatments[i].color_palette,
                intensity=arc_weights[i].final_intensity,
                speed=treatments[i].speed,
                params=treatments[i].params,
                transition=transition_plans[i].transition,
                transition_beats=transition_plans[i].transition_beats,
                intensity_start=intensity_start,
            ))

            # Insert micro-cues for phrases within this section
            if structure.phrases:
                section_phrases = [
                    p for p in structure.phrases if p.parent_section_index == i
                ]
                section_events = [
                    e for e in structure.instrument_events
                    if sections[i].start_t <= e.t < sections[i].end_t
                ]

                for phrase in section_phrases[1:]:  # skip first (covered by section cue)
                    micro_cue = self._build_micro_cue(
                        phrase, treatments[i], arc_weights[i], section_events,
                    )
                    if micro_cue is not None:
                        cues.append(micro_cue)

        # D1: Insert fade-to-black cue after last musical beat
        if features:
            last_beat = self._find_last_musical_beat(
                structure.beat_grid.beat_times, features,
            )
            if last_beat is not None:
                fade_duration = min(4.0, structure.duration - last_beat)
                if fade_duration > 0.5 and cues:
                    last_cue = cues[-1]
                    fade_beats = min(8, int(fade_duration * structure.bpm / 60))
                    cues.append(ShowCue(
                        t=last_beat,
                        render_mode="solid",
                        color_palette=last_cue.color_palette,
                        intensity=0.0,
                        speed=0.0,
                        params={},
                        transition="fade",
                        transition_beats=max(1, fade_beats),
                    ))

        # Sort by time
        cues.sort(key=lambda c: c.t)

        return ShowTimeline(
            song_path=structure.path,
            duration=structure.duration,
            bpm=structure.bpm,
            time_signature=structure.time_signature,
            beat_times=structure.beat_grid.beat_times,
            downbeat_times=structure.beat_grid.downbeat_times,
            cues=tuple(cues),
            metadata=structure.metadata,
        )

    def _find_last_musical_beat(
        self,
        beat_times: tuple[float, ...],
        features: list[FeatureRow],
        energy_threshold: float = 0.10,
    ) -> float | None:
        """Find the last beat where surrounding energy exceeds threshold."""
        if not beat_times or not features:
            return None

        for beat_t in reversed(beat_times):
            nearby_energy = [
                f.energy for f in features
                if abs(f.t - beat_t) <= 0.5
            ]
            if nearby_energy and sum(nearby_energy) / len(nearby_energy) > energy_threshold:
                return beat_t

        return None

    def _build_micro_cue(
        self,
        phrase: Phrase,
        treatment: Treatment,
        arc_weight: ArcWeight,
        section_events: list,
    ) -> ShowCue | None:
        """Build a micro-cue for a phrase within a section."""
        base_intensity = arc_weight.final_intensity

        # Check for instrument events at this phrase boundary
        events_at_phrase = [e for e in section_events if abs(e.t - phrase.start_t) < 0.1]
        event_types = {e.event_type for e in events_at_phrase}

        render_mode = treatment.render_mode
        intensity = base_intensity
        speed = treatment.speed
        params = dict(treatment.params)

        if "kick_enter" in event_types:
            intensity = min(1.0, base_intensity + 0.15)
            render_mode = "pulse"
        elif phrase.phrase_type == "build":
            intensity = min(1.0, base_intensity + 0.20)
        elif phrase.phrase_type == "breakdown":
            intensity = max(0.05, base_intensity - 0.20)
            render_mode = "breathe"
        elif phrase.phrase_type == "steady":
            # Steady phrase: minor variation to prevent staleness
            pass
        elif phrase.phrase_type == "drop":
            intensity = min(1.0, base_intensity + 0.10)

        return ShowCue(
            t=phrase.start_t,
            render_mode=render_mode,
            color_palette=treatment.color_palette,
            intensity=round(intensity, 4),
            speed=speed,
            params=params,
            transition="fade",
            transition_beats=2,
        )
