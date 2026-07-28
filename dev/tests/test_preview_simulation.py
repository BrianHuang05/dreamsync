from __future__ import annotations

import json
import textwrap
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dreamsync.analyzer.bpm import BeatGrid, TempoRegion
from dreamsync.analyzer.models import SongStructure
from dreamsync.analyzer.sections import Section
from dreamsync.director import EffectMode, LightingIntent
from dreamsync.gui.services.session_service import SessionService
from dreamsync.local_session import LocalShowSession
from dreamsync.output.auto_detect import load_device_config
from dreamsync.output.null_adapter import NullMultiAdapter, PreviewMirrorAdapter, SimulationMultiAdapter
from dreamsync.render import RenderMode
from dreamsync.show.models import ShowCue, ShowTimeline


class PreviewSimulationTests(unittest.TestCase):
    def test_local_show_session_reuses_analysis_sidecar_without_reanalyzing(self) -> None:
        tmp_dir = Path(self.id().replace(".", "_"))
        tmp_dir.mkdir(parents=True, exist_ok=True)
        song = tmp_dir / "song.mp3"
        song.touch()

        structure = SongStructure(
            path=str(song),
            duration=12.0,
            bpm=120.0,
            time_signature=4,
            beat_grid=BeatGrid(
                bpm=120.0,
                beat_times=(0.0, 0.5, 1.0, 1.5),
                downbeat_times=(0.0, 2.0),
                time_signature=4,
            ),
            tempo_regions=(
                TempoRegion(start_t=0.0, end_t=12.0, bpm=120.0, confidence=1.0),
            ),
            sections=(
                Section(
                    start_t=0.0,
                    end_t=12.0,
                    label="intro",
                    energy_mean=0.4,
                    mood="steady",
                    bpm=120.0,
                    section_id="intro-1",
                ),
            ),
            metadata={"_analysis_cache_version": 5, "title": "Cached Song"},
        )
        song.with_suffix(".analysis.json").write_text(
            json.dumps(structure.to_dict(), indent=2),
            encoding="utf-8",
        )

        timeline = ShowTimeline(
            song_path=str(song),
            duration=12.0,
            bpm=120.0,
            time_signature=4,
            beat_times=(0.0, 0.5, 1.0, 1.5),
            downbeat_times=(0.0,),
            cues=(
                ShowCue(
                    t=0.0,
                    render_mode="solid",
                    color_palette=("#224466",),
                    intensity=1.0,
                    speed=0.0,
                    params={},
                    transition="cut",
                    transition_beats=0,
                ),
            ),
            metadata={"source": "cached-analysis"},
        )

        cache = MagicMock()
        cache.has.return_value = False
        session = LocalShowSession(NullMultiAdapter(), cache=cache, profile=None)

        with patch("dreamsync.local_session.analyze_song", side_effect=AssertionError("analysis should not rerun")), patch(
            "dreamsync.local_session.cached_compile_show",
            return_value=(timeline, False),
        ) as compile_mock:
            compiled = session._compile_for_file(song)

        self.assertIs(compiled, timeline)
        self.assertEqual(session._cache_hits, 0)
        self.assertEqual(session._cache_misses, 1)
        self.assertEqual(compile_mock.call_count, 1)
        cached_structure = compile_mock.call_args.args[0]
        self.assertIsInstance(cached_structure, SongStructure)
        self.assertEqual(cached_structure.metadata.get("_analysis_cache_version"), 5)

    def test_simulation_adapter_exposes_section_node_colors(self) -> None:
        tmp_dir = Path(self.id().replace(".", "_"))
        tmp_dir.mkdir(parents=True, exist_ok=True)
        path = tmp_dir / "devices.yaml"
        path.write_text(
            textwrap.dedent(
                """
                devices:
                  - name: Couch Strip
                    address: 10.0.0.10
                    segments: 3
                    x: 0.0
                    y: 0.0
                    z: 0.0
                    sections:
                      - index: 0
                        x: -1.0
                        y: 0.0
                        z: 0.0
                      - index: 1
                        x: 0.0
                        y: 0.0
                        z: 0.0
                      - index: 2
                        x: 1.0
                        y: 0.0
                        z: 0.0
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        configs = load_device_config(path)
        adapter = SimulationMultiAdapter.from_configs(configs)
        adapter.send_frame(
            0.0,
            LightingIntent(
                mode=EffectMode.AMBIENT,
                intensity=1.0,
                speed=0.0,
                bpm=120.0,
                color="#ff4400",
            ),
            params={"_render_mode": "solid"},
        )

        snapshot = adapter.preview_snapshot()
        node_colors = snapshot["node_colors"]

        self.assertEqual(
            sorted(node_colors.keys()),
            [
                "10.0.0.10#section:0",
                "10.0.0.10#section:1",
                "10.0.0.10#section:2",
            ],
        )
        self.assertTrue(all(str(color).startswith("#") for color in node_colors.values()))
        self.assertTrue(any(str(color).lower() != "#000000" for color in node_colors.values()))

    def test_simulation_preview_preserves_rendered_brightness(self) -> None:
        tmp_dir = Path(self.id().replace(".", "_"))
        tmp_dir.mkdir(parents=True, exist_ok=True)
        path = tmp_dir / "devices.yaml"
        path.write_text(
            textwrap.dedent(
                """
                devices:
                  - name: Preview Bulb
                    address: 10.0.0.11
                    protocol: bulb
                    x: 0.0
                    y: 0.0
                    z: 0.0
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        adapter = SimulationMultiAdapter.from_configs(load_device_config(path))
        preview_device = adapter.devices[0][0]
        preview_device.last_colors = [(32, 64, 16)]

        snapshot = adapter.preview_snapshot()
        color_hex = snapshot["node_colors"]["10.0.0.11"]
        rgb = tuple(int(color_hex[index : index + 2], 16) for index in (1, 3, 5))

        self.assertEqual(rgb, (32, 64, 16))

    def test_simulation_preview_preserves_pulse_decay_instead_of_flickering(self) -> None:
        tmp_dir = Path(self.id().replace(".", "_"))
        tmp_dir.mkdir(parents=True, exist_ok=True)
        path = tmp_dir / "devices.yaml"
        path.write_text(
            textwrap.dedent(
                """
                devices:
                  - name: Preview Bulb
                    address: 10.0.0.13
                    protocol: bulb
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        adapter = SimulationMultiAdapter.from_configs(
            load_device_config(path),
            render_mode=RenderMode.PULSE,
        )
        intent = LightingIntent(
            mode=EffectMode.PULSE,
            intensity=1.0,
            speed=1.0,
            bpm=120.0,
            color="#ffffff",
        )

        adapter.send_frame(1.0, intent, beat=True)
        bright = adapter.preview_snapshot()["node_colors"]["10.0.0.13"]
        adapter.send_frame(1.25, intent, beat=False)
        faded = adapter.preview_snapshot()["node_colors"]["10.0.0.13"]

        bright_level = int(bright[1:3], 16)
        faded_level = int(faded[1:3], 16)
        assert bright_level > faded_level > 0

    def test_simulation_adapter_captures_baked_frame_node_colors(self) -> None:
        tmp_dir = Path(self.id().replace(".", "_"))
        tmp_dir.mkdir(parents=True, exist_ok=True)
        path = tmp_dir / "devices.yaml"
        path.write_text(
            textwrap.dedent(
                """
                devices:
                  - name: Baked Preview Strip
                    address: 10.0.0.12
                    type: lan
                    segments: 3
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        adapter = SimulationMultiAdapter.from_configs(load_device_config(path))

        self.assertTrue(adapter.send_baked_frame(
            0.0,
            {
                "10.0.0.12#section:0": "#ff0000",
                "10.0.0.12#section:1": "#00ff00",
                "10.0.0.12#section:2": "#0000ff",
            },
        ))

        self.assertEqual(
            adapter.preview_snapshot()["node_colors"],
            {
                "10.0.0.12#section:0": "#ff0000",
                "10.0.0.12#section:1": "#00ff00",
                "10.0.0.12#section:2": "#0000ff",
            },
        )

    def test_preview_mirror_exposes_hardware_show_frames_to_the_gui(self) -> None:
        tmp_dir = Path(self.id().replace(".", "_"))
        tmp_dir.mkdir(parents=True, exist_ok=True)
        path = tmp_dir / "devices.yaml"
        path.write_text(
            textwrap.dedent(
                """
                devices:
                  - name: Preview Bulb
                    address: 10.0.0.13
                    protocol: bulb
                    x: 0.0
                    y: 0.0
                    z: 0.0
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        class HardwareAdapter:
            device_status_label = "hardware output"
            devices: list[object] = []

            def __init__(self) -> None:
                self.frames = 0

            def activate(self, brightness: int = 100) -> None:
                del brightness

            def deactivate(self) -> None:
                pass

            def send_frame(self, *args, **kwargs) -> bool:
                del args, kwargs
                self.frames += 1
                return True

        hardware = HardwareAdapter()
        mirror = PreviewMirrorAdapter(
            hardware,
            SimulationMultiAdapter.from_configs(load_device_config(path)),
        )
        mirror.activate()
        self.assertTrue(
            mirror.send_frame(
                0.0,
                LightingIntent(
                    mode=EffectMode.AMBIENT,
                    intensity=1.0,
                    speed=0.0,
                    bpm=120.0,
                    color="#ff4400",
                ),
                params={"_render_mode": "solid"},
            )
        )

        self.assertEqual(hardware.frames, 1)
        self.assertTrue(
            any(color.lower() != "#000000" for color in mirror.preview_snapshot()["node_colors"].values())
        )

    def test_local_show_session_reports_simulation_mode_without_devices(self) -> None:
        tmp_dir = Path(self.id().replace(".", "_"))
        tmp_dir.mkdir(parents=True, exist_ok=True)
        path = tmp_dir / "devices.yaml"
        path.write_text(
            textwrap.dedent(
                """
                devices:
                  - name: Rear Lamp
                    address: AA:BB:CC:DD:EE:FF
                    type: ble
                    protocol: bulb
                    x: 0.2
                    y: 0.3
                    z: -0.4
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        adapter = SimulationMultiAdapter.from_configs(load_device_config(path))
        session = LocalShowSession(adapter, cache=MagicMock(), profile=None)

        snapshot = session.session_snapshot()

        self.assertEqual(snapshot["device_status"], "simulation mode (no connected devices)")

    def test_session_service_uses_simulation_adapter_when_config_is_available(self) -> None:
        tmp_dir = Path(self.id().replace(".", "_"))
        tmp_dir.mkdir(parents=True, exist_ok=True)
        song = tmp_dir / "song.mp3"
        song.touch()
        config_path = tmp_dir / "devices.yaml"
        config_path.write_text(
            textwrap.dedent(
                """
                devices:
                  - name: Desk Left
                    address: 10.0.0.10
                    segments: 2
                    x: -0.4
                    y: 0.1
                    z: 0.0
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        service = SessionService()
        ready = threading.Event()
        release = threading.Event()
        captured_adapter: dict[str, object] = {}

        def fake_run_local_session(*args, **kwargs):
            captured_adapter["value"] = args[0]
            kwargs["session_ref"][0] = MagicMock()
            ready.set()
            release.wait(timeout=2)
            return {"mode": "local"}

        with patch("dreamsync.gui.services.session_service.check_ffmpeg", return_value=True), patch(
            "dreamsync.gui.services.session_service.run_local_session",
            side_effect=fake_run_local_session,
        ):
            handle = service.start_local_preview_session(song, config_path=config_path)
            self.assertTrue(ready.wait(timeout=2))
            release.set()
            handle.thread.join(timeout=2)

        self.assertIsInstance(captured_adapter.get("value"), SimulationMultiAdapter)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
