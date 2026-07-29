"""Immutable models for logical output groups."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass


ALL_GROUP_ID = "all"
_GROUP_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def normalize_group_id(value: object) -> str:
    """Return a canonical group ID or raise a useful validation error."""

    group_id = str(value or "").strip().lower()
    if not group_id:
        raise ValueError("Group ID must not be empty.")
    if not _GROUP_ID_PATTERN.fullmatch(group_id):
        raise ValueError(
            f"Invalid group ID '{value}'; use lowercase letters, numbers, '-' or '_'."
        )
    return group_id


def normalize_group_ids(
    values: object,
    *,
    field_name: str = "groups",
) -> tuple[str, ...]:
    """Normalize a YAML/JSON group list while preserving stable order."""

    if values is None:
        return ()
    if isinstance(values, str) or not isinstance(values, Iterable):
        raise ValueError(f"{field_name} must be a list of group IDs.")
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        group_id = normalize_group_id(value)
        if group_id in seen:
            continue
        seen.add(group_id)
        result.append(group_id)
    return tuple(result)


@dataclass(frozen=True)
class GroupDefinition:
    id: str
    name: str
    color: str = "#64748b"
    enabled_by_default: bool = True
    tags: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, object]:
        data: dict[str, object] = {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "enabled_by_default": self.enabled_by_default,
        }
        if self.tags:
            data["tags"] = list(self.tags)
        return data


@dataclass(frozen=True)
class GroupSelector:
    target_groups: tuple[str, ...] = ()
    exclude_groups: tuple[str, ...] = ()
    target_match: str = "any"
    untargeted_behavior: str = "blackout"

    @property
    def targets_all(self) -> bool:
        return not self.target_groups or ALL_GROUP_ID in self.target_groups

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, object] | None,
        *,
        default_untargeted_behavior: str = "blackout",
    ) -> "GroupSelector":
        raw = data or {}
        target_match = str(raw.get("target_match", "any") or "any").strip().lower()
        if target_match not in {"any", "all"}:
            raise ValueError("target_match must be 'any' or 'all'.")
        behavior = str(
            raw.get("untargeted_behavior", default_untargeted_behavior)
            or default_untargeted_behavior
        ).strip().lower()
        if behavior not in {"blackout", "preserve_base", "ignore"}:
            raise ValueError(
                "untargeted_behavior must be 'blackout', 'preserve_base', or 'ignore'."
            )
        return cls(
            target_groups=normalize_group_ids(
                raw.get("target_groups"), field_name="target_groups"
            ),
            exclude_groups=normalize_group_ids(
                raw.get("exclude_groups"), field_name="exclude_groups"
            ),
            target_match=target_match,
            untargeted_behavior=behavior,
        )

    def matches(self, memberships: Iterable[str]) -> bool:
        member_set = set(memberships)
        positive = (
            True
            if self.targets_all
            else (
                all(group_id in member_set for group_id in self.target_groups)
                if self.target_match == "all"
                else any(group_id in member_set for group_id in self.target_groups)
            )
        )
        return positive and not any(
            group_id in member_set for group_id in self.exclude_groups
        )


@dataclass(frozen=True)
class GroupRuntimeState:
    disabled_groups: frozenset[str] = frozenset()
    solo_groups: frozenset[str] = frozenset()
    enabled_groups: frozenset[str] = frozenset()

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, object] | None,
        *,
        default_disabled: Iterable[str] = (),
    ) -> "GroupRuntimeState":
        raw = data or {}
        enabled_groups = frozenset(
            normalize_group_ids(
                raw.get("enabled_groups"),
                field_name="enabled_groups",
            )
        )
        return cls(
            disabled_groups=frozenset(
                (
                    *normalize_group_ids(default_disabled),
                    *normalize_group_ids(
                        raw.get("disabled_groups"), field_name="disabled_groups"
                    ),
                )
            )
            - enabled_groups,
            solo_groups=frozenset(
                normalize_group_ids(raw.get("solo_groups"), field_name="solo_groups")
            ),
            enabled_groups=enabled_groups,
        )

    def contribution_enabled(self, selector: GroupSelector) -> bool:
        targets = (
            frozenset({ALL_GROUP_ID})
            if selector.targets_all
            else frozenset(selector.target_groups)
        )
        if targets and targets.issubset(self.disabled_groups):
            return False
        if self.solo_groups and ALL_GROUP_ID not in targets:
            return bool(targets & self.solo_groups)
        return True

    def node_enabled(self, memberships: Iterable[str]) -> bool:
        """Return whether a logical node remains available under group toggles."""

        groups = frozenset(memberships) - {ALL_GROUP_ID}
        if self.solo_groups:
            return bool(groups & self.solo_groups)
        if not groups:
            return ALL_GROUP_ID not in self.disabled_groups
        return bool(groups - self.disabled_groups)


def parse_group_definitions(raw: object) -> tuple[GroupDefinition, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("Top-level 'groups' must be a list.")
    definitions: list[GroupDefinition] = []
    seen: set[str] = set()
    for index, value in enumerate(raw):
        if not isinstance(value, Mapping):
            raise ValueError(f"Group entry {index} must be a mapping.")
        group_id = normalize_group_id(value.get("id"))
        if group_id == ALL_GROUP_ID:
            raise ValueError("'all' is reserved and cannot be defined.")
        if group_id in seen:
            raise ValueError(f"Duplicate group ID '{group_id}'.")
        seen.add(group_id)
        name = str(value.get("name", "") or "").strip()
        if not name:
            raise ValueError(f"Group '{group_id}' must have a display name.")
        enabled_raw = value.get("enabled_by_default", True)
        if not isinstance(enabled_raw, bool):
            raise ValueError(
                f"Group '{group_id}' enabled_by_default must be a boolean."
            )
        definitions.append(
            GroupDefinition(
                id=group_id,
                name=name,
                color=str(value.get("color", "#64748b") or "#64748b"),
                enabled_by_default=enabled_raw,
                tags=normalize_group_ids(
                    value.get("tags"), field_name=f"groups[{index}].tags"
                ),
            )
        )
    return tuple(definitions)


def validate_memberships(
    definitions: Iterable[GroupDefinition],
    memberships: Iterable[str],
    *,
    field_name: str,
) -> None:
    known = {definition.id for definition in definitions}
    unknown = sorted(
        group_id
        for group_id in memberships
        if group_id != ALL_GROUP_ID and group_id not in known
    )
    if unknown:
        raise ValueError(
            f"{field_name} references unknown group(s): {', '.join(unknown)}."
        )
