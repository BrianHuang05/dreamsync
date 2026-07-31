"""Phase A integration tests for bounded, capture-only reactive live mode."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from dreamsync.dsp.harmonic import LiveHarmonicState
from dreamsync.dsp.structure import LiveStructureEvent
from dreamsync.effects import EffectCycler
from dreamsync.live import (
    LiveStructureConfig,
    _advance_deadline,
    _audio_sample_at_monotonic,
    _live_render_interval,
    _nearest_downbeat_target,
    run_live_to_govee,
)
from dreamsync.output.null_adapter import NullMultiAdapter


class _FakeInputStream:
    def __init__(self, *, callback, channels: int, blocksize: int, **_kwargs) -> None:
        self._callback = callback
        self._channels = channels
        self._blocksize = blocksize

    def __enter__(self):
        for index in range(12):
            data = np.full(
                (self._blocksize, self._channels),
                0.05 if index % 2 else 0.02,
                dtype=np.float32,
            )
            self._callback(
                data,
                self._blocksize,
                SimpleNamespace(inputBufferAdcTime=index * 0.01),
                SimpleNamespace(input_overflow=False),
            )
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _CaptureOnlySoundDevice:
    def __init__(self) -> None:
        self.input_streams = 0
        self.output_streams = 0
        self.last_input_stream = None

    def InputStream(self, **kwargs):
        self.input_streams += 1
        self.last_input_stream = _FakeInputStream(**kwargs)
        return self.last_input_stream

    def OutputStream(self, **_kwargs):
        self.output_streams += 1
        raise AssertionError("reactive live mode must never open an OutputStream")


class _RecordingAdapter(NullMultiAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.params: list[dict] = []

    def send_frame(self, t, intent, beat=False, params=None) -> bool:
        self.params.append(dict(params or {}))
        return super().send_frame(t, intent, beat=beat, params=params)


def test_deadline_advance_skips_catchup_bursts() -> None:
    assert _advance_deadline(1.0, 0.1, 1.35) == 1.4


def test_live_render_interval_uses_default_for_null_adapter() -> None:
    assert round(_live_render_interval(NullMultiAdapter()), 6) == round(1.0 / 30.0, 6)


def test_downbeat_nudge_chooses_nearest_side_of_beat_midpoint() -> None:
    assert _nearest_downbeat_target(10.24, 10.0, 0.5) == "previous"
    assert _nearest_downbeat_target(10.25, 10.0, 0.5) == "previous"
    assert _nearest_downbeat_target(10.26, 10.0, 0.5) == "next"


def test_manual_keypress_maps_to_portaudio_adc_sample_not_display_time() -> None:
    sample = _audio_sample_at_monotonic(
        99.995,
        sample_rate=48_000,
        timing=(1000, 480, 10.0, 10.01, 100.0),
        captured_samples=1480,
        observed_at=100.2,
    )

    assert sample == 1240


def test_manual_keypress_fallback_uses_callback_clock_not_analysis_loop() -> None:
    sample = _audio_sample_at_monotonic(
        99.99,
        sample_rate=48_000,
        timing=(1000, 480, None, None, 100.0),
        captured_samples=1480,
        observed_at=100.2,
    )

    assert sample == 1000


def test_reactive_live_is_capture_only_and_reports_bounded_overflow() -> None:
    fake_sd = _CaptureOnlySoundDevice()
    adapter = NullMultiAdapter()
    states: list[dict] = []

    with patch("dreamsync.live._require_sounddevice", return_value=fake_sd):
        logs, summary = run_live_to_govee(
            adapter,
            duration_seconds=0.08,
            sample_rate=44_100,
            channels=1,
            frame_size=64,
            hop_size=16,
            blocksize=64,
            auto_cycle=False,
            state_callback=states.append,
            structure_config=LiveStructureConfig(
                harmonic_structure_enabled=True,
                harmonic_frame_size=64,
            ),
        )

    assert fake_sd.input_streams == 1
    assert fake_sd.output_streams == 0
    assert summary["audio_ring_capacity"] == 8
    assert summary["analysis_dropped_blocks"] == 4
    assert summary["analysis_discontinuities"] >= 0
    assert summary["returned_rows"] == len(logs)
    assert summary["returned_rows"] <= summary["max_returned_log_rows"]
    assert adapter._frames_sent <= 5
    assert states
    assert states[-1]["audio_ring_capacity"] == 8
    assert "audio_lag_ms" in states[-1]
    assert "analysis_frame_ms_p95" in states[-1]
    assert states[-1]["harmonic_enabled"]
    assert "harmonic_frame_ms_p95" in states[-1]
    assert states[-1]["harmonic_hop_ms"] == 0.363
    assert states[-1]["state_publish_interval_ms"] == 33.333
    assert summary["harmonic_enabled"]
    assert summary["harmonic_hop_ms"] == 0.363
    assert summary["state_publish_interval_ms"] == 33.333
    assert "meter_confident" in states[-1]
    # Phase C public downbeats come from the same confidence-gated meter state.
    assert "downbeat" in states[-1]
    assert "meter_downbeat" in states[-1]
    assert states[-1]["downbeat"] is states[-1]["meter_downbeat"]


def test_raw_visualizer_runs_without_tempo_detection() -> None:
    fake_sd = _CaptureOnlySoundDevice()
    adapter = _RecordingAdapter()
    states: list[dict] = []

    with (
        patch("dreamsync.live._require_sounddevice", return_value=fake_sd),
        patch(
            "dreamsync.live.ReactiveGroupPolicy",
        ) as group_policy_type,
        patch(
            "dreamsync.live.LiveBpmEstimator.update",
            autospec=True,
        ) as bpm_update,
    ):
        group_policy_type.return_value.descriptors = (object(),)
        logs, summary = run_live_to_govee(
            adapter,
            duration_seconds=0.08,
            sample_rate=44_100,
            channels=1,
            frame_size=64,
            hop_size=16,
            blocksize=64,
            auto_cycle=False,
            raw_visualizer=True,
            state_callback=states.append,
        )

    bpm_update.assert_not_called()
    group_policy_type.return_value.update.assert_not_called()
    assert logs
    assert summary["raw_visualizer"] is True
    assert summary["beats"] == 0
    assert states
    assert states[-1]["raw_visualizer"] is True
    assert states[-1]["raw_visualizer_levels"]
    assert adapter.params
    assert adapter.params[-1]["raw_visualizer_levels"]
    assert "target_groups" not in adapter.params[-1]
    assert "untargeted_behavior" not in adapter.params[-1]


def test_reactive_auto_cycle_keeps_timed_changes_enabled() -> None:
    fake_sd = _CaptureOnlySoundDevice()
    structure_controlled_values: list[bool] = []
    original_update = EffectCycler.update

    def recording_update(cycler, *args, **kwargs):
        structure_controlled_values.append(
            bool(kwargs.get("structure_controlled", False))
        )
        return original_update(cycler, *args, **kwargs)

    with (
        patch("dreamsync.live._require_sounddevice", return_value=fake_sd),
        patch(
            "dreamsync.live.EffectCycler.update",
            autospec=True,
            side_effect=recording_update,
        ),
    ):
        run_live_to_govee(
            NullMultiAdapter(),
            duration_seconds=0.08,
            sample_rate=44_100,
            channels=1,
            frame_size=64,
            hop_size=16,
            blocksize=64,
            auto_cycle=True,
            cycle_interval=0.01,
            structure_config=LiveStructureConfig(
                harmonic_frame_size=64,
                structure_similarity_enabled=True,
                structure_similarity_shadow_mode=False,
            ),
        )

    assert structure_controlled_values
    assert not any(structure_controlled_values)


def test_manual_downbeat_nudge_applies_on_next_detected_beat() -> None:
    fake_sd = _CaptureOnlySoundDevice()
    states: list[dict] = []

    def forced_beat(estimator, *_args, **_kwargs):
        estimator.last_onset = 1.0
        return 120.0, True

    with (
        patch("dreamsync.live._require_sounddevice", return_value=fake_sd),
        patch(
            "dreamsync.live.LiveBpmEstimator.update",
            autospec=True,
            side_effect=forced_beat,
        ),
    ):
        logs, summary = run_live_to_govee(
            NullMultiAdapter(),
            duration_seconds=0.08,
            sample_rate=44_100,
            channels=1,
            frame_size=64,
            hop_size=16,
            blocksize=64,
            auto_cycle=False,
            state_callback=states.append,
            downbeat_nudge_revision_getter=lambda: 1,
        )

    nudge_events = [
        row for row in logs
        if row.get("kind") == "manual_downbeat_nudge"
    ]
    assert len(nudge_events) == 1
    assert nudge_events[0]["bar_phase"] == 0
    assert summary["manual_downbeat_nudges"] == 1
    assert states[-1]["manual_downbeat_nudge_pending"] is False
    assert states[-1]["manual_downbeat_nudge_count"] == 1
    assert states[-1]["manual_downbeat_nudge_revision"] == 1
    marker = states[-1]["manual_beat_markers"][0]
    assert marker["kind"] == "downbeat"
    assert marker["t"] in states[-1]["detected_beat_times"]


def test_manual_secondary_beat_can_snap_to_nearest_previous_detection() -> None:
    fake_sd = _CaptureOnlySoundDevice()
    request_calls = 0
    states: list[dict] = []

    def forced_beat(estimator, *_args, **_kwargs):
        estimator.last_onset = 1.0
        return 120.0, True

    def request_after_first_processing_pass():
        nonlocal request_calls
        request_calls += 1
        if request_calls == 1:
            return 0, 0.0
        return 1, time.monotonic(), "beat"

    with (
        patch("dreamsync.live._require_sounddevice", return_value=fake_sd),
        patch(
            "dreamsync.live.LiveBpmEstimator.update",
            autospec=True,
            side_effect=forced_beat,
        ),
    ):
        logs, summary = run_live_to_govee(
            NullMultiAdapter(),
            duration_seconds=0.08,
            sample_rate=44_100,
            channels=1,
            frame_size=64,
            hop_size=16,
            blocksize=64,
            auto_cycle=False,
            state_callback=states.append,
            downbeat_nudge_request_getter=request_after_first_processing_pass,
        )

    latch_events = [
        row for row in logs
        if row.get("kind") == "manual_beat_latch"
    ]
    assert len(latch_events) == 1
    assert latch_events[0]["target"] == "previous"
    assert latch_events[0]["beat_kind"] == "beat"
    assert summary["manual_downbeat_nudges"] == 0
    assert summary["manual_beat_latches"] == 1
    marker = states[-1]["manual_beat_markers"][0]
    assert marker["t"] in states[-1]["detected_beat_times"]
    assert any(state["beat"] for state in states)


def test_manual_downbeat_can_confirm_nearest_previous_detection() -> None:
    fake_sd = _CaptureOnlySoundDevice()
    request_calls = 0
    states: list[dict] = []

    def forced_beat(estimator, *_args, **_kwargs):
        estimator.last_onset = 1.0
        return 120.0, True

    def request_after_first_processing_pass():
        nonlocal request_calls
        request_calls += 1
        if request_calls == 1:
            return 0, 0.0
        return 1, time.monotonic(), "downbeat"

    with (
        patch("dreamsync.live._require_sounddevice", return_value=fake_sd),
        patch(
            "dreamsync.live.LiveBpmEstimator.update",
            autospec=True,
            side_effect=forced_beat,
        ),
    ):
        logs, summary = run_live_to_govee(
            NullMultiAdapter(),
            duration_seconds=0.08,
            sample_rate=44_100,
            channels=1,
            frame_size=64,
            hop_size=16,
            blocksize=64,
            auto_cycle=False,
            state_callback=states.append,
            downbeat_nudge_request_getter=request_after_first_processing_pass,
        )

    nudge_events = [
        row for row in logs
        if row.get("kind") == "manual_downbeat_nudge"
    ]
    assert len(nudge_events) == 1
    assert nudge_events[0]["target"] == "previous"
    assert summary["manual_downbeat_nudges"] == 1
    marker = states[-1]["manual_beat_markers"][0]
    assert marker["kind"] == "downbeat"
    assert marker["t"] in states[-1]["detected_downbeat_times"]
    assert any(
        state["beat"] and state["downbeat"] and state["beat_in_bar"] == 0
        for state in states
    )


def test_manual_new_session_request_restarts_live_detection_state() -> None:
    fake_sd = _CaptureOnlySoundDevice()
    request_calls = 0

    def reset_after_first_processing_pass():
        nonlocal request_calls
        request_calls += 1
        if request_calls == 1:
            return 0, 0.0, "downbeat"
        if request_calls == 2:
            stream = fake_sd.last_input_stream
            data = np.full(
                (64, 1),
                0.04,
                dtype=np.float32,
            )
            stream._callback(
                data,
                64,
                SimpleNamespace(inputBufferAdcTime=1.0),
                SimpleNamespace(input_overflow=False),
            )
            return 1, time.monotonic(), "reset"
        return 1, time.monotonic(), "reset"

    with patch(
        "dreamsync.live._require_sounddevice",
        return_value=fake_sd,
    ):
        _logs, summary = run_live_to_govee(
            NullMultiAdapter(),
            duration_seconds=0.08,
            sample_rate=44_100,
            channels=1,
            frame_size=64,
            hop_size=16,
            blocksize=64,
            auto_cycle=False,
            downbeat_nudge_request_getter=(
                reset_after_first_processing_pass
            ),
        )

    assert summary["manual_detection_resets"] == 1
    assert summary["manual_beat_latches"] == 0
    assert summary["meter_time_signature"] == (4, 4)


def test_cycle_tempo_override_changes_detector_grid_but_not_raw_bpm() -> None:
    fake_sd = _CaptureOnlySoundDevice()
    states: list[dict] = []

    def forced_beat(estimator, *_args, **_kwargs):
        estimator.last_onset = 1.0
        return 120.0, True

    with (
        patch("dreamsync.live._require_sounddevice", return_value=fake_sd),
        patch(
            "dreamsync.live.LiveBpmEstimator.update",
            autospec=True,
            side_effect=forced_beat,
        ),
    ):
        _logs, summary = run_live_to_govee(
            NullMultiAdapter(),
            duration_seconds=0.08,
            sample_rate=44_100,
            channels=1,
            frame_size=64,
            hop_size=16,
            blocksize=64,
            auto_cycle=False,
            state_callback=states.append,
            cycle_tempo_multiplier_getter=lambda: 2.0,
        )

    assert summary["detected_bpm"] == 120.0
    assert summary["cycle_bpm"] == 240.0
    assert summary["cycle_tempo_multiplier"] == 2.0
    assert states[-1]["detected_bpm"] == 120.0
    assert states[-1]["cycle_bpm"] == 240.0
    assert states[-1]["bpm"] == 240.0


def test_phase_e_harmonic_change_reaches_lighting_as_small_live_accent() -> None:
    fake_sd = _CaptureOnlySoundDevice()
    adapter = _RecordingAdapter()
    event = LiveHarmonicState(
        t=0.1,
        chroma=(1.0,) + (0.0,) * 11,
        tonal_confidence=0.8,
        chord="C",
        chord_confidence=0.8,
        novelty=0.5,
        novelty_threshold=0.18,
        harmonic_change=True,
    )

    with (
        patch("dreamsync.live._require_sounddevice", return_value=fake_sd),
        patch(
            "dreamsync.live.LiveHarmonicAnalyzer.update",
            return_value=event,
        ),
    ):
        run_live_to_govee(
            adapter,
            duration_seconds=0.08,
            sample_rate=44_100,
            channels=1,
            frame_size=64,
            hop_size=16,
            blocksize=64,
            auto_cycle=False,
            structure_config=LiveStructureConfig(
                harmonic_structure_enabled=True,
                harmonic_frame_size=64,
                harmonic_hop_multiplier=4,
            ),
        )

    accents = [
        float(params["harmonic_accent"])
        for params in adapter.params
        if "harmonic_accent" in params
    ]
    assert fake_sd.output_streams == 0
    assert accents
    assert all(0.0 < accent <= 0.12 for accent in accents)
    assert any(params.get("harmonic_change") for params in adapter.params)
    assert any(
        params.get("detected_chord") == "C"
        and params.get("chord_change_anchor") == "initial"
        for params in adapter.params
    )


def test_phase_f_macro_event_remains_capture_only_and_reaches_renderer() -> None:
    fake_sd = _CaptureOnlySoundDevice()
    adapter = _RecordingAdapter()
    macro = LiveStructureEvent(
        kind="macro_change",
        t=0.2,
        confidence=0.8,
        harmonic_novelty=0.7,
        phase_confidence=0.8,
        bar_index=4,
        phrase_index=1,
        chord_before="C",
        chord_after="F",
    )

    with (
        patch("dreamsync.live._require_sounddevice", return_value=fake_sd),
        patch(
            "dreamsync.live.LiveStructureTracker.observe_harmonic",
            return_value=(macro,),
        ),
    ):
        _logs, summary = run_live_to_govee(
            adapter,
            duration_seconds=0.08,
            sample_rate=44_100,
            channels=1,
            frame_size=64,
            hop_size=16,
            blocksize=64,
            auto_cycle=False,
            structure_config=LiveStructureConfig(
                harmonic_structure_enabled=True,
                harmonic_frame_size=64,
                harmonic_hop_multiplier=4,
            ),
        )

    assert fake_sd.output_streams == 0
    assert summary["macro_changes"] > 0
    assert any(params.get("macro_change") for params in adapter.params)
    assert any(
        params.get("structure_event") == "macro_change"
        for params in adapter.params
    )


def test_debug_only_harmonic_analysis_never_changes_lighting_or_audio() -> None:
    fake_sd = _CaptureOnlySoundDevice()
    adapter = _RecordingAdapter()
    states: list[dict] = []
    event = LiveHarmonicState(
        t=0.1,
        chroma=(1.0,) + (0.0,) * 11,
        tonal_confidence=0.8,
        chord="C",
        chord_confidence=0.8,
        novelty=0.5,
        novelty_threshold=0.18,
        harmonic_change=True,
    )

    with (
        patch("dreamsync.live._require_sounddevice", return_value=fake_sd),
        patch(
            "dreamsync.live.LiveHarmonicAnalyzer.update",
            return_value=event,
        ),
    ):
        run_live_to_govee(
            adapter,
            duration_seconds=0.08,
            sample_rate=44_100,
            channels=1,
            frame_size=64,
            hop_size=16,
            blocksize=64,
            auto_cycle=False,
            state_callback=states.append,
            structure_config=LiveStructureConfig(
                harmonic_structure_enabled=False,
                harmonic_frame_size=64,
                harmonic_hop_multiplier=4,
                debug_harmonics=True,
            ),
        )

    assert fake_sd.output_streams == 0
    assert states[-1]["harmonic_debug_enabled"]
    assert states[-1]["harmonic_debug_chord"] == "C"
    assert all("harmonic_accent" not in params for params in adapter.params)
    assert all("macro_change" not in params for params in adapter.params)
