"""Deterministic abstention and conflict arbitration for predicted events."""

from __future__ import annotations

from collections import defaultdict

from .models import PredictedMusicalEvent


_PRIORITY = {
    "section_transition": 9,
    "chorus_entrance": 8,
    "section_repeat": 7,
    "section_entrance": 6,
    "build_release": 5,
    "harmonic_resolution": 5,
    "phrase_boundary": 4,
    "verse_repeat": 3,
    "chord_change": 2,
    "mood_change": 1,
    "bar_marker": 0,
}


def arbitrate_predictions(
    events: tuple[PredictedMusicalEvent, ...],
    *,
    target_tolerance: float = 0.08,
    maximum_events_per_target: int = 3,
) -> tuple[PredictedMusicalEvent, ...]:
    buckets: dict[int, list[PredictedMusicalEvent]] = defaultdict(list)
    scale = max(1e-6, float(target_tolerance))
    for event in events:
        if not event.evidence or event.probability <= 0.0:
            continue
        bucket = (
            event.target.target_bar_index
            if event.target is not None
            else int(round(event.target_t / scale))
        )
        buckets[bucket].append(event)
    selected: list[PredictedMusicalEvent] = []
    for bucket in sorted(buckets):
        ranked = sorted(
            buckets[bucket],
            key=lambda event: (
                -event.probability,
                -_PRIORITY.get(event.event_type, 0),
                event.prediction_id,
            ),
        )
        seen_types: set[str] = set()
        for event in ranked:
            if event.event_type in seen_types:
                continue
            if any(_conflicts(event, kept) for kept in selected[-maximum_events_per_target:]):
                continue
            selected.append(event)
            seen_types.add(event.event_type)
            if len(seen_types) >= maximum_events_per_target:
                break
    return tuple(selected)


def _conflicts(
    left: PredictedMusicalEvent, right: PredictedMusicalEvent
) -> bool:
    if abs(left.target_t - right.target_t) > max(
        left.timing_sigma, right.timing_sigma
    ):
        return False
    if left.event_type == right.event_type:
        return True
    structural_large = {
        "section_entrance",
        "section_repeat",
        "section_transition",
        "chorus_entrance",
        "build_release",
    }
    if left.event_type in structural_large and right.event_type in structural_large:
        return True
    # A resolution and ordinary chord change may describe the same event.
    if {left.event_type, right.event_type} == {
        "harmonic_resolution",
        "chord_change",
    }:
        return (
            left.target_function is not None
            and right.target_function is not None
            and left.target_function != right.target_function
        )
    return False
