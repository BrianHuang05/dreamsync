"""Spatial editor controller."""

from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import replace
from pathlib import Path

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

    def load(self, path: Path) -> list[SceneNode]:
        self.config_path = path
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
            )
            for entry in self.service.load_scene(path)
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
        )
