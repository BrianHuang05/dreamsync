from __future__ import annotations

from unittest.mock import patch

import pytest

from dreamsync.gui.services.output_lease_service import (
    AudioOutputInvariantError,
    OutputLeaseService,
)
from dreamsync.gui.services.session_service import SessionService
from dreamsync.show.models import ShowCue, ShowTimeline


def _timeline(*, duration: float = 0.01) -> ShowTimeline:
    return ShowTimeline(
        song_path="captured.mp3",
        duration=duration,
        bpm=120.0,
        time_signature=4,
        beat_times=(),
        downbeat_times=(),
        cues=(
            ShowCue(
                t=0.0,
                render_mode="solid",
                color_palette=("#112233",),
                intensity=1.0,
                speed=0.0,
                params={},
                transition="cut",
                transition_beats=0,
            ),
        ),
        metadata={},
    )


@pytest.mark.parametrize("owner", ["reactive", "raw_visualizer", "spotify_learned_live", "capture"])
def test_only_queue_can_acquire_audio_output(owner: str) -> None:
    leases = OutputLeaseService()

    with pytest.raises(AudioOutputInvariantError, match="Queue-only"):
        leases.acquire_audio(owner=owner, mode=owner)

    snapshot = leases.audio_snapshot()
    assert snapshot.owner == ""
    assert snapshot.violation_count == 1
    assert owner in snapshot.last_violation


def test_queue_audio_and_lighting_leases_are_independent() -> None:
    leases = OutputLeaseService()
    leases.acquire_lighting(
        owner="reactive",
        mode="reactive_live",
        simulation_only=True,
    )

    assert leases.audio_snapshot().owner == ""
    assert leases.lighting_snapshot().owner == "reactive"

    leases.release_lighting(owner="reactive")
    leases.acquire_audio(owner="queue", mode="saved_show")

    assert leases.lighting_snapshot().owner == ""
    assert leases.audio_snapshot().owner == "queue"


def test_non_queue_timeline_playback_fails_before_player_construction() -> None:
    service = SessionService()

    with patch("dreamsync.gui.services.session_service.AudioPlayer") as player:
        with pytest.raises(AudioOutputInvariantError, match="Queue-only"):
            service.start_timeline_playback_session(
                "captured.mp3",
                _timeline(),
                mode="simulation_preview",
            )

    player.assert_not_called()


def test_captured_show_lighting_preview_is_silent() -> None:
    service = SessionService()

    with patch("dreamsync.gui.services.session_service.AudioPlayer") as player:
        handle = service.start_timeline_preview_session(_timeline())
        handle.wait(timeout=2.0)

    player.assert_not_called()
    assert handle.error is None
    assert handle.summary is not None
    assert handle.summary["audio_output"] == "none (lighting-only preview)"
