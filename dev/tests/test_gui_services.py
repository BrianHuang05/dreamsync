from __future__ import annotations

import json
import textwrap
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dreamsync.gui.services.device_service import DeviceService
from dreamsync.gui.services.profile_service import ProfileService
from dreamsync.gui.services.queue_service import QueueService
from dreamsync.gui.services.session_service import (
    ReactiveLiveSession,
    SessionHandle,
    SessionService,
    TimelinePlaybackSession,
)
from dreamsync.gui.services.show_service import ShowService
from dreamsync.gui.models.reactive_settings import ReactiveSettings
from dreamsync.gui.settings import GuiSettings, GuiSettingsStore
from dreamsync.local_session import LocalPlaylistSession
from dreamsync.playlist import PlaylistManager
from dreamsync.show.baked_frames import (
    BakeSettings,
    BakedFrame,
    BakedFrameArtifact,
    BakedFrameNode,
    default_engine_metadata,
    default_baked_frame_path,
    sha256_file,
)
from dreamsync.show.models import Show, ShowCue, ShowTimeline, ShowTrack
from dreamsync.spotify.models import PlaybackState, QueueSnapshot, SpotifyTrack


def test_device_service_scene_round_trip(tmp_path: Path):
    path = tmp_path / "devices.yaml"
    path.write_text(
        textwrap.dedent(
            """
            devices:
              - name: Desk Left
                address: 10.0.0.10
                x: -0.5
                y: 0.25
              - name: Rear Lamp
                address: AA:BB:CC:DD:EE:FF
                type: ble
                x: 0.5
                y: -0.25
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    service = DeviceService()
    service.save_scene(path, {"10.0.0.10": (-0.25, 0.75, 0.5), "AA:BB:CC:DD:EE:FF": (0.5, -0.25, -0.5)})
    scene = {entry.key: entry for entry in service.load_scene(path)}

    assert scene["10.0.0.10"].z == 0.5
    assert scene["AA:BB:CC:DD:EE:FF"].z == -0.5


def test_device_service_expands_and_saves_section_nodes(tmp_path: Path):
    path = tmp_path / "devices.yaml"
    path.write_text(
        textwrap.dedent(
            """
            devices:
              - name: Couch Strip
                address: 10.0.0.10
                segments: 3
                x: 0.0
                y: 0.0
              - name: Bulb
                address: AA:BB:CC:DD:EE:FF
                type: ble
                protocol: bulb
                x: 0.2
                y: -0.3
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    service = DeviceService()
    scene = service.load_scene(path)

    assert [entry.section_index for entry in scene[:3]] == [0, 1, 2]
    assert all(entry.is_section for entry in scene[:3])
    assert scene[3].is_section is False

    service.save_scene(
        path,
        {
            "10.0.0.10#section:0": (-0.6, 0.2, 0.0),
            "10.0.0.10#section:1": (0.0, 0.2, 0.0),
            "10.0.0.10#section:2": (0.6, 0.2, 0.0),
            "AA:BB:CC:DD:EE:FF": (0.2, -0.3, 0.4),
        },
    )

    updated = path.read_text(encoding="utf-8")
    assert "sections:" in updated
    assert "index: 0" in updated
    assert "z: 0.4" in updated


def test_profile_service_updates_palette(tmp_path: Path):
    path = tmp_path / "profile.yaml"
    path.write_text(
        textwrap.dedent(
            """
            name: Test Profile
            version: 1
            palettes:
              warm: ["#111111", "#222222", "#333333"]
            moods:
              chill: {palettes: [warm]}
              groove: {palettes: [warm]}
              hype: {palettes: [warm]}
              drop: {palettes: [warm]}
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    service = ProfileService()
    profile = service.update_palette(path, "warm", ("#abcdef", "#123456", "#654321"))

    assert profile.palettes["warm"][0] == "#abcdef"


def test_profile_service_searches_custom_profiles_and_reports_invalid_files(tmp_path: Path):
    valid_path = tmp_path / "ocean.yaml"
    invalid_path = tmp_path / "broken.yaml"
    valid_path.write_text(
        textwrap.dedent(
            """
            name: Ocean Test
            tags: [cool, ambient]
            palettes:
              ocean: ["#112233", "#225588", "#44aacc"]
            moods:
              chill: {palettes: [ocean]}
              groove: {palettes: [ocean]}
              hype: {palettes: [ocean]}
              drop: {palettes: [ocean]}
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    invalid_path.write_text("name: Broken\npalettes: nope\n", encoding="utf-8")
    service = ProfileService()

    results = service.search_profiles((tmp_path,), query="ocean", tags=("cool",))
    all_custom = [
        entry for entry in service.search_profiles((tmp_path,)) if entry.path.parent == tmp_path
    ]
    custom_results = [entry for entry in results if entry.path.parent == tmp_path]

    assert len(custom_results) == 1
    assert custom_results[0].name == "Ocean Test"
    assert custom_results[0].palette_count == 1
    assert any(entry.path == invalid_path.resolve() and not entry.valid for entry in all_custom)


def test_profile_generation_is_deterministic_and_export_is_valid(tmp_path: Path):
    service = ProfileService()
    first = service.generate_profile_pool(4, seed=99)
    second = service.generate_profile_pool(4, seed=99)
    export_path = tmp_path / "generated.yaml"

    service.export_generated_profile(first[2], export_path, seed=99, index=2)

    assert [profile.palettes for profile in first] == [profile.palettes for profile in second]
    assert service.load_profile(export_path).palettes == first[2].palettes


def test_invalid_profile_save_does_not_overwrite_existing_file(tmp_path: Path):
    path = tmp_path / "profile.yaml"
    original = textwrap.dedent(
        """
        name: Safe
        palettes:
          main: ["#112233", "#445566", "#778899"]
        moods:
          chill: {palettes: [main]}
          groove: {palettes: [main]}
          hype: {palettes: [main]}
          drop: {palettes: [main]}
        """
    ).strip() + "\n"
    path.write_text(original, encoding="utf-8")

    with pytest.raises(Exception):
        ProfileService().save_profile_document(
            path,
            {
                "name": "Invalid",
                "palettes": {"bad": ["not-a-color"]},
                "moods": {},
            },
        )

    assert path.read_text(encoding="utf-8") == original


def test_queue_service_local_and_spotify_snapshots(tmp_path: Path):
    for name in ("a.mp3", "b.mp3", "c.mp3"):
        (tmp_path / name).touch()

    playlist = PlaylistManager.from_directory(tmp_path)
    session = LocalPlaylistSession(MagicMock(), playlist, cache=MagicMock(), profile=None)
    service = QueueService()
    local = service.local_queue_snapshot(session)

    assert local["current_index"] == 0
    assert tuple(Path(track).name for track in local["tracks"]) == ("a.mp3", "b.mp3", "c.mp3")
    assert len(local["track_rows"]) == 3
    assert local["track_rows"][0]["display_name"] == "a.mp3"
    assert local["track_rows"][0]["track_key"]

    track = SpotifyTrack(
        track_id="1",
        name="Current",
        artist="Artist",
        album="Album",
        duration_ms=200000,
        uri="spotify:track:1",
    )
    queue_track = SpotifyTrack(
        track_id="2",
        name="Next",
        artist="Artist",
        album="Album",
        duration_ms=180000,
        uri="spotify:track:2",
    )
    client = MagicMock()
    client.get_playback_state.return_value = PlaybackState(
        is_playing=True,
        track=track,
        progress_ms=0,
        timestamp=0.0,
        device_name="test",
        shuffle=False,
        repeat="off",
    )
    client.get_queue.return_value = QueueSnapshot(
        currently_playing=track,
        queue=(queue_track,),
        fetched_at=0.0,
    )

    spotify = service.spotify_queue_snapshot(client=client)
    assert spotify["current_track"] == "Current"
    assert spotify["queue"] == ("Next",)


def test_queue_service_playlist_mutations(tmp_path: Path):
    for name in ("a.mp3", "b.mp3", "c.mp3", "d.mp3"):
        (tmp_path / name).touch()

    playlist = PlaylistManager.from_directory(tmp_path)
    service = QueueService()

    moved = service.move_playlist_track(playlist, 3, 1)
    assert tuple(Path(track).name for track in moved["tracks"]) == ("a.mp3", "d.mp3", "b.mp3", "c.mp3")

    removed = service.remove_playlist_track(playlist, 2)
    assert tuple(Path(track).name for track in removed["tracks"]) == ("a.mp3", "d.mp3", "c.mp3")

    jumped = service.play_playlist_now(playlist, 1)
    assert jumped["current_index"] == 1

    shuffled = service.shuffle_playlist(playlist)
    assert tuple(Path(track).name for track in shuffled["tracks"])[0] == "a.mp3"

    service.set_playlist_repeat(playlist, True)
    assert playlist.repeat_enabled is True

    session = LocalPlaylistSession(MagicMock(), playlist, cache=MagicMock(), profile=None)
    service.set_local_repeat(session, False)
    assert playlist.repeat_enabled is False


def test_profile_service_loads_palette_choices_and_generation(tmp_path: Path):
    path = tmp_path / "profile.yaml"
    path.write_text(
        textwrap.dedent(
            """
            name: Queue Palette Test
            version: 1
            palettes:
              calm: ["#111111", "#222222", "#333333"]
              bright: ["#aaaaaa", "#bbbbbb", "#cccccc"]
            moods:
              chill: {palettes: [calm]}
              groove: {palettes: [bright]}
              hype: {palettes: [bright]}
              drop: {palettes: [bright]}
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    service = ProfileService()
    choices = service.load_palette_choices(path)

    assert [choice["palette_name"] for choice in choices] == ["calm", "bright"]

    generated = service.generate_palette_choice(seed=7)
    assert generated["profile_name"]
    assert generated["palette_name"]
    assert len(generated["colors"]) >= 3


def test_profile_service_updates_mood_effects_params_and_routes(tmp_path: Path):
    path = tmp_path / "profile.yaml"
    path.write_text(
        textwrap.dedent(
            """
            name: Control Surface Test
            version: 1
            palettes:
              warm: ["#111111", "#222222", "#333333"]
              cool: ["#444444", "#555555", "#666666"]
            moods:
              chill: {palettes: [warm]}
              groove: {palettes: [cool]}
              hype: {palettes: [cool]}
              drop: {palettes: [cool]}
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    service = ProfileService()
    profile = service.update_mood_effects(
        path,
        "chill",
        [{"name": "wave_drift", "weight": 2.5}],
    )
    assert profile.moods["chill"].effects[0].name == "wave_drift"

    profile = service.update_mood_params(path, "chill", {"wave_rate_mult": 0.3})
    assert profile.moods["chill"].params["wave_rate_mult"] == 0.3

    profile = service.update_eq_routes(
        path,
        [{"band": "bass", "when": "dominant", "color_bias": "#ff6600"}],
    )
    assert profile.eq_routes[0].band == "bass"

    profile = service.update_instrument_routes(
        path,
        [{"instrument": "vocals", "when": "dominant", "pan_follow": 0.7}],
        mood="groove",
    )
    assert profile.moods["groove"].instrument_routes[0].instrument == "vocals"
    assert profile.moods["groove"].instrument_routes[0].pan_follow == 0.7


def test_profile_service_updates_transitions(tmp_path: Path):
    path = tmp_path / "profile.yaml"
    path.write_text(
        textwrap.dedent(
            """
            name: Transition Test
            version: 1
            palettes:
              warm: ["#111111", "#222222", "#333333"]
              cool: ["#444444", "#555555", "#666666"]
            moods:
              chill: {palettes: [warm]}
              groove: {palettes: [cool]}
              hype: {palettes: [cool]}
              drop: {palettes: [cool]}
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    service = ProfileService()
    profile = service.update_transitions(
        path,
        [{"from": "chill", "to": "groove", "palette": "cool"}],
    )

    assert profile.transitions[0].from_mood == "chill"
    assert profile.transitions[0].palette == "cool"


def test_gui_settings_store_round_trips_show_editor_layout_fields(tmp_path: Path):
    path = tmp_path / "gui-settings.json"
    store = GuiSettingsStore(path)
    settings = GuiSettings(
        dark_mode=True,
        show_editor_hidden_columns=(1, 4, 9),
        show_editor_meta_hidden=True,
        show_editor_focus_mode=True,
        show_editor_splitter_sizes=(320, 1180),
    )

    store.save(settings)
    loaded = store.load()

    assert loaded.show_editor_hidden_columns == (1, 4, 9)
    assert loaded.show_editor_meta_hidden is True
    assert loaded.show_editor_focus_mode is True
    assert loaded.show_editor_splitter_sizes == (320, 1180)
    assert loaded.dark_mode is True


def test_gui_settings_store_migrates_legacy_slow_harmonic_hop(
    tmp_path: Path,
):
    path = tmp_path / "gui-settings.json"
    path.write_text(
        json.dumps(
            {
                "reactive_settings": {
                    "harmonic_hop_multiplier": 4,
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = GuiSettingsStore(path).load()

    assert loaded.reactive_settings.harmonic_hop_multiplier == 1


def test_gui_settings_store_round_trips_reactive_live_layout_and_harmonics(
    tmp_path: Path,
):
    path = tmp_path / "gui-settings.json"
    store = GuiSettingsStore(path)
    settings = GuiSettings(
        reactive_diagnostics_splitter_sizes=(210, 330, 450),
        reactive_chord_panel_visible=False,
        reactive_waveform_panel_visible=True,
        reactive_harmonic_panel_visible=False,
        reactive_live_color_profile="neon",
        reactive_live_active_effect="ripple",
        reactive_live_effect_speed="8",
        reactive_live_effect_origin="outer",
        reactive_live_effect_bank=("pulse", "ripple"),
        reactive_settings=ReactiveSettings(
            harmonic_structure_enabled=True,
            beats_per_bar=3,
            bars_per_phrase=8,
            harmonic_frame_size=8192,
            harmonic_hop_multiplier=2,
            structure_sensitivity=0.72,
            downbeat_min_confidence=0.31,
            debug_harmonics=True,
            predictive_analysis_enabled=True,
            predictive_diagnostics_enabled=True,
            predictive_shadow_mode=False,
            predictive_cues_enabled=True,
            predictive_high_impact_cues_enabled=True,
            predictive_cue_prepare_threshold=0.55,
            predictive_cue_schedule_threshold=0.7,
            predictive_cue_high_impact_threshold=0.86,
            predictive_maximum_anticipatory_intensity=0.24,
            predictive_cue_cooldown_seconds=3.5,
            predictive_allowed_cue_classes=(
                "chord_accent",
                "resolution_bloom",
            ),
        ),
    )

    store.save(settings)
    loaded = store.load()

    assert loaded.reactive_diagnostics_splitter_sizes == (210, 330, 450)
    assert loaded.reactive_chord_panel_visible is False
    assert loaded.reactive_waveform_panel_visible is True
    assert loaded.reactive_harmonic_panel_visible is False
    assert loaded.reactive_live_color_profile == "neon"
    assert loaded.reactive_live_active_effect == "ripple"
    assert loaded.reactive_live_effect_speed == "8"
    assert loaded.reactive_live_effect_origin == "outer"
    assert loaded.reactive_live_effect_bank == ("pulse", "ripple")
    assert loaded.reactive_settings.harmonic_structure_enabled is True
    assert loaded.reactive_settings.beats_per_bar == 3
    assert loaded.reactive_settings.bars_per_phrase == 8
    assert loaded.reactive_settings.harmonic_frame_size == 8192
    assert loaded.reactive_settings.harmonic_hop_multiplier == 2
    assert loaded.reactive_settings.structure_sensitivity == 0.72
    assert loaded.reactive_settings.downbeat_min_confidence == 0.31
    assert loaded.reactive_settings.debug_harmonics is True
    assert loaded.reactive_settings.predictive_analysis_enabled is True
    assert loaded.reactive_settings.predictive_diagnostics_enabled is True
    assert loaded.reactive_settings.predictive_shadow_mode is False
    assert loaded.reactive_settings.predictive_cues_enabled is True
    assert loaded.reactive_settings.predictive_high_impact_cues_enabled is True
    assert loaded.reactive_settings.predictive_cue_prepare_threshold == 0.55
    assert loaded.reactive_settings.predictive_cue_schedule_threshold == 0.7
    assert loaded.reactive_settings.predictive_cue_high_impact_threshold == 0.86
    assert (
        loaded.reactive_settings.predictive_maximum_anticipatory_intensity
        == 0.24
    )
    assert loaded.reactive_settings.predictive_cue_cooldown_seconds == 3.5
    assert loaded.reactive_settings.predictive_allowed_cue_classes == (
        "chord_accent",
        "resolution_bloom",
    )


def test_gui_settings_store_round_trips_baked_playback_mode(tmp_path: Path):
    path = tmp_path / "gui-settings.json"
    store = GuiSettingsStore(path)
    settings = GuiSettings(baked_playback_mode="require")

    store.save(settings)
    loaded = store.load()

    assert loaded.baked_playback_mode == "require"

def test_gui_settings_store_round_trips_optional_compile_seed(tmp_path: Path):
    path = tmp_path / "gui-settings.json"
    store = GuiSettingsStore(path)

    store.save(GuiSettings(show_compile_seed=8675309))

    assert store.load().show_compile_seed == 8675309


def test_gui_settings_store_round_trips_profile_chain_generation_fields(tmp_path: Path):
    path = tmp_path / "gui-settings.json"
    store = GuiSettingsStore(path)
    settings = GuiSettings(
        reactive_settings=ReactiveSettings(
            profile_strategy="smart_rotation",
            auto_palette=True,
            auto_palette_seed=42,
            auto_palette_pool_size=6,
            chain_dwell_range_enabled=True,
            chain_min_dwell_seconds=30.0,
            chain_max_dwell_seconds=90.0,
        )
    )

    store.save(settings)
    loaded = store.load().reactive_settings

    assert loaded.auto_palette_seed == 42
    assert loaded.auto_palette_pool_size == 6
    assert loaded.chain_dwell_range_enabled is True
    assert loaded.chain_min_dwell_seconds == 30.0
    assert loaded.chain_max_dwell_seconds == 90.0


def test_gui_settings_store_round_trips_live_loopback_setting(tmp_path: Path):
    store = GuiSettingsStore(tmp_path / "gui-settings.json")

    store.save(GuiSettings(live_loopback_enabled=True))

    assert store.load().live_loopback_enabled is True


def test_gui_settings_store_round_trips_file_location_preferences(tmp_path: Path):
    path = tmp_path / "gui-settings.json"
    store = GuiSettingsStore(path)
    settings = GuiSettings(
        profile_directory="D:/DreamSync/profiles",
        show_directory="D:/DreamSync/shows",
        queue_directory="D:/Music",
    )

    store.save(settings)
    loaded = store.load()

    assert loaded.profile_directory == "D:/DreamSync/profiles"
    assert loaded.show_directory == "D:/DreamSync/shows"
    assert loaded.queue_directory == "D:/Music"


def test_show_service_load_and_save_timeline_round_trip(tmp_path: Path):
    path = tmp_path / "demo.show.json"
    timeline = ShowTimeline(
        song_path=str(tmp_path / "demo.mp3"),
        duration=12.5,
        bpm=128.0,
        time_signature=4,
        beat_times=(0.0, 0.5, 1.0, 1.5),
        downbeat_times=(0.0, 2.0),
        cues=(
            ShowCue(
                t=0.0,
                render_mode="gradient",
                color_palette=("#ff0000", "#00ffcc"),
                intensity=0.8,
                speed=1.2,
                params={"wave_rate_mult": 0.4},
                transition="fade",
                transition_beats=2,
                intensity_start=0.3,
            ),
        ),
        metadata={"track_name": "Demo", "artist": "Tester"},
    )

    service = ShowService()
    saved_path = service.save_timeline(timeline, path)
    loaded = service.load_timeline(saved_path)

    assert saved_path == path
    assert loaded.song_path == timeline.song_path
    assert loaded.bpm == timeline.bpm
    assert loaded.metadata["track_name"] == "Demo"
    assert loaded.cues[0].render_mode == "gradient"
    assert loaded.cues[0].params["wave_rate_mult"] == 0.4


def test_show_service_forwards_compile_seed_and_records_it(tmp_path: Path):
    audio_path = tmp_path / "seeded.mp3"
    structure = MagicMock()
    timeline = ShowTimeline(
        song_path=str(audio_path),
        duration=2.0,
        bpm=120.0,
        time_signature=4,
        beat_times=(0.0, 0.5),
        downbeat_times=(0.0,),
        cues=(ShowCue(0.0, "solid", ("#123456",), 1.0, 0.0, {}, "cut", 0),),
        metadata={},
    )
    service = ShowService()

    with patch(
        "dreamsync.gui.services.show_service.analyze_song",
        return_value=structure,
    ), patch(
        "dreamsync.gui.services.show_service.compile_show",
        return_value=timeline,
    ) as compiler:
        _structure, compiled = service.compile_with_analysis(audio_path, seed=42)

    compiler.assert_called_once_with(structure, None, seed=42)
    assert compiled.metadata["compile_seed"] == 42


def test_show_service_validates_and_exports_baked_artifact(tmp_path: Path):
    show_path = tmp_path / "song.show.json"
    config_path = tmp_path / "devices.yaml"
    source_path = default_baked_frame_path(show_path)
    target_path = tmp_path / "exports" / "copy.frames.json"
    show_path.write_text("{}", encoding="utf-8")
    config_path.write_text("devices: []\n", encoding="utf-8")
    artifact = BakedFrameArtifact(
        source_show_path=str(show_path),
        source_show_hash=sha256_file(show_path),
        device_config_path=str(config_path),
        device_config_hash=sha256_file(config_path),
        engine=default_engine_metadata(),
        settings=BakeSettings(),
        duration=1.0,
        nodes=(BakedFrameNode("node", "device"),),
        frames=(BakedFrame(0.0, ("#112233",)),),
        summary={"frame_count": 1, "node_count": 1},
    )
    artifact.to_json(source_path)
    service = ShowService()

    validation = service.validate_baked_artifact(
        source_path,
        source_show_path=show_path,
        device_config_path=config_path,
    )
    exported = service.export_baked_artifact(source_path, target_path)

    assert validation.valid is True
    assert exported == target_path
    assert BakedFrameArtifact.from_json(target_path).to_dict() == artifact.to_dict()


def test_show_service_retint_preserves_cues_and_explicit_palette_overrides():
    original_palette = ("#111111", "#222222")
    replacement_palette = ("#abcdef", "#fedcba")
    timeline = ShowTimeline(
        song_path="demo.mp3",
        duration=12.0,
        bpm=120.0,
        time_signature=4,
        beat_times=(0.0, 0.5),
        downbeat_times=(0.0,),
        cues=(
            ShowCue(
                t=0.0,
                render_mode="gradient",
                color_palette=original_palette,
                intensity=0.7,
                speed=1.4,
                params={"gradient_colors": original_palette, "effect": "wave"},
                transition="fade",
                transition_beats=2,
            ),
            ShowCue(
                t=4.0,
                render_mode="solid",
                color_palette=("#ff0000",),
                intensity=0.5,
                speed=0.4,
                params={"palette_override": True},
                transition="cut",
                transition_beats=0,
            ),
        ),
        metadata={"show_palette": list(original_palette), "track_name": "Demo"},
    )

    retinted = ShowService.retint_timeline(timeline, replacement_palette)

    assert retinted.cues[0].color_palette == replacement_palette
    assert retinted.cues[0].params["gradient_colors"] == replacement_palette
    assert retinted.cues[0].t == timeline.cues[0].t
    assert retinted.cues[0].intensity == timeline.cues[0].intensity
    assert retinted.cues[0].params["effect"] == "wave"
    assert retinted.cues[1] == timeline.cues[1]
    assert retinted.metadata["show_palette"] == list(replacement_palette)
    assert retinted.metadata["palette_retinted"] is True


def test_show_service_saves_and_loads_ordered_multi_track_show(tmp_path: Path):
    timeline = ShowTimeline(
        song_path=str(tmp_path / "one.mp3"),
        duration=12.0,
        bpm=120.0,
        time_signature=4,
        beat_times=(0.0, 0.5),
        downbeat_times=(0.0,),
        cues=(ShowCue(0.0, "solid", ("#224466",), 1.0, 0.0, {}, "cut", 0),),
        metadata={},
    )
    show = Show(
        name="Two Track Set",
        tracks=(
            ShowTrack(audio_path=str(tmp_path / "one.mp3"), timeline=timeline),
            ShowTrack(audio_path=str(tmp_path / "two.mp3")),
        ),
        metadata={},
    )

    service = ShowService()
    path = tmp_path / "out" / "shows" / "two-track-set.show.json"
    saved = service.save_show(show, path)
    loaded = service.load_show(saved)

    assert saved == path
    assert [track.audio_path for track in loaded.tracks] == [
        str(tmp_path / "one.mp3"),
        str(tmp_path / "two.mp3"),
    ]
    assert loaded.tracks[0].is_compiled is True
    assert loaded.tracks[1].is_compiled is False


def test_show_service_prefers_editor_sections_from_timeline_metadata():
    timeline = ShowTimeline(
        song_path="demo.mp3",
        duration=10.0,
        bpm=120.0,
        time_signature=4,
        beat_times=(0.0, 0.5, 1.0, 1.5),
        downbeat_times=(0.0, 2.0),
        cues=(
            ShowCue(
                t=0.0,
                render_mode="solid",
                color_palette=("#ffffff",),
                intensity=1.0,
                speed=0.0,
                params={},
                transition="cut",
                transition_beats=0,
            ),
        ),
        metadata={
            "editor_sections": [
                {"start_t": 0.0, "end_t": 3.0, "label": "Intro"},
                {"start_t": 3.0, "end_t": 7.5, "label": "Verse"},
            ]
        },
    )

    context = ShowService().build_timeline_context(audio_path=None, timeline=timeline)

    assert context.duration == 10.0
    assert len(context.sections) == 2
    assert context.sections[0].label == "Intro"
    assert context.sections[1].start_t == 3.0


def test_session_service_exposes_live_session_ref(tmp_path: Path):
    song = tmp_path / "song.mp3"
    song.touch()

    service = SessionService()
    import threading
    ready = threading.Event()
    release = threading.Event()

    def fake_run_local_session(*args, **kwargs):
        session_ref = kwargs["session_ref"]
        session_ref[0] = MagicMock()
        ready.set()
        release.wait(timeout=2)
        return {"mode": "local"}

    from unittest.mock import patch

    with patch("dreamsync.gui.services.session_service.check_ffmpeg", return_value=True), patch(
        "dreamsync.gui.services.session_service.run_local_session",
        side_effect=fake_run_local_session,
    ):
        handle = service.start_local_preview_session(song)
        assert ready.wait(timeout=2) is True
        assert handle.session_ref[0] is not None
        release.set()
        handle.thread.join(timeout=2)

    assert handle.session_ref is not None


def test_session_service_passes_timeline_resolver_to_local_preview(tmp_path: Path):
    song = tmp_path / "song.mp3"
    song.touch()
    service = SessionService()
    captured_kwargs = {}
    resolver = lambda audio_path, timeline: timeline

    def fake_run_local_session(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return {"mode": "local"}

    from unittest.mock import patch

    with patch("dreamsync.gui.services.session_service.check_ffmpeg", return_value=True), patch(
        "dreamsync.gui.services.session_service.run_local_session",
        side_effect=fake_run_local_session,
    ):
        handle = service.start_local_preview_session(song, timeline_resolver=resolver)
        handle.wait(timeout=2)

    assert captured_kwargs["timeline_resolver"] is resolver
    assert captured_kwargs["runtime_control"] is not None


def test_session_service_requires_ffmpeg_for_local_preview(tmp_path: Path):
    song = tmp_path / "song.mp3"
    song.touch()

    service = SessionService()

    from unittest.mock import patch

    with patch("dreamsync.gui.services.session_service.check_ffmpeg", return_value=False):
        try:
            service.start_local_preview_session(song)
        except RuntimeError as exc:
            assert "ffmpeg is required for local preview playback" in str(exc)
        else:  # pragma: no cover - defensive
            raise AssertionError("Expected ffmpeg preflight failure")


def test_session_service_starts_precompiled_show_session(tmp_path: Path):
    song = tmp_path / "song.mp3"
    song.touch()
    show_path = tmp_path / "song.show.json"
    timeline = ShowTimeline(
        song_path=str(song),
        duration=12.0,
        bpm=120.0,
        time_signature=4,
        beat_times=(0.0, 0.5),
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
        metadata={},
    )

    class FakeAudioPlayer:
        def __init__(self, *args, **kwargs):
            self.playing = True
            self.finished = True
            self.position_seconds = 0.0

        def play(self):
            self.playing = True

        def stop(self):
            self.playing = False

    runtime = MagicMock()
    runtime.tick.return_value = True
    runtime.stats = {"frames_sent": 1}

    from unittest.mock import patch

    service = SessionService()
    with patch("dreamsync.gui.services.session_service.ShowTimeline.from_json", return_value=timeline), patch(
        "dreamsync.gui.services.session_service.AudioPlayer",
        FakeAudioPlayer,
    ), patch("dreamsync.gui.services.session_service.ShowPlaybackRuntime", return_value=runtime):
        handle = service.start_precompiled_show_session(song, show_path)
        handle.wait(timeout=2)

    assert handle.session_ref[0] is not None
    assert handle.summary is not None
    assert handle.summary["mode"] == "saved_show"


def test_session_service_can_require_baked_precompiled_show_session(tmp_path: Path):
    song = tmp_path / "song.mp3"
    song.touch()
    show_path = tmp_path / "song.show.json"
    config_path = tmp_path / "devices.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            devices:
              - name: Test Strip
                address: 10.0.0.2
                type: lan
                segments: 1
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    timeline = ShowTimeline(
        song_path=str(song),
        duration=12.0,
        bpm=120.0,
        time_signature=4,
        beat_times=(0.0, 0.5),
        downbeat_times=(0.0,),
        cues=(ShowCue(0.0, "solid", ("#224466",), 1.0, 0.0, {}, "cut", 0),),
        metadata={},
    )
    timeline.to_json(show_path)
    BakedFrameArtifact(
        source_show_path=str(show_path),
        source_show_hash=sha256_file(show_path),
        device_config_path=str(config_path),
        device_config_hash=sha256_file(config_path),
        engine=default_engine_metadata(),
        settings=BakeSettings(),
        duration=12.0,
        nodes=(BakedFrameNode("10.0.0.2", "10.0.0.2"),),
        frames=(BakedFrame(0.0, ("#224466",)),),
        summary={"frame_count": 1, "node_count": 1, "cue_count": 1},
    ).to_json(default_baked_frame_path(show_path))

    class FakeAudioPlayer:
        def __init__(self, *args, **kwargs):
            self.playing = True
            self.finished = True
            self.position_seconds = 0.0

        def play(self):
            self.playing = True

        def stop(self):
            self.playing = False

    from unittest.mock import patch

    service = SessionService()
    with patch("dreamsync.gui.services.session_service.AudioPlayer", FakeAudioPlayer):
        handle = service.start_precompiled_show_session(
            song,
            show_path,
            config_path=config_path,
            baked_playback_mode="require",
        )
        handle.wait(timeout=2)

    assert handle.error is None
    assert handle.summary is not None
    assert handle.summary["playback_mode_used"] == "baked"
    assert handle.summary["baked_validation_valid"] is True
    assert handle.summary["frame_count"] == 1
    assert handle.summary["node_count"] == 1
    assert handle.summary["frame_lookup_count"] == 0
    assert handle.summary["frame_lookup_avg_ms"] == 0.0


def test_timeline_playback_session_caps_render_rate_from_adapter_fps(tmp_path: Path):
    class FakeAdapter:
        config = type("Config", (), {"fps": 20})()

    multi_adapter = type("Multi", (), {"devices": [(FakeAdapter(), object(), object(), 1.0, None)]})()
    timeline = ShowTimeline(
        song_path=str(tmp_path / "song.mp3"),
        duration=12.0,
        bpm=120.0,
        time_signature=4,
        beat_times=(0.0,),
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
        metadata={},
    )
    session = TimelinePlaybackSession(
        multi_adapter,
        tmp_path / "song.mp3",
        timeline,
        mode="saved_show",
    )

    assert session._target_frame_interval() == 0.05


def test_timeline_playback_session_uses_safe_default_render_rate(tmp_path: Path):
    multi_adapter = type("Multi", (), {"devices": []})()
    timeline = ShowTimeline(
        song_path=str(tmp_path / "song.mp3"),
        duration=12.0,
        bpm=120.0,
        time_signature=4,
        beat_times=(0.0,),
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
        metadata={},
    )
    session = TimelinePlaybackSession(
        multi_adapter,
        tmp_path / "song.mp3",
        timeline,
        mode="saved_show",
    )

    assert round(session._target_frame_interval(), 4) == round(1.0 / 30.0, 4)


def test_session_service_starts_reactive_live_session():
    ready = threading.Event()
    release = threading.Event()
    seen_kwargs = {}

    def fake_run_live_to_govee(*args, **kwargs):
        seen_kwargs.update(kwargs)
        ready.set()
        release.wait(timeout=2)
        return [], {"sent": 5, "beats": 2}

    from unittest.mock import patch

    service = SessionService()
    with patch("dreamsync.live.run_live_to_govee", side_effect=fake_run_live_to_govee):
        handle = service.start_reactive_live_session(
            render_mode="pulse",
            cycle_interval=22.0,
            telemetry_dir=Path("telemetry"),
            crossfade_detect=True,
        )
        assert ready.wait(timeout=2) is True
        assert handle.session_ref[0] is not None
        release.set()
        handle.wait(timeout=2)

    assert handle.summary is not None
    assert handle.summary["mode"] == "reactive_live"
    assert seen_kwargs["cycle_interval"] == 22.0
    assert seen_kwargs["crossfade_detect"] is True
    assert seen_kwargs["telemetry_dir"] == Path("telemetry")
    assert seen_kwargs["render_mode_policy"] == "adaptive"
    assert seen_kwargs["render_mode"] == "pulse"
    assert callable(seen_kwargs["downbeat_nudge_request_getter"])
    assert callable(seen_kwargs["cycle_tempo_multiplier_getter"])


def test_reactive_session_queues_monotonic_downbeat_nudge_revisions():
    adapter = type("Adapter", (), {"devices": []})()
    session = ReactiveLiveSession(adapter)

    assert session.downbeat_nudge_revision() == 0
    assert session.request_downbeat_nudge() == 1
    assert session.request_downbeat_nudge() == 2
    assert session.downbeat_nudge_revision() == 2
    revision, requested_at, kind = session.downbeat_nudge_request()
    assert revision == 2
    assert requested_at > 0.0
    assert kind == "downbeat"

    assert session.request_manual_beat("beat") == 3
    assert session.downbeat_nudge_request()[2] == "beat"
    assert session.request_detection_reset() == 4
    assert session.downbeat_nudge_request()[2] == "reset"
    assert session.cycle_tempo_multiplier() == 1.0
    assert session.set_cycle_tempo_multiplier(2.0) == 2.0
    assert session.cycle_tempo_multiplier() == 2.0
    session.request_detection_reset()
    assert session.cycle_tempo_multiplier() == 1.0


def test_reactive_snapshot_forwards_detected_chord_history_and_markers():
    adapter = type("Adapter", (), {"devices": []})()
    session = ReactiveLiveSession(adapter)
    session._update_runtime_state({
        "harmonic_enabled": True,
        "detected_chord": "G",
        "detected_chord_history": ("C", "Am", "F", "G"),
        "detected_bar_chord_history": ("C", "C", "F"),
        "detected_chord_changes": (
            (1.0, "Am"),
            (2.0, "F"),
            (3.0, "G"),
        ),
        "harmonic_debug_enabled": True,
        "harmonic_debug_spectrum": ((55.0, 0.2), (110.0, 1.0)),
        "harmonic_debug_chroma": (1.0,) + (0.0,) * 11,
        "harmonic_debug_chord": "C",
        "harmonic_debug_chord_tones": ("C", "E", "G"),
        "harmonic_debug_root_note": "C",
        "harmonic_debug_non_chord_tones": ("D",),
        "detected_chord_prediction": {
            "chord": "C",
            "t": 4.0,
            "confidence": 0.8,
        },
        "detected_chord_prediction_seconds": 1.0,
        "detected_chord_prediction_mismatch": {
            "predicted_chord": "F",
            "actual_chord": "G",
        },
        "detected_chord_prediction_mismatch_active": True,
        "predictive_cycle": {
            "available": True,
            "current_bar": 3,
            "cycle_bars": 4,
        },
    })

    snapshot = session.session_snapshot()

    assert snapshot["detected_chord"] == "G"
    assert snapshot["detected_chord_history"] == ("C", "Am", "F", "G")
    assert snapshot["detected_bar_chord_history"] == ("C", "C", "F")
    assert snapshot["detected_chord_changes"][-1] == (3.0, "G")
    assert snapshot["harmonic_debug_enabled"]
    assert snapshot["harmonic_debug_spectrum"][-1] == (110.0, 1.0)
    assert snapshot["harmonic_debug_chord_tones"] == ("C", "E", "G")
    assert snapshot["harmonic_debug_root_note"] == "C"
    assert snapshot["harmonic_debug_non_chord_tones"] == ("D",)
    assert snapshot["predictive_cycle"]["current_bar"] == 3
    assert snapshot["detected_chord_prediction"]["chord"] == "C"
    assert snapshot["detected_chord_prediction_seconds"] == 1.0
    assert snapshot["detected_chord_prediction_mismatch_active"]


def test_reactive_snapshot_promotes_structure_similarity_diagnostics():
    adapter = type("Adapter", (), {"devices": []})()
    session = ReactiveLiveSession(adapter)
    session._update_runtime_state(
        {
            "structure_similarity_enabled": True,
            "structure_configured_meter": (4, 4),
            "structure_current_bar": 7,
            "structure_phrase_hypotheses": (
                {
                    "bars": 4,
                    "probability": 0.76,
                    "source": "song_local_recurrence",
                },
            ),
            "structure_section_id": "B",
            "structure_top_matches": (
                {"right_bar": 3, "combined": 0.88},
            ),
            "structure_upcoming_boundaries": (
                {"target_bar": 8, "predicted_probability": 0.71},
            ),
            "structure_action_history": (),
        }
    )

    snapshot = session.session_snapshot()

    assert snapshot["structure_similarity_enabled"] is True
    assert snapshot["structure_configured_meter"] == (4, 4)
    assert snapshot["structure_current_bar"] == 7
    assert snapshot["structure_phrase_hypotheses"][0]["bars"] == 4
    assert snapshot["structure_section_id"] == "B"
    assert snapshot["structure_top_matches"][0]["right_bar"] == 3
    assert snapshot["structure_upcoming_boundaries"][0]["target_bar"] == 8


def test_reactive_snapshot_promotes_manual_downbeat_diagnostics():
    adapter = type("Adapter", (), {"devices": []})()
    session = ReactiveLiveSession(adapter)
    session._update_runtime_state(
        {
            "manual_downbeat_nudge_pending": True,
            "manual_downbeat_nudge_count": 2,
            "manual_downbeat_nudge_last_t": 12.5,
            "manual_downbeat_nudge_previous_phase": 2,
            "manual_downbeat_nudge_revision": 3,
            "manual_beat_markers": (
                {"t": 12.5, "kind": "downbeat", "beat_index": 20},
                {"t": 13.0, "kind": "beat", "beat_index": 21},
            ),
            "manual_meter_beats_per_bar": 4,
            "meter_time_signature": (4, 4),
            "detected_downbeat_times": (10.0, 12.0),
        }
    )

    snapshot = session.session_snapshot()

    assert snapshot["manual_downbeat_nudge_pending"] is True
    assert snapshot["manual_downbeat_nudge_count"] == 2
    assert snapshot["manual_downbeat_nudge_last_t"] == 12.5
    assert snapshot["manual_downbeat_nudge_previous_phase"] == 2
    assert snapshot["manual_downbeat_nudge_revision"] == 3
    assert snapshot["manual_beat_markers"][1]["kind"] == "beat"
    assert snapshot["manual_meter_beats_per_bar"] == 4
    assert snapshot["meter_time_signature"] == (4, 4)
    assert snapshot["detected_downbeat_times"] == (10.0, 12.0)


def test_reactive_snapshot_forwards_upcoming_timeline_contract():
    adapter = type("Adapter", (), {"devices": []})()
    session = ReactiveLiveSession(adapter)
    session._update_runtime_state(
        {
            "stream_t": 20.0,
            "automatic_detection_reset_count": 2,
            "last_detection_reset_reason": "silence",
            "predicted_beat_times": (
                {"t": 20.5, "downbeat": False, "beat_in_bar": 2},
                {"t": 22.0, "downbeat": True, "beat_in_bar": 1},
            ),
            "upcoming_effect_cues": (
                {
                    "t": 22.0,
                    "effect": "pulse",
                    "cue_class": "boundary",
                    "state": "armed",
                    "confidence": 0.75,
                },
            ),
            "active_effects": (
                {
                    "effect": "pulse",
                    "render_mode": "pulse",
                    "decay_seconds": 0.75,
                    "remaining_seconds": 0.5,
                },
            ),
            "effect_trigger_history": (
                {
                    "t": 19.5,
                    "effect": "wave_drift",
                    "render_mode": "wave",
                },
            ),
        }
    )

    snapshot = session.session_snapshot()

    assert snapshot["stream_t"] == 20.0
    assert snapshot["automatic_detection_reset_count"] == 2
    assert snapshot["last_detection_reset_reason"] == "silence"
    assert snapshot["predicted_beat_times"][1]["downbeat"] is True
    assert snapshot["upcoming_effect_cues"][0]["effect"] == "pulse"
    assert snapshot["active_effects"][0]["remaining_seconds"] == 0.5
    assert snapshot["effect_trigger_history"][0]["t"] == 19.5


def test_session_service_builds_seeded_generated_profile_chain():
    ready = threading.Event()
    release = threading.Event()

    def fake_run_live_to_govee(*_args, **_kwargs):
        ready.set()
        release.wait(timeout=2)
        return [], {"sent": 0}

    service = SessionService()
    with patch("dreamsync.live.run_live_to_govee", side_effect=fake_run_live_to_govee):
        handle = service.start_reactive_live_session(
            profile_strategy="smart_rotation",
            auto_palette=True,
            auto_palette_seed=42,
            auto_palette_pool_size=6,
            rotation_interval=120.0,
            chain_dwell_range_enabled=True,
            chain_min_dwell_seconds=30.0,
            chain_max_dwell_seconds=90.0,
            chain_blend_seconds=5.0,
        )
        assert ready.wait(timeout=2)
        chain = handle.session_ref[0]._profile_chain
        assert chain.seed == 42
        assert chain._config.min_profile_duration == 30.0
        assert chain._config.max_profile_duration == 90.0
        assert chain._config.blend_duration == 5.0
        assert len(chain._pool) == 6
        release.set()
        handle.wait(timeout=2)


def test_session_handle_pause_resume_and_toggle_delegate_to_active_session():
    handle = SessionHandle(
        mode="saved_show",
        stop_event=threading.Event(),
        thread=threading.Thread(target=lambda: None),
    )
    session = MagicMock()
    session.pause.return_value = True
    session.resume.return_value = True
    session.toggle_pause.return_value = "paused"
    handle.session_ref[0] = session

    assert handle.pause() is True
    assert handle.resume() is True
    assert handle.toggle_pause() == "paused"
