"""Timeline Assembler — combine arc weights, treatments, and transitions into a ShowTimeline."""

from __future__ import annotations

from dreamsync.analyzer.models import SongStructure
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

        cues = []
        for i in range(n):
            cues.append(ShowCue(
                t=sections[i].start_t,
                render_mode=treatments[i].render_mode,
                color_palette=treatments[i].color_palette,
                intensity=arc_weights[i].final_intensity,
                speed=treatments[i].speed,
                params=treatments[i].params,
                transition=transition_plans[i].transition,
                transition_beats=transition_plans[i].transition_beats,
            ))

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
