"""Stable, placement-aware group selection for Live Reactive output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from dreamsync.groups.models import GroupRuntimeState
from dreamsync.spatial.models import DevicePlacement


@dataclass(frozen=True)
class ReactiveGroupDescriptor:
    id: str
    tags: tuple[str, ...]
    centroid: tuple[float, float, float]
    node_count: int


@dataclass(frozen=True)
class ReactiveGroupDecision:
    target_groups: tuple[str, ...] = ()
    reason: str = "no_groups"
    confidence: float = 0.0
    revision: int = 0


class ReactiveGroupPolicy:
    """Choose groups at bounded musical boundaries without frame-to-frame churn."""

    def __init__(
        self,
        descriptors: Iterable[ReactiveGroupDescriptor],
        *,
        change_every_downbeats: int = 4,
    ) -> None:
        self.descriptors = tuple(
            descriptor
            for descriptor in descriptors
            if descriptor.node_count > 0
        )
        self.change_every_downbeats = max(1, int(change_every_downbeats))
        self._downbeats = 0
        self._cursor = -1
        self._decision = ReactiveGroupDecision()

    @property
    def decision(self) -> ReactiveGroupDecision:
        return self._decision

    def update(
        self,
        *,
        downbeat: bool,
        routes: Iterable[dict[str, object]] = (),
        runtime_state: GroupRuntimeState | None = None,
        force_boundary: bool = False,
    ) -> ReactiveGroupDecision:
        state = runtime_state or GroupRuntimeState()
        candidates = [
            descriptor
            for descriptor in self.descriptors
            if state.node_enabled(("all", descriptor.id))
            and (
                not state.solo_groups
                or descriptor.id in state.solo_groups
            )
        ]
        if not candidates:
            self._decision = ReactiveGroupDecision(
                reason="no_enabled_groups",
                revision=self._decision.revision,
            )
            return self._decision

        if downbeat:
            self._downbeats += 1
        should_change = (
            not self._decision.target_groups
            or force_boundary
            or (
                downbeat
                and self._downbeats % self.change_every_downbeats == 0
            )
        )
        if not should_change:
            return self._decision

        route_tokens = {
            str(route.get("band") or route.get("instrument") or "")
            .strip()
            .lower()
            for route in routes
        }
        scored = sorted(
            (
                self._score(descriptor, route_tokens),
                descriptor.id,
                descriptor,
            )
            for descriptor in candidates
        )
        best_score = scored[-1][0]
        best = [
            descriptor
            for score, _group_id, descriptor in scored
            if abs(score - best_score) < 1e-9
        ]
        self._cursor = (self._cursor + 1) % len(best)
        selected = best[self._cursor]
        reason = self._reason(selected, route_tokens)
        self._decision = ReactiveGroupDecision(
            target_groups=(selected.id,),
            reason=reason,
            confidence=max(0.25, min(1.0, 0.5 + best_score / 4.0)),
            revision=self._decision.revision + 1,
        )
        return self._decision

    @staticmethod
    def _score(
        descriptor: ReactiveGroupDescriptor,
        route_tokens: set[str],
    ) -> float:
        _x, y, z = descriptor.centroid
        tags = set(descriptor.tags)
        score = 0.0
        if route_tokens & {"sub", "bass", "kick"}:
            score += -y
        if route_tokens & {"presence", "air", "vocals", "harmonic"}:
            score += y
        if route_tokens & {"drums", "percussive", "kick"}:
            score += 0.7 if "strip" in tags or "accent" in tags else 0.0
        if route_tokens & {"vocals"}:
            score += 0.35 if "primary" in tags or z <= 0.0 else 0.0
        if "alternating" in tags:
            score += 0.1
        return score

    @staticmethod
    def _reason(
        descriptor: ReactiveGroupDescriptor,
        route_tokens: set[str],
    ) -> str:
        if route_tokens & {"sub", "bass", "kick"}:
            return "low_frequency_spatial_bias"
        if route_tokens & {"presence", "air", "vocals", "harmonic"}:
            return "high_frequency_spatial_bias"
        if "alternating" in descriptor.tags:
            return "alternating_group_rotation"
        return "stable_group_rotation"


def descriptors_from_adapter(adapter: object) -> tuple[ReactiveGroupDescriptor, ...]:
    definitions = tuple(getattr(adapter, "_group_definitions", ()) or ())
    if not definitions:
        return ()
    samples: dict[str, list[tuple[float, float, float]]] = {
        definition.id: [] for definition in definitions
    }
    devices = tuple(getattr(adapter, "devices", ()) or ())
    for device in devices:
        if not isinstance(device, tuple) or len(device) < 5:
            continue
        placement = device[4]
        if not isinstance(placement, DevicePlacement):
            continue
        if placement.sections:
            for section in placement.sections:
                memberships = (
                    set(placement.groups) | set(section.groups)
                ) - set(section.exclude_groups)
                for group_id in memberships:
                    if group_id in samples:
                        samples[group_id].append(
                            (section.x, section.y, section.z)
                        )
        else:
            for group_id in placement.groups:
                if group_id in samples:
                    samples[group_id].append(
                        (placement.x, placement.y, placement.z)
                    )
    descriptors: list[ReactiveGroupDescriptor] = []
    for definition in definitions:
        points = samples[definition.id]
        if not points:
            continue
        count = float(len(points))
        descriptors.append(
            ReactiveGroupDescriptor(
                id=definition.id,
                tags=definition.tags,
                centroid=(
                    sum(point[0] for point in points) / count,
                    sum(point[1] for point in points) / count,
                    sum(point[2] for point in points) / count,
                ),
                node_count=len(points),
            )
        )
    return tuple(descriptors)
