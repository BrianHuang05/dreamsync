"""compile_show() orchestrator — wire arc, treatments, transitions, assembly into one call."""

from __future__ import annotations

from dreamsync.analyzer.features import FeatureRow
from dreamsync.analyzer.models import SongStructure
from dreamsync.compiler.arc import NarrativeArcPlanner
from dreamsync.compiler.assemble import TimelineAssembler
from dreamsync.compiler.treatments import TreatmentSelector
from dreamsync.compiler.transitions import TransitionPlanner
from dreamsync.profile import ProfileConfig
from dreamsync.show.models import ShowTimeline


def compile_show(
    structure: SongStructure,
    profile: ProfileConfig | None = None,
    *,
    seed: int | None = None,
    intro_intensity: float = 0.15,
    outro_intensity: float = 0.10,
    peak_boost: float = 1.2,
    buildup_ramp: float = 0.15,
    bridge_reduction: float = 0.80,
    default_fade_beats: int = 4,
    max_fade_beats: int = 16,
    features: list[FeatureRow] | None = None,
) -> ShowTimeline:
    """Compile a SongStructure into a ShowTimeline.

    Steps:
    1. Plan narrative arc → arc_weights
    2. Select treatments for each section → treatments
    3. Plan transitions → transition_plans
    4. Assemble timeline → ShowTimeline
    """
    # 1. Narrative arc
    arc_planner = NarrativeArcPlanner(
        intro_intensity=intro_intensity,
        outro_intensity=outro_intensity,
        peak_boost=peak_boost,
        buildup_ramp=buildup_ramp,
        bridge_reduction=bridge_reduction,
    )
    arc_weights = arc_planner.plan(structure.sections)

    # 2. Treatments
    selector = TreatmentSelector(profile, seed)
    treatments = [
        selector.select(s.mood, s.label, s.bpm) for s in structure.sections
    ]

    # 3. Transitions
    transition_planner = TransitionPlanner(default_fade_beats, max_fade_beats)
    transition_plans = transition_planner.plan(structure.sections, structure.beat_grid)

    # 4. Assemble
    assembler = TimelineAssembler()
    return assembler.assemble(structure, arc_weights, treatments, transition_plans, features=features)


def format_summary(structure: SongStructure, timeline: ShowTimeline) -> str:
    """Build a human-readable summary of a compiled show."""
    import sys
    _enc = sys.stdout.encoding or "utf-8"

    minutes = int(structure.duration // 60)
    seconds = int(structure.duration % 60)
    duration_str = f"{minutes}:{seconds:02d}"

    safe_path = structure.path.encode(_enc, errors="replace").decode(_enc)
    lines = [
        f"Show compiled: {safe_path}",
        f"  Duration:  {duration_str}",
        f"  BPM:       {structure.bpm:.1f}",
        f"  Sections:  {len(structure.sections)}",
        f"  Cues:      {len(timeline.cues)}",
        "",
        "  Time     Label     Mood    Effect         Transition  Beats  Intensity",
        "  -------- --------- ------- -------------- ---------- ------- ---------",
    ]

    for i, cue in enumerate(timeline.cues):
        t_min = int(cue.t // 60)
        t_sec = int(cue.t % 60)
        time_str = f"{t_min}:{t_sec:02d}"

        # Find the section that contains this cue's timestamp
        label = "?"
        mood = "?"
        for sec in structure.sections:
            if sec.start_t <= cue.t < sec.end_t:
                label = sec.label
                mood = sec.mood
                break

        lines.append(
            f"  {time_str:<8s} {label:<9s} {mood:<7s} "
            f"{cue.render_mode:<14s} {cue.transition:<10s} "
            f"{cue.transition_beats:<7d} {cue.intensity:.2f}"
        )

    return "\n".join(lines)
