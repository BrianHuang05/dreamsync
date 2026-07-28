from __future__ import annotations

from pathlib import Path

from dreamsync.gui.services.queue_service import QueueService
from dreamsync.gui.services.runtime_supervisor import PipelineCoordinator, RuntimeSupervisor
from dreamsync.gui.models.reactive_settings import ReactiveSettings
from dreamsync.spotify.models import PlaybackState, QueueSnapshot, SpotifyTrack


def _track(track_id: str, name: str, duration_ms: int) -> SpotifyTrack:
    return SpotifyTrack(
        track_id=track_id,
        name=name,
        artist=f"Artist {track_id}",
        album=f"Album {track_id}",
        duration_ms=duration_ms,
        uri=f"spotify:track:{track_id}",
    )


class FakeWatcher:
    def __init__(self, playback_state, queue) -> None:
        self._snapshot = {
            "playback_state": playback_state,
            "queue": queue,
        }

    def snapshot(self) -> dict[str, object]:
        return dict(self._snapshot)


class FakeCapture:
    def __init__(self, *, config, on_segment_saved) -> None:
        self.config = config
        self.on_segment_saved = on_segment_saved
        self.started = False
        self.shutdown_called = False
        self.timing_updates: list[dict] = []
        self.track_changes: list[dict] = []
        self.periodic_fetcher = None
        self.periodic_stop_count = 0

    def start(self) -> None:
        self.started = True

    def shutdown(self) -> None:
        self.shutdown_called = True

    def update_timing(self, timing_data: dict) -> int:
        self.timing_updates.append(timing_data)
        return len(timing_data.get("song_durations", ()))

    def on_track_change(self, timing_data: dict) -> int:
        self.track_changes.append(timing_data)
        return 1

    def start_periodic_timing(self, fetcher) -> None:
        self.periodic_fetcher = fetcher

    def stop_periodic_timing(self) -> None:
        self.periodic_stop_count += 1
        self.periodic_fetcher = None

    @property
    def stats(self) -> dict[str, int]:
        return {}


class FakeWorker:
    def __init__(self, **kwargs) -> None:
        self.shutdown_called = False

    def shutdown(self) -> None:
        self.shutdown_called = True

    def pending_count(self) -> int:
        return 0

    def stats(self) -> dict[str, int]:
        return {}


class FakeAudioDeviceService:
    def list_output_options(self):
        return ()

    def list_input_options(self):
        return ()


class FakePipeline:
    def __init__(self) -> None:
        self.running = False
        self.timing_sources = []
        self.track_changes = []

    def set_timing_source(self, fetcher) -> None:
        self.timing_sources.append(fetcher)

    def start(self, **kwargs) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def on_track_change(self, timing_data: dict) -> int:
        self.track_changes.append(timing_data)
        return 1


def test_spotify_timing_payloads_match_capture_orchestrator_contract() -> None:
    old = _track("old", "Old Song", 180_000)
    current = _track("current", "Current Song", 210_000)
    upcoming = _track("next", "Next Song", 240_000)
    playback = PlaybackState(
        is_playing=True,
        track=current,
        progress_ms=12_500,
        timestamp=1.0,
        device_name="Speakers",
        shuffle=False,
        repeat="off",
    )
    queue = QueueSnapshot(
        currently_playing=current,
        queue=(upcoming,),
        fetched_at=1.0,
    )

    changed = QueueService.spotify_track_change_timing(current, old)
    periodic = QueueService.spotify_capture_timing_snapshot(FakeWatcher(playback, queue))

    assert changed["current_playback_time"] == 0.0
    assert changed["previous_song"]["song_title"] == "Old Song"
    assert changed["current_song"]["song_title"] == "Current Song"
    assert periodic is not None
    assert periodic["song_durations"] == [210.0, 240.0]
    assert periodic["current_playback_time"] == 12.5
    assert [song["song_title"] for song in periodic["songs"]] == ["Current Song", "Next Song"]


def test_pipeline_coordinator_activates_timing_source_and_forwards_track_change(tmp_path: Path) -> None:
    captures: list[FakeCapture] = []

    def capture_factory(**kwargs):
        capture = FakeCapture(**kwargs)
        captures.append(capture)
        return capture

    timing_data = {
        "song_durations": [210.0],
        "current_playback_time": 12.5,
        "current_song": {"song_title": "Current Song", "artist": "Artist", "album": "Album"},
    }
    coordinator = PipelineCoordinator(
        capture_factory=capture_factory,
        worker_factory=FakeWorker,
        cache_factory=lambda _path: object(),
    )
    coordinator.set_timing_source(lambda: timing_data)
    coordinator.start(capture_dir=tmp_path / "captures")

    capture = captures[0]
    assert capture.timing_updates == [timing_data]
    assert capture.periodic_fetcher is not None
    assert coordinator.on_track_change({"previous_song": {"song_title": "Old Song"}}) == 1
    assert capture.track_changes[-1]["previous_song"]["song_title"] == "Old Song"

    coordinator.set_timing_source(None)
    assert capture.periodic_stop_count == 1
    assert capture.periodic_fetcher is None


def test_runtime_supervisor_supports_timing_source_before_or_after_capture(tmp_path: Path) -> None:
    pipelines: list[FakePipeline] = []

    def pipeline_factory():
        pipeline = FakePipeline()
        pipelines.append(pipeline)
        return pipeline

    supervisor = RuntimeSupervisor(
        pipeline_factory=pipeline_factory,
        audio_device_service=FakeAudioDeviceService(),
    )
    first_fetcher = lambda: {"song_durations": [100.0]}
    second_fetcher = lambda: {"song_durations": [200.0]}

    supervisor.set_capture_timing_source(first_fetcher)
    supervisor.start_capture_pipeline(capture_dir=tmp_path / "captures")
    pipeline = pipelines[0]
    assert pipeline.timing_sources == [first_fetcher]

    supervisor.set_capture_timing_source(second_fetcher)
    assert pipeline.timing_sources[-1] is second_fetcher
    assert supervisor.notify_capture_track_change({"previous_song": {"song_title": "Old"}}) == 1
    assert pipeline.track_changes[-1]["previous_song"]["song_title"] == "Old"

    supervisor.set_capture_timing_source(None)
    assert pipeline.timing_sources[-1] is None


def test_reactive_settings_validate_master_brightness() -> None:
    assert ReactiveSettings(master_brightness=0.5, mirror=False).validate() == ()
    assert "master brightness" in ReactiveSettings(master_brightness=0.01).validate()[0]


def test_reactive_settings_validate_generated_pool_and_dwell_range() -> None:
    valid = ReactiveSettings(
        profile_strategy="smart_rotation",
        auto_palette=True,
        auto_palette_seed=42,
        auto_palette_pool_size=6,
        chain_dwell_range_enabled=True,
        chain_min_dwell_seconds=30.0,
        chain_max_dwell_seconds=90.0,
    )

    assert valid.validate() == ()
    assert any(
        "pool size" in error
        for error in ReactiveSettings(auto_palette_pool_size=1).validate()
    )
    assert any(
        "cannot exceed" in error
        for error in ReactiveSettings(
            chain_dwell_range_enabled=True,
            chain_min_dwell_seconds=90.0,
            chain_max_dwell_seconds=30.0,
        ).validate()
    )
