"""Narrative Arc Planner — shape intensity across song sections for show compilation."""

from __future__ import annotations

from dataclasses import dataclass

from dreamsync.analyzer.sections import Section


@dataclass(frozen=True)
class ArcWeight:
    section_index: int
    base_energy: float          # section.energy_mean (unchanged)
    arc_multiplier: float       # 0.0-1.0 (position-based scaling)
    final_intensity: float      # base_energy * arc_multiplier, clamped to [0.05, 1.0]


class NarrativeArcPlanner:
    def __init__(
        self,
        intro_intensity: float = 0.15,
        outro_intensity: float = 0.10,
        peak_boost: float = 1.2,
        buildup_ramp: float = 0.15,
        bridge_reduction: float = 0.80,
    ) -> None:
        self._intro_intensity = intro_intensity
        self._outro_intensity = outro_intensity
        self._peak_boost = peak_boost
        self._buildup_ramp = buildup_ramp
        self._bridge_reduction = bridge_reduction

    def plan(self, sections: tuple[Section, ...]) -> list[ArcWeight]:
        """Compute arc weights for each section."""
        if not sections:
            return []

        # Find the index of the last chorus or drop
        peak_index = -1
        for i, s in enumerate(sections):
            if s.label in ("chorus", "drop"):
                peak_index = i

        label_counts: dict[str, int] = {}
        result: list[ArcWeight] = []

        for i, section in enumerate(sections):
            occurrence = label_counts.get(section.label, 0)
            arc_multiplier = 1.0

            # Buildup ramp: each repeated label occurrence gets +buildup_ramp
            arc_multiplier += occurrence * self._buildup_ramp

            # Intro cap
            if section.label == "intro":
                arc_multiplier = min(arc_multiplier, self._intro_intensity / max(section.energy_mean, 0.01))

            # Outro cap
            if section.label == "outro":
                arc_multiplier = min(arc_multiplier, self._outro_intensity / max(section.energy_mean, 0.01))

            # Bridge / breakdown dip
            if section.label in ("bridge", "breakdown"):
                arc_multiplier *= self._bridge_reduction

            # Final chorus/drop boost
            if i == peak_index:
                arc_multiplier *= self._peak_boost

            final_intensity = section.energy_mean * arc_multiplier
            final_intensity = max(0.05, min(1.0, final_intensity))

            result.append(ArcWeight(
                section_index=i,
                base_energy=section.energy_mean,
                arc_multiplier=round(arc_multiplier, 4),
                final_intensity=round(final_intensity, 4),
            ))

            label_counts[section.label] = occurrence + 1

        return result
