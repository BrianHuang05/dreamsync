from __future__ import annotations

import textwrap
import threading
from pathlib import Path
from unittest.mock import MagicMock

from dreamsync.gui.services.device_service import DeviceService
from dreamsync.gui.services.profile_service import ProfileService
from dreamsync.gui.services.queue_service import QueueService
from dreamsync.gui.services.session_service import SessionHandle, SessionService
from dreamsync.gui.services.show_service import ShowService
from dreamsync.gui.settings import GuiSettings, GuiSettingsStore
from dreamsync.local_session import LocalPlaylistSession
from dreamsync.playlist import PlaylistManager
from dreamsync.show.models import ShowCue, ShowTimeline
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
