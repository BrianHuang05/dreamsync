"""Transition Planner — decide transition type and duration for section boundaries."""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from dreamsync.analyzer.sections import Section
from dreamsync.analyzer.bpm import BeatGrid


@dataclass(frozen=True)
class TransitionPlan:
    section_index: int
    transition: str             # "cut" | "fade"
    transition_beats: int       # 0 for cut, N for fade (must be > 0 if fade)


class TransitionPlanner:
    """Decide transitions for all section boundaries using a rules table."""

    # Rules table: (outgoing_labels, incoming_labels, transition, beats)
    # None means "any". Evaluated top-to-bottom, first match wins.
    _RULES: list[tuple[set[str] | None, set[str] | None, str, int]] = [
        (None, {"intro"}, "cut", 0),                                    # Rule 1
        (None, {"drop"}, "cut", 0),                                     # Rule 2
        ({"chorus"}, {"chorus"}, "cut", 0),                             # Rule 3
        ({"verse", "bridge"}, {"chorus"}, "fade", 2),                   # Rule 4
        ({"bridge", "breakdown"}, {"chorus"}, "fade", 2),               # Rule 5
        ({"chorus", "drop"}, {"verse"}, "fade", 4),                     # Rule 7
        ({"chorus", "drop"}, {"bridge"}, "fade", 4),                    # Rule 8
        ({"chorus", "drop"}, {"breakdown"}, "fade", 4),                 # Rule 9
        ({"verse"}, {"verse"}, "fade", 4),                              # Rule 10
        (None, {"outro"}, "fade", 8),                                   # Rule 11
    ]

    def __init__(
        self,
        default_fade_beats: int = 4,
        max_fade_beats: int = 16,
    ) -> None:
        self._default_fade_beats = default_fade_beats
        self._max_fade_beats = max_fade_beats

    def plan(
        self,
        sections: tuple[Section, ...],
        beat_grid: BeatGrid,
    ) -> list[TransitionPlan]:
        """Decide transitions for all section boundaries."""
        if not sections:
            return []

        result: list[TransitionPlan] = []

        for i, section in enumerate(sections):
            # First section always gets a cut
            if i == 0:
                result.append(TransitionPlan(section_index=0, transition="cut", transition_beats=0))
                continue

            outgoing = sections[i - 1]
            incoming = section

            # Match rules
            transition = None
            beats = 0
            for out_labels, in_labels, rule_transition, rule_beats in self._RULES:
                out_match = out_labels is None or outgoing.label in out_labels
                in_match = in_labels is None or incoming.label in in_labels
                if out_match and in_match:
                    transition = rule_transition
                    beats = rule_beats
                    break

            # Default fallback
            if transition is None:
                transition = "fade"
                beats = self._default_fade_beats

            # Cap at max
            beats = min(beats, self._max_fade_beats)

            # Check beat availability
            if transition == "fade" and beats > 0:
                available = self._count_beats_in_range(
                    beat_grid, incoming.start_t, incoming.end_t
                )
                if available < beats:
                    beats = available
                if beats == 0:
                    transition = "cut"

            result.append(TransitionPlan(
                section_index=i,
                transition=transition,
                transition_beats=beats,
            ))

        return result

    @staticmethod
    def _count_beats_in_range(beat_grid: BeatGrid, start_t: float, end_t: float) -> int:
        """Count beats in [start_t, end_t) using bisect for efficiency."""
        times = beat_grid.beat_times
        left = bisect.bisect_left(times, start_t)
        right = bisect.bisect_left(times, end_t)
        return right - left
