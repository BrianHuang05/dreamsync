"""Spatial editor controller."""

from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import replace
from pathlib import Path

from dreamsync.groups.models import GroupDefinition, normalize_group_id
from dreamsync.gui.models.spatial_scene import (
    DIRECTION_VECTORS,
    SPATIAL_STEP,
    ChainValidation,
    SceneNode,
    chain_center,
    group_section_chains,
    validate_layout,
)
from dreamsync.gui.services.device_service import DeviceService


@dataclass
class SpatialController:
    service: DeviceService
    config_path: Path | None = None
    nodes: dict[str, SceneNode] = field(default_factory=dict)
    selected_key: str = ""
    group_definitions: tuple[GroupDefinition, ...] = ()
    device_groups: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def load(self, path: Path) -> list[SceneNode]:
        self.config_path = path
        self.group_definitions = self.service.load_groups(path)
        entries = self.service.load_scene(path)
        self.device_groups = {}
        for entry in entries:
            if entry.is_section:
                self.device_groups.setdefault(
                    entry.address,
                    tuple(entry.inherited_groups),
                )
            else:
                self.device_groups[entry.address] = tuple(entry.groups)
        self.nodes = {
            entry.key: SceneNode(
                key=entry.key,
                label=entry.name,
                x=entry.x,
                y=entry.y,
                z=entry.z,
                address=entry.address,
                physical_name=entry.physical_name,
                section_index=entry.section_index,
                section_count=entry.section_count,
                is_section=entry.is_section,
                groups=entry.groups,
                exclude_groups=entry.exclude_groups,
                inherited_groups=entry.inherited_groups,
            )
            for entry in entries
        }
        self.selected_key = next(iter(self.nodes), "")
        if self.selected_key:
            self.select(self.selected_key)
        return self.snapshot()

    def snapshot(self) -> list[SceneNode]:
        return list(self.nodes.values())

    def select(self, key: str) -> SceneNode | None:
        if key not in self.nodes:
            return None
        self.selected_key = key
        self.nodes = {
            node_key: replace(node, selected=(node_key == key))
            for node_key, node in self.nodes.items()
        }
        return self.nodes[key]

    def update_position(self, key: str, *, x: float, y: float, z: float | None = None) -> SceneNode:
        node = self.nodes[key]
        next_node = replace(
            node,
            x=float(x),
            y=float(y),
            z=node.z if z is None else float(z),
        )
        self.nodes[key] = next_node
        return next_node

    def chain_for_node(self, key: str) -> list[SceneNode]:
        node = self.nodes.get(key)
        if node is None or not node.is_section:
            return []
        return group_section_chains(self.nodes.values()).get(node.chain_key, [])

    def chain_for_address(self, address: str) -> list[SceneNode]:
        return group_section_chains(self.nodes.values()).get(address, [])

    def selected_center(self) -> tuple[float, float, float]:
        chain = self.chain_for_node(self.selected_key)
        if chain:
            return chain_center(chain)
        node = self.nodes.get(self.selected_key)
        if node is None:
            return (0.0, 0.0, 0.0)
        return (node.x, node.y, node.z)

    def move_chain(self, address: str, dx: float, dy: float, dz: float) -> list[SceneNode]:
        chain = self.chain_for_address(address)
        positions = [
            (node.x + float(dx), node.y + float(dy), node.z + float(dz))
            for node in chain
        ]
        if any(
            value < -1.0 or value > 1.0
            for position in positions
            for value in position
        ):
            raise ValueError("Moving the strip would place a section outside the room bounds.")
        for node, position in zip(chain, positions):
            self.nodes[node.key] = replace(
                node,
                x=position[0],
                y=position[1],
                z=position[2],
            )
        return self.chain_for_address(address)

    def set_chain_center(self, address: str, x: float, y: float, z: float) -> list[SceneNode]:
        chain = self.chain_for_address(address)
        current_x, current_y, current_z = chain_center(chain)
        return self.move_chain(address, x - current_x, y - current_y, z - current_z)

    def orient_chain(
        self,
        address: str,
        direction: str,
        *,
        center: tuple[float, float, float] | None = None,
        step: float = SPATIAL_STEP,
    ) -> list[SceneNode]:
        chain = self.chain_for_address(address)
        if not chain:
            return []
        if direction not in DIRECTION_VECTORS:
            raise ValueError(f"Unknown strip direction: {direction}")
        center_x, center_y, center_z = center or chain_center(chain)
        vector_x, vector_y, vector_z = DIRECTION_VECTORS[direction]
        midpoint = (len(chain) - 1) / 2.0
        positions: list[tuple[float, float, float]] = []
        for index in range(len(chain)):
            offset = (index - midpoint) * step
            position = (
                center_x + (vector_x * offset),
                center_y + (vector_y * offset),
                center_z + (vector_z * offset),
            )
            if any(value < -1.0 or value > 1.0 for value in position):
                raise ValueError("The oriented strip would extend outside the room bounds.")
            positions.append(position)
        for node, position in zip(chain, positions):
            self.nodes[node.key] = replace(node, x=position[0], y=position[1], z=position[2])
        return self.chain_for_address(address)

    def reverse_chain(self, address: str) -> list[SceneNode]:
        chain = self.chain_for_address(address)
        positions = [(node.x, node.y, node.z) for node in reversed(chain)]
        for node, position in zip(chain, positions):
            self.nodes[node.key] = replace(node, x=position[0], y=position[1], z=position[2])
        return self.chain_for_address(address)

    def validate_layout(self) -> list[ChainValidation]:
        return validate_layout(self.nodes.values())

    def save(self) -> None:
        if self.config_path is None:
            raise ValueError("No config path loaded")
        self.service.save_scene(
            self.config_path,
            {key: (node.x, node.y, node.z) for key, node in self.nodes.items()},
            group_definitions=self.group_definitions,
            device_groups=self.device_groups,
            section_groups={
                key: node.groups
                for key, node in self.nodes.items()
                if node.is_section
            },
            section_exclude_groups={
                key: node.exclude_groups
                for key, node in self.nodes.items()
                if node.is_section
            },
        )

    def create_group(
        self,
        group_id: str,
        name: str,
        *,
        color: str = "#64748b",
    ) -> GroupDefinition:
        normalized = normalize_group_id(group_id)
        if normalized == "all":
            raise ValueError("'all' is reserved.")
        if any(definition.id == normalized for definition in self.group_definitions):
            raise ValueError(f"Group '{normalized}' already exists.")
        display_name = str(name).strip()
        if not display_name:
            raise ValueError("Group name must not be empty.")
        definition = GroupDefinition(
            id=normalized,
            name=display_name,
            color=str(color or "#64748b"),
        )
        self.group_definitions = (*self.group_definitions, definition)
        return definition

    def rename_group(self, group_id: str, name: str) -> None:
        display_name = str(name).strip()
        if not display_name:
            raise ValueError("Group name must not be empty.")
        if not any(item.id == group_id for item in self.group_definitions):
            raise ValueError(f"Unknown group '{group_id}'.")
        self.group_definitions = tuple(
            replace(item, name=display_name) if item.id == group_id else item
            for item in self.group_definitions
        )

    def set_group_default_enabled(
        self,
        group_id: str,
        enabled: bool,
    ) -> None:
        if not any(item.id == group_id for item in self.group_definitions):
            raise ValueError(f"Unknown group '{group_id}'.")
        self.group_definitions = tuple(
            replace(item, enabled_by_default=bool(enabled))
            if item.id == group_id
            else item
            for item in self.group_definitions
        )

    def delete_group(self, group_id: str) -> None:
        if not any(item.id == group_id for item in self.group_definitions):
            raise ValueError(f"Unknown group '{group_id}'.")
        self.group_definitions = tuple(
            item for item in self.group_definitions if item.id != group_id
        )
        self.device_groups = {
            address: tuple(value for value in groups if value != group_id)
            for address, groups in self.device_groups.items()
        }
        self.nodes = {
            key: replace(
                node,
                groups=tuple(value for value in node.groups if value != group_id),
                exclude_groups=tuple(
                    value for value in node.exclude_groups if value != group_id
                ),
                inherited_groups=tuple(
                    value for value in node.inherited_groups if value != group_id
                ),
            )
            for key, node in self.nodes.items()
        }

    def set_group_membership(
        self,
        key: str,
        group_id: str,
        enabled: bool,
        *,
        whole_device: bool = False,
    ) -> None:
        if not any(item.id == group_id for item in self.group_definitions):
            raise ValueError(f"Unknown group '{group_id}'.")
        node = self.nodes[key]
        if whole_device or not node.is_section:
            current = set(self.device_groups.get(node.address, ()))
            if enabled:
                current.add(group_id)
            else:
                current.discard(group_id)
            inherited = tuple(sorted(current))
            self.device_groups[node.address] = inherited
            self.nodes = {
                item_key: replace(item, inherited_groups=inherited)
                if item.address == node.address and item.is_section
                else (
                    replace(item, groups=inherited)
                    if item.address == node.address and not item.is_section
                    else item
                )
                for item_key, item in self.nodes.items()
            }
            return
        direct = set(node.groups)
        exclusions = set(node.exclude_groups)
        if enabled:
            exclusions.discard(group_id)
            if group_id not in node.inherited_groups:
                direct.add(group_id)
        else:
            direct.discard(group_id)
            if group_id in node.inherited_groups:
                exclusions.add(group_id)
        self.nodes[key] = replace(
            node,
            groups=tuple(sorted(direct)),
            exclude_groups=tuple(sorted(exclusions)),
        )
