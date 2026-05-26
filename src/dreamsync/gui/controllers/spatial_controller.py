"""Spatial editor controller."""

from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import replace
from pathlib import Path

from dreamsync.gui.models.spatial_scene import SceneNode
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
        next_node = SceneNode(
            key=node.key,
            label=node.label,
            x=float(x),
            y=float(y),
            z=node.z if z is None else float(z),
            color=node.color,
            selected=node.selected,
            address=node.address,
            physical_name=node.physical_name,
            section_index=node.section_index,
            section_count=node.section_count,
            is_section=node.is_section,
        )
        self.nodes[key] = next_node
        return next_node

    def save(self) -> None:
        if self.config_path is None:
            raise ValueError("No config path loaded")
        self.service.save_scene(
            self.config_path,
            {key: (node.x, node.y, node.z) for key, node in self.nodes.items()},
        )
