"""Device config and spatial editor services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dreamsync.output.auto_detect import DeviceConfig, load_device_config
from dreamsync.spatial.models import parse_device_placement


@dataclass(frozen=True)
class DeviceSceneEntry:
    key: str
    name: str
    address: str
    segments: int
    x: float
    y: float
    z: float
    physical_name: str
    section_index: int | None = None
    section_count: int = 1
    protocol: str | None = None
    is_section: bool = False


class DeviceService:
    """Load and persist device spatial config data."""

    def __init__(self) -> None:
        self._yaml = None

    def _require_yaml(self):
        if self._yaml is None:
            import yaml

            self._yaml = yaml
        return self._yaml

    def load_config(self, path: Path) -> list[DeviceConfig]:
        return load_device_config(path)

    def validate_config(self, path: Path) -> list[DeviceConfig]:
        return self.load_config(path)

    def _load_raw_config(self, path: Path) -> dict[str, Any]:
        yaml = self._require_yaml()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("Device config must be a mapping")
        return data

    def _iter_device_entries(self, path: Path) -> list[dict[str, Any]]:
        data = self._load_raw_config(path)
        devices = data.get("devices", [])
        if not isinstance(devices, list):
            raise ValueError("Device config must contain a top-level devices list")
        return [entry for entry in devices if isinstance(entry, dict)]

    @staticmethod
    def _section_key(address: str, index: int) -> str:
        return f"{address}#section:{index}"

    @staticmethod
    def _should_expand_sections(entry: dict[str, Any]) -> bool:
        protocol = str(entry.get("protocol", "")).strip().lower()
        if protocol == "bulb":
            return False
        if isinstance(entry.get("sections"), list):
            return True
        return int(entry.get("segments", 1)) > 1

    def load_scene(self, path: Path) -> list[DeviceSceneEntry]:
        entries: list[DeviceSceneEntry] = []
        for entry in self._iter_device_entries(path):
            address = str(entry.get("address", ""))
            name = str(entry.get("name", address))
            protocol = str(entry.get("protocol", "")).strip().lower() or None
            segments = int(entry.get("segments", 1))
            placement = parse_device_placement(entry)
            base_x = placement.x if placement else 0.0
            base_y = placement.y if placement else 0.0
            base_z = getattr(placement, "z", 0.0) if placement else 0.0
            if self._should_expand_sections(entry):
                sections = entry.get("sections", [])
                section_map = {
                    int(section.get("index", idx)): section
                    for idx, section in enumerate(sections)
                    if isinstance(section, dict)
                } if isinstance(sections, list) else {}
                for index in range(max(1, segments)):
                    section_entry = section_map.get(index, {})
                    section_placement = parse_device_placement(section_entry) if section_entry else None
                    entries.append(
                        DeviceSceneEntry(
                            key=self._section_key(address, index),
                            name=str(section_entry.get("name", f"{name} [{index + 1}/{segments}]")),
                            address=address,
                            segments=1,
                            x=section_placement.x if section_placement else base_x,
                            y=section_placement.y if section_placement else base_y,
                            z=getattr(section_placement, "z", 0.0) if section_placement else base_z,
                            physical_name=name,
                            section_index=index,
                            section_count=max(1, segments),
                            protocol=protocol,
                            is_section=True,
                        )
                    )
                continue
            entries.append(
                DeviceSceneEntry(
                    key=address,
                    name=name,
                    address=address,
                    segments=segments,
                    x=base_x,
                    y=base_y,
                    z=base_z,
                    physical_name=name,
                    protocol=protocol,
                )
            )
        return entries

    def save_scene(self, path: Path, placements: dict[str, tuple[float, float, float]]) -> None:
        yaml = self._require_yaml()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        devices = data.get("devices", [])
        if not isinstance(devices, list):
            raise ValueError("Device config must contain a top-level devices list")

        for entry in devices:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("address", ""))
            if self._should_expand_sections(entry):
                segments = int(entry.get("segments", 1))
                prior_sections = entry.get("sections", [])
                prior_map = {
                    int(section.get("index", idx)): dict(section)
                    for idx, section in enumerate(prior_sections)
                    if isinstance(section, dict)
                } if isinstance(prior_sections, list) else {}
                section_payloads: list[dict[str, object]] = []
                section_positions: list[tuple[float, float, float]] = []
                for index in range(max(1, segments)):
                    section_key = self._section_key(key, index)
                    if section_key not in placements and index not in prior_map:
                        continue
                    if section_key in placements:
                        x, y, z = placements[section_key]
                    else:
                        prior_section = prior_map[index]
                        x = float(prior_section.get("x", 0.0))
                        y = float(prior_section.get("y", 0.0))
                        z = float(prior_section.get("z", 0.0))
                    payload = prior_map.get(index, {})
                    payload.pop("x_position", None)
                    payload.pop("y_position", None)
                    payload["index"] = index
                    payload["x"] = float(x)
                    payload["y"] = float(y)
                    payload["z"] = float(z)
                    section_payloads.append(payload)
                    section_positions.append((float(x), float(y), float(z)))
                if section_payloads:
                    entry["sections"] = section_payloads
                    avg_x = sum(x for x, _y, _z in section_positions) / len(section_positions)
                    avg_y = sum(y for _x, y, _z in section_positions) / len(section_positions)
                    avg_z = sum(z for _x, _y, z in section_positions) / len(section_positions)
                    entry.pop("x_position", None)
                    entry.pop("y_position", None)
                    entry["x"] = float(avg_x)
                    entry["y"] = float(avg_y)
                    entry["z"] = float(avg_z)
                continue

            if key not in placements:
                continue
            x, y, z = placements[key]
            entry.pop("x_position", None)
            entry.pop("y_position", None)
            entry["x"] = float(x)
            entry["y"] = float(y)
            entry["z"] = float(z)

        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
