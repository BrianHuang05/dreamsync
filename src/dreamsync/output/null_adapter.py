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


def _normalize_display_color(color: tuple[int, int, int]) -> tuple[int, int, int]:
    """Show rendered hue at full value without changing output brightness."""

    normalized = _normalize_preview_color(color)
    peak = max(normalized)
    if peak == 0:
        return normalized
    # Physical RGB has intensity/master brightness multiplied into it. Using
    # those values directly as a fill color looks exactly like a translucent
    # black mask over the palette. The canvas is a color preview, so restore
    # value while preserving the rendered channel ratios. Exact output remains
    # available in ``node_colors`` and the frame diagnostics.
    scale = 255.0 / peak
    return tuple(min(255, int(round(channel * scale))) for channel in normalized)


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
        params: dict | None = None,
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
        self._last_mirror_parity: dict[str, Any] = {
            "available": False,
            "matches": None,
        }

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

    def keep_ble_connected(self, enabled: bool = True) -> None:
        setter = getattr(self._output_adapter, "keep_ble_connected", None)
        if callable(setter):
            setter(enabled)

    def connect_ble_followers(self) -> None:
        connect = getattr(self._output_adapter, "connect_ble_followers", None)
        if callable(connect):
            connect()

    def shutdown(self) -> None:
        shutdown = getattr(self._output_adapter, "shutdown", None)
        if callable(shutdown):
            shutdown()

    def configure_frame_trace(
        self,
        *,
        enabled: bool,
        max_frames: int = 120,
        sample_every: int = 1,
    ) -> None:
        for adapter in (self._output_adapter, self._preview_adapter):
            configure = getattr(adapter, "configure_frame_trace", None)
            if callable(configure):
                configure(
                    enabled=enabled,
                    max_frames=max_frames,
                    sample_every=sample_every,
                )

    def send_frame(self, t, intent, beat=False, params=None) -> bool:
        output_sent = self._output_adapter.send_frame(t, intent, beat=beat, params=params)
        preview_sent = self._mirror_final_output(
            fallback=lambda: self._preview_adapter.send_frame(
                t, intent, beat=beat, params=params
            ),
            t=t,
        )
        return bool(preview_sent or output_sent)

    def send_spatial_scene(self, t, scene, *, beat=False, base_intent=None) -> bool:
        output_sent = self._output_adapter.send_spatial_scene(
            t, scene, beat=beat, base_intent=base_intent
        )
        preview_sent = self._mirror_final_output(
            fallback=lambda: self._preview_adapter.send_spatial_scene(
                t, scene, beat=beat, base_intent=base_intent
            ),
            t=t,
        )
        return bool(preview_sent or output_sent)

    def send_baked_frame(
        self,
        t: float,
        node_colors: dict[str, str],
        *,
        fallback_color: str = "#000000",
        params: dict | None = None,
    ) -> bool:
        output_sent = self._output_adapter.send_baked_frame(
            t,
            node_colors,
            fallback_color=fallback_color,
            params=params,
        )
        preview_sent = self._mirror_final_output(
            fallback=lambda: self._preview_adapter.send_baked_frame(
                t,
                node_colors,
                fallback_color=fallback_color,
                params=params,
            ),
            t=t,
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
        snapshot = self._preview_adapter.preview_snapshot()
        snapshot["hardware_mirror_parity"] = dict(self._last_mirror_parity)
        return snapshot

    def _mirror_final_output(self, *, fallback, t: float) -> bool:
        final_snapshot = getattr(self._output_adapter, "final_frame_snapshot", None)
        if not callable(final_snapshot):
            self._last_mirror_parity = {"available": False, "matches": None}
            return bool(fallback())
        hardware = dict(final_snapshot())
        node_colors = hardware.get("node_colors", {})
        if not isinstance(node_colors, dict) or not node_colors:
            self._last_mirror_parity = {"available": False, "matches": None}
            return bool(fallback())

        # A hardware adapter only knows about devices it could reach. Render the
        # logical frame for the full configured room first, then overlay exact
        # hardware RGB for the subset that was actually submitted. Otherwise a
        # partial LAN snapshot turns every BLE/unreachable fixture black.
        preview_before = self._preview_adapter.preview_snapshot()
        configured_colors = preview_before.get("node_colors", {})
        configured_keys = (
            {str(key) for key in configured_colors}
            if isinstance(configured_colors, dict)
            else set()
        )
        hardware_keys = {str(key) for key in node_colors}
        fallback_sent = False
        if configured_keys - hardware_keys:
            fallback_sent = bool(fallback())

        logical_colors = self._preview_adapter.preview_snapshot().get(
            "node_colors",
            {},
        )
        merged_colors = (
            {
                str(key): str(value)
                for key, value in logical_colors.items()
            }
            if isinstance(logical_colors, dict)
            else {}
        )
        merged_colors.update(
            {str(key): str(value) for key, value in node_colors.items()}
        )
        mirrored_snapshot = dict(hardware)
        mirrored_snapshot["node_colors"] = merged_colors
        sent = self._preview_adapter.accept_mirrored_frame(
            t,
            mirrored_snapshot,
        )
        preview_colors = self._preview_adapter.preview_snapshot().get("node_colors", {})
        normalized_hardware = {
            str(key): str(value).lower() for key, value in node_colors.items()
        }
        normalized_preview = {
            key: str(dict(preview_colors).get(key, "")).lower()
            for key in normalized_hardware
        }
        self._last_mirror_parity = {
            "available": True,
            "matches": normalized_preview == normalized_hardware,
            "hardware_node_colors": normalized_hardware,
            "preview_node_colors": normalized_preview,
            "simulated_node_count": len(configured_keys - hardware_keys),
        }
        return bool(sent or fallback_sent)

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
        group_definitions: tuple = (),
    ) -> None:
        super().__init__(
            devices,
            ble_followers=[],
            spatial_mapper=spatial_mapper,
            group_definitions=group_definitions,
        )
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
        group_definitions = next(
            (
                tuple(getattr(cfg, "group_definitions", ()) or ())
                for cfg in configs
                if getattr(cfg, "group_definitions", ())
            ),
            (),
        )
        return cls(
            device_tuples,
            node_keys=node_keys,
            spatial_mapper=mapper,
            group_definitions=group_definitions,
        )

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
        params: dict | None = None,
    ) -> bool:
        sent = super().send_baked_frame(
            t,
            node_colors,
            fallback_color=fallback_color,
            params=params,
        )
        if sent:
            self._frames_sent += 1
            self._capture_preview_colors()
        return sent

    def accept_mirrored_frame(
        self,
        t: float,
        final_snapshot: dict[str, Any],
    ) -> bool:
        """Display an output adapter's exact final RGB and provenance."""

        node_colors = final_snapshot.get("node_colors", {})
        if not isinstance(node_colors, dict):
            return False
        sent = self.send_baked_frame(t, node_colors)
        diagnostics = final_snapshot.get("frame_diagnostics", {})
        if isinstance(diagnostics, dict):
            self._last_frame_diagnostics = dict(diagnostics)
            if (
                self._frame_trace_enabled
                and self._frame_trace
                and self._frame_trace[-1].get("output_t") == float(t)
            ):
                self._frame_trace[-1] = dict(diagnostics)
        return sent

    def preview_snapshot(self) -> dict[str, Any]:
        self._capture_preview_colors()
        return {
            "node_colors": dict(self._preview_colors),
            "display_node_colors": {
                key: _rgb_to_hex(
                    _normalize_display_color(
                        (
                            int(color[1:3], 16),
                            int(color[3:5], 16),
                            int(color[5:7], 16),
                        )
                    )
                )
                for key, color in self._preview_colors.items()
            },
            "frames_sent": self._frames_sent,
            "frame_diagnostics": dict(self._last_frame_diagnostics),
            "frame_trace": self.frame_trace_snapshot(),
        }

    def _capture_preview_colors(self) -> None:
        # Retain the last valid color for keys omitted by a transient adapter
        # update. Missing keys must not flicker back to canvas defaults.
        preview_colors: dict[str, str] = dict(self._preview_colors)
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
