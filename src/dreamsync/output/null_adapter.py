"""Adapters for dry-run, audio-only, and GUI simulation preview modes."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from dreamsync.output.govee_lan import MultiGoveeLanAdapter
from dreamsync.output.roles import default_device_config, infer_device_type
from dreamsync.render import RenderMode, SegmentRenderer
from dreamsync.spatial.mapper import SpatialMapper


def _section_key(address: str, index: int) -> str:
    return f"{address}#section:{index}"


def _rgb_to_hex(color: tuple[int, int, int]) -> str:
    r, g, b = color
    return f"#{r:02x}{g:02x}{b:02x}"


def _normalize_preview_color(color: tuple[int, int, int]) -> tuple[int, int, int]:
    """Clamp rendered RGB without discarding its brightness envelope."""

    r, g, b = color
    return (
        min(255, max(0, int(round(r)))),
        min(255, max(0, int(round(g)))),
        min(255, max(0, int(round(b)))),
    )


def _average_color(colors: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    if not colors:
        return (79, 127, 218)
    count = max(1, len(colors))
    return (
        int(sum(color[0] for color in colors) / count),
        int(sum(color[1] for color in colors) / count),
        int(sum(color[2] for color in colors) / count),
    )


class _PreviewDeviceAdapter:
    """In-memory stand-in for a physical device."""

    def __init__(self, address: str, *, name: str, segments: int) -> None:
        self.address = address
        self.name = name
        self.segments = max(1, int(segments))
        self.config = SimpleNamespace(device_ip=address)
        self.last_colors: list[tuple[int, int, int]] = [(79, 127, 218)] * self.segments
        self.brightness = 100
        self.powered = False

    def turn_on(self) -> None:
        self.powered = True

    def set_brightness(self, brightness: int) -> None:
        self.brightness = int(brightness)

    def send_frame(self, colors: list[tuple[int, int, int]]) -> bool:
        if colors:
            self.last_colors = list(colors)
        return True


class NullMultiAdapter:
    """Drop-in replacement for MultiGoveeLanAdapter that does nothing."""

    def __init__(self) -> None:
        self.devices: list = []
        self._ble_followers: list = []
        self._frames_sent: int = 0

    def activate(self, brightness: int = 100) -> None:
        pass

    def deactivate(self) -> None:
        pass

    def send_frame(self, t, intent, beat=False, params=None) -> bool:
        self._frames_sent += 1
        return True

    def send_spatial_scene(self, t, scene, *, beat=False, base_intent=None) -> bool:
        self._frames_sent += 1
        return True

    def send_baked_frame(
        self,
        t: float,
        node_colors: dict[str, str],
        *,
        fallback_color: str = "#000000",
    ) -> bool:
        self._frames_sent += 1
        return True

    def preview_snapshot(self) -> dict[str, Any]:
        return {"node_colors": {}, "frames_sent": self._frames_sent}


class PreviewMirrorAdapter:
    """Send real output normally while mirroring frames into a GUI simulation."""

    def __init__(self, output_adapter: Any, preview_adapter: "SimulationMultiAdapter") -> None:
        self._output_adapter = output_adapter
        self._preview_adapter = preview_adapter

    @property
    def devices(self):
        return getattr(self._output_adapter, "devices", [])

    @property
    def device_status_label(self) -> str:
        label = getattr(self._output_adapter, "device_status_label", "hardware output")
        return f"{label} + GUI simulation mirror"

    def activate(self, brightness: int = 100) -> None:
        self._output_adapter.activate(brightness=brightness)
        self._preview_adapter.activate(brightness=brightness)

    def deactivate(self) -> None:
        try:
            self._output_adapter.deactivate()
        finally:
            self._preview_adapter.deactivate()

    def send_frame(self, t, intent, beat=False, params=None) -> bool:
        preview_sent = self._preview_adapter.send_frame(t, intent, beat=beat, params=params)
        output_sent = self._output_adapter.send_frame(t, intent, beat=beat, params=params)
        return bool(preview_sent or output_sent)

    def send_spatial_scene(self, t, scene, *, beat=False, base_intent=None) -> bool:
        preview_sent = self._preview_adapter.send_spatial_scene(
            t, scene, beat=beat, base_intent=base_intent
        )
        output_sent = self._output_adapter.send_spatial_scene(
            t, scene, beat=beat, base_intent=base_intent
        )
        return bool(preview_sent or output_sent)

    def send_baked_frame(self, t: float, node_colors: dict[str, str], *, fallback_color: str = "#000000") -> bool:
        preview_sent = self._preview_adapter.send_baked_frame(
            t, node_colors, fallback_color=fallback_color
        )
        output_sent = self._output_adapter.send_baked_frame(
            t, node_colors, fallback_color=fallback_color
        )
        return bool(preview_sent or output_sent)

    def prepare_spatial_cue(self, key, intent, *, params=None) -> bool:
        preview_prepare = getattr(self._preview_adapter, "prepare_spatial_cue", None)
        output_prepare = getattr(self._output_adapter, "prepare_spatial_cue", None)
        preview_prepared = bool(preview_prepare(key, intent, params=params)) if callable(preview_prepare) else False
        output_prepared = bool(output_prepare(key, intent, params=params)) if callable(output_prepare) else False
        return preview_prepared or output_prepared

    def clear_prepared_spatial_cues(self) -> None:
        for adapter in (self._preview_adapter, self._output_adapter):
            clear = getattr(adapter, "clear_prepared_spatial_cues", None)
            if callable(clear):
                clear()

    def preview_snapshot(self) -> dict[str, Any]:
        return self._preview_adapter.preview_snapshot()

    def __getattr__(self, name: str):
        return getattr(self._output_adapter, name)


class SimulationMultiAdapter(MultiGoveeLanAdapter):
    """Preview-only adapter that renders live device colors into GUI nodes."""

    device_status_label = "simulation mode (no connected devices)"

    def __init__(
        self,
        devices: list[tuple],
        *,
        node_keys: dict[str, list[str]],
        spatial_mapper: SpatialMapper | None = None,
    ) -> None:
        super().__init__(devices, ble_followers=[], spatial_mapper=spatial_mapper)
        self._frames_sent = 0
        self._node_keys = {key: list(value) for key, value in node_keys.items()}
        self._preview_colors: dict[str, str] = {}
        self._capture_preview_colors()

    @classmethod
    def from_configs(
        cls,
        configs: list[Any],
        *,
        render_mode: RenderMode = RenderMode.SCROLL,
        mirror: bool = True,
    ) -> "SimulationMultiAdapter":
        spatial_enabled = False
        device_tuples: list[tuple] = []
        node_keys: dict[str, list[str]] = {}

        for cfg in configs:
            device_type = infer_device_type(int(getattr(cfg, "segments", 1)), str(getattr(cfg, "name", "")))
            default_role, default_bs = default_device_config(device_type)
            device_role = getattr(cfg, "role", None)
            if device_role:
                device_role = type(default_role)(device_role)
            else:
                device_role = default_role
            brightness_scale = getattr(cfg, "brightness_scale", None)
            device_bs = float(brightness_scale) if brightness_scale is not None else default_bs
            placement = getattr(cfg, "placement", None)
            if placement is not None:
                spatial_enabled = True

            segments = max(1, int(getattr(cfg, "segments", 1)))
            adapter = _PreviewDeviceAdapter(
                str(getattr(cfg, "address", "")),
                name=str(getattr(cfg, "name", getattr(cfg, "address", "device"))),
                segments=segments,
            )
            renderer = SegmentRenderer(
                segments=segments,
                mode=render_mode,
                mirror=mirror,
                device_type=device_type.value,
            )
            device_tuples.append((adapter, renderer, device_role, device_bs, placement))

            protocol = str(getattr(cfg, "protocol", "") or "").strip().lower()
            if protocol != "bulb" and segments > 1:
                node_keys[adapter.address] = [_section_key(adapter.address, index) for index in range(segments)]
            else:
                node_keys[adapter.address] = [adapter.address]

        mapper = SpatialMapper(enabled=spatial_enabled) if spatial_enabled else None
        return cls(device_tuples, node_keys=node_keys, spatial_mapper=mapper)

    def activate(self, brightness: int = 100) -> None:
        for adapter, _renderer, _role, _bs, _placement in self.devices:
            adapter.turn_on()
            adapter.set_brightness(brightness)

    def deactivate(self) -> None:
        pass

    def send_frame(self, t, intent, beat=False, params=None) -> bool:
        sent = super().send_frame(t, intent, beat=beat, params=params)
        if sent:
            self._frames_sent += 1
            self._capture_preview_colors()
        return sent

    def send_spatial_scene(self, t, scene, *, beat=False, base_intent=None) -> bool:
        sent = super().send_spatial_scene(t, scene, beat=beat, base_intent=base_intent)
        if sent:
            self._frames_sent += 1
            self._capture_preview_colors()
        return sent

    def send_baked_frame(
        self,
        t: float,
        node_colors: dict[str, str],
        *,
        fallback_color: str = "#000000",
    ) -> bool:
        sent = super().send_baked_frame(t, node_colors, fallback_color=fallback_color)
        if sent:
            self._frames_sent += 1
            self._capture_preview_colors()
        return sent

    def preview_snapshot(self) -> dict[str, Any]:
        self._capture_preview_colors()
        return {
            "node_colors": dict(self._preview_colors),
            "frames_sent": self._frames_sent,
        }

    def _capture_preview_colors(self) -> None:
        preview_colors: dict[str, str] = {}
        for adapter, _renderer, _role, _bs, _placement in self.devices:
            colors = list(getattr(adapter, "last_colors", []) or [])
            keys = self._node_keys.get(adapter.address, [adapter.address])
            if not colors:
                continue
            if len(keys) == len(colors):
                for key, color in zip(keys, colors):
                    preview_colors[key] = _rgb_to_hex(_normalize_preview_color(color))
                continue
            if len(keys) > 1 and len(colors) == 1:
                color_hex = _rgb_to_hex(_normalize_preview_color(colors[0]))
                for key in keys:
                    preview_colors[key] = color_hex
                continue
            preview_colors[keys[0]] = _rgb_to_hex(_normalize_preview_color(_average_color(colors)))
        self._preview_colors = preview_colors
