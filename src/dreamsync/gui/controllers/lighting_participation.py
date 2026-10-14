"""Session-scoped static/dynamic participation for lights and groups.

Participation (dynamic lighting vs. the shared static background color) lives
only for the lifetime of the application session. It is never written to the
device config or to a Show file, so every Show acts on every light unless a
light or group was explicitly set static earlier in the same app session.

Changes are kept as an ordered log of (scope, target) entries. Resolution
replays the log against the *current* group memberships, so the newest change
always wins regardless of whether it targeted a single light or a group.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from dreamsync.groups.models import ALL_GROUP_ID

NODE_SCOPE = "node"
GROUP_SCOPE = "group"


@dataclass(frozen=True)
class ParticipationChange:
    scope: str
    target: str
    enabled: bool


class LightingParticipation:
    """Ordered, newest-wins participation log for lights and groups."""

    def __init__(self) -> None:
        self._changes: list[ParticipationChange] = []

    @property
    def changes(self) -> tuple[ParticipationChange, ...]:
        return tuple(self._changes)

    def clear(self) -> None:
        self._changes.clear()

    def set_node(self, key: str, enabled: bool) -> None:
        self._record(NODE_SCOPE, str(key), bool(enabled))

    def set_group(self, group_id: str, enabled: bool) -> None:
        self._record(GROUP_SCOPE, str(group_id).strip().lower(), bool(enabled))

    def _record(self, scope: str, target: str, enabled: bool) -> None:
        # Only the most recent change per target can matter, so compact the log.
        self._changes = [
            change for change in self._changes
            if not (change.scope == scope and change.target == target)
        ]
        self._changes.append(ParticipationChange(scope, target, enabled))

    @staticmethod
    def _group_matches(group_id: str, memberships: Iterable[str]) -> bool:
        return group_id == ALL_GROUP_ID or group_id in set(memberships)

    def resolve(self, memberships: Mapping[str, Iterable[str]]) -> dict[str, bool]:
        """Return the enabled flag for every node key in *memberships*.

        Every light starts dynamic. The log is replayed in order so a later
        change overrides an earlier one, whether it addressed the node directly
        or through one of its groups.
        """
        resolved = {key: True for key in memberships}
        for change in self._changes:
            if change.scope == NODE_SCOPE:
                if change.target in resolved:
                    resolved[change.target] = change.enabled
                continue
            for key, groups in memberships.items():
                if self._group_matches(change.target, groups):
                    resolved[key] = change.enabled
        return resolved

    def group_state(
        self,
        group_id: str,
        memberships: Mapping[str, Iterable[str]],
        resolved: Mapping[str, bool] | None = None,
    ) -> str:
        """Summarize a group's members: dynamic, static, mixed, or empty."""
        values = resolved if resolved is not None else self.resolve(memberships)
        member_flags = [
            values[key]
            for key, groups in memberships.items()
            if self._group_matches(group_id, groups)
        ]
        if not member_flags:
            return "empty"
        if all(member_flags):
            return "dynamic"
        if not any(member_flags):
            return "static"
        return "mixed"
