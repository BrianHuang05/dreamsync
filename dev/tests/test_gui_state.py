from __future__ import annotations

import time
from pathlib import Path

from dreamsync.gui.models.capture_settings import CaptureSettings
from dreamsync.gui.models.reactive_settings import ReactiveSettings
from dreamsync.gui.main_window import _reactive_live_palette_override
from dreamsync.gui.settings import GuiSettings, GuiSettingsStore
from dreamsync.gui.state import AppState
from dreamsync.gui.workers import WorkerPool


def test_settings_round_trip(tmp_path: Path):
    store = GuiSettingsStore(tmp_path / "gui-settings.json")
    settings = GuiSettings(
        last_config_path="dev/devices-dummy.yaml",
        last_profile_path="src/dreamsync/profiles/aurora.yaml",
        last_tab="Queue",
        window_geometry="abc123",
        splitter_sizes=(240, 880),
        output_target_mode="hardware",
        selected_output_audio_device_id=7,
        selected_live_input_device_id=3,
        hardware_fallback_to_simulation=False,
        live_start_mode="raw_visualizer",
        raw_visualizer_noise_threshold=0.027,
        raw_visualizer_gradient_points=(
            (60, "#110000"),
            (700, "#001100"),
            (4_000, "#000011"),
            (12_000, "#ffffff"),
        ),
        raw_visualizer_origins=(
            ("192.0.2.10", 2),
            ("192.0.2.11", 7),
        ),
        recent_saved_show_paths=("a.show.json", "b.show.json"),
        capture_settings=CaptureSettings(
            capture_dir="captures",
            naming_mode="metadata",
            max_capture_buffer=12,
            device_pattern="Cable",
            sample_rate=48000,
            channels=2,
            pipeline_playback_device_id=7,
            purge_after_playback=True,
        ),
        reactive_settings=ReactiveSettings(
            render_mode="pulse",
            sample_rate=48000,
            frame_size=4096,
            hop_size=1024,
            blocksize=2048,
            telemetry_dir="telemetry",
            profile_strategy="profile_rotation",
            rotation_profiles=("aurora", "sunset"),
            smart_rotation=True,
        ),
    )

    store.save(settings)
    loaded = store.load()

    assert loaded == settings


def test_reactive_auto_cycle_does_not_install_fixed_palette_override():
    assert _reactive_live_palette_override(
        "neon",
        auto_cycle=True,
    ) == ()
    assert _reactive_live_palette_override(
        "neon",
        auto_cycle=False,
    )


def test_settings_migrates_device_discovery_tab_name(tmp_path: Path):
    path = tmp_path / "gui-settings.json"
    path.write_text('{"last_tab": "Device Discovery"}', encoding="utf-8")

    assert GuiSettingsStore(path).load().last_tab == "Devices"


def test_app_state_transitions():
    state = AppState()
    state = state.with_tab("Palettes").with_diagnostic("ready")

    assert state.selected_tab == "Palettes"
    assert state.diagnostics[-1] == "ready"


def test_reactive_settings_accept_wave_and_gradient():
    assert ReactiveSettings(render_mode="wave").validate() == ()
    assert ReactiveSettings(render_mode="gradient").validate() == ()


def test_reactive_settings_support_song_change_profile_cycle():
    settings = ReactiveSettings(
        profile_strategy="song_change_rotation",
        rotation_profiles=("aurora", "sunset"),
    )

    assert settings.validate() == ()
    assert ReactiveSettings(profile_strategy="song_change_rotation").validate() == (
        "Provide one or more profiles before starting Reactive profile cycling.",
    )


def test_worker_pool_success_and_failure():
    pool = WorkerPool(max_workers=2)
    try:
        pool.submit("ok", lambda: 7)
        pool.submit("fail", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        events = []
        for _ in range(20):
            time.sleep(0.01)
            events = pool.drain_events()
            if len(events) >= 2:
                break
        names = {event.name for event in events}
        assert names == {"ok", "fail"}
        assert any(getattr(event, "result", None) == 7 for event in events)
        assert any(str(getattr(event, "error", "")) == "boom" for event in events)
    finally:
        pool.shutdown()
