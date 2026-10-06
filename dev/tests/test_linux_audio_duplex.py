"""Signal and orchestration checks for the one-terminal hardware diagnostic."""
import importlib.util
import os
from pathlib import Path
import wave

import numpy as np
import pytest


spec = importlib.util.spec_from_file_location(
    "linux_audio_duplex", Path(__file__).parents[1] / "scripts/test_linux_audio_duplex.py")
duplex = importlib.util.module_from_spec(spec)
spec.loader.exec_module(duplex)


def wav(path, frequency, *, amplitude=0.1, extra=0, dropout=False):
    t = np.arange(4 * duplex.RATE) / duplex.RATE
    pcm = amplitude * np.sin(2 * np.pi * frequency * t)
    pcm += extra * np.sin(2 * np.pi * (880 if frequency == 440 else 440) * t)
    if dropout:
        pcm[2 * duplex.RATE:3 * duplex.RATE] = 0
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(duplex.RATE)
        stereo = np.repeat(pcm[:, None], 2, axis=1)
        audio.writeframes((stereo * 32767).astype("<i2").tobytes())


@pytest.mark.parametrize("amplitude,frequency,extra,dropout,passed", [
    (0.1, 440, 0, False, True),
    (0, 440, 0, False, False),
    (0.1, 880, 0, False, False),
    (0.1, 440, 0.03, False, False),
    (0.1, 440, 0, True, False),
])
def test_signal_requires_correct_sustained_isolated_tone(
    tmp_path, amplitude, frequency, extra, dropout, passed
):
    path = tmp_path / "capture.wav"
    wav(path, frequency, amplitude=amplitude, extra=extra, dropout=dropout)
    assert duplex.signal_check(path, 440, 880)["passed"] is passed


def test_route_parser_ignores_unrelated_peak_meter_and_missing_pid():
    text = '''Source Output #16347
    Source: 15675
    Properties:
        application.process.id = "100"
Source Output #16394
    Source: 15675
    Properties:
        application.process.id = "200"
Source Output #17495
    Source: 4294967295
    Properties:
        application.process.id = "100"
Source Output #17500
    Source: 15672
'''
    assert duplex.stream_targets(text, 200, "Source") == ["15675"]
    assert duplex.stream_targets(text, 300, "Source") == []


class Process:
    def __init__(self, pid, recorder=False):
        self.pid = pid
        self.recorder = recorder
        self.returncode = None
        self.polls = 0
        self.terminated = False

    def poll(self):
        self.polls += 1
        if self.recorder and self.polls >= 10:
            self.returncode = 0
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout):
        return self.returncode


@pytest.fixture
def hardware(tmp_path, monkeypatch):
    test = duplex.DuplexTest(tmp_path)
    test.streams = {}
    test.silent_duplex = False
    test.wrong_duplex_route = False
    test.player_open = False
    physical = "alsa_output.usb.test.analog-stereo"
    capture = "alsa_output.platform-snd_aloop.0.dreamsync-playback"
    arguments = type("Args", (), {"physical_sink": physical, "capture_sink": None,
                                 "capture_source": None})()

    def pactl(*args):
        # Change the numeric endpoint indices between every snapshot. Route
        # verification must resolve each current snapshot's symbolic names.
        base = test.snapshot_number * 100 + 10
        maps = {"sinks": {physical: str(base), capture: str(base + 1)},
                "sources": {physical + ".monitor": str(base),
                            capture + ".monitor": str(base + 1)}}
        if args == ("info",):
            return "Server Name: PulseAudio (on PipeWire)"
        if args[1] == "short":
            return "\n".join(f"{index}\t{name}\tPipeWire" for name, index in maps[args[2]].items())
        kind = args[1]
        if kind in maps:
            return ""
        prefix, field, names = (("Sink Input", "Sink", maps["sinks"]) if kind == "sink-inputs"
                                else ("Source Output", "Source", maps["sources"]))
        blocks = []
        for pid, (stream_kind, endpoint) in test.streams.items():
            if kind == stream_kind:
                index = names[endpoint]
                if test.wrong_duplex_route and test.player_open and kind == "sink-inputs" \
                        and pid == os.getpid() and any(
                            name == "tone-880" for name in test.labels):
                    index = names[capture]
                blocks.append(f'{prefix} #{pid}\n\t{field}: {index}\n\tProperties:\n'
                              f'\t\tapplication.process.id = "{pid}"\n')
        return "\n".join(blocks)

    test.labels = []

    def spawn(label, args):
        proc = Process(100000 + len(test.children), recorder=not label.startswith("tone"))
        test.children.append(proc)
        test.labels.append(label)
        if label.startswith("tone"):
            test.streams[proc.pid] = ("sink-inputs", capture)
        else:
            source = args[args.index("-i") + 1]
            test.streams[proc.pid] = ("source-outputs", source)
            frequency = 880 if label == "concurrent-capture" else 440
            amplitude = 0 if test.silent_duplex and label == "concurrent-playback" else 0.1
            wav(Path(args[-1]), frequency, amplitude=amplitude)
        return proc

    class Player:
        def __init__(self, path):
            # Playback comes from the captured recording, never an external file.
            assert path == tmp_path / "replay.wav"
            assert len(duplex.read_pcm(path)) == 20 * duplex.RATE

        def play(self):
            assert os.environ["PULSE_SINK"] == physical
            test.player_open = True
            test.streams[os.getpid()] = ("sink-inputs", physical)

        def stop(self):
            test.player_open = False
            test.streams.pop(os.getpid(), None)

    import dreamsync.show.player
    monkeypatch.setattr(dreamsync.show.player, "AudioPlayer", Player)
    monkeypatch.setattr(test, "pactl", pactl)
    monkeypatch.setattr(test, "spawn", spawn)
    monkeypatch.setattr(duplex.time, "sleep", lambda seconds: None)
    # Restore caller's process environment after the diagnostic changes it.
    monkeypatch.setenv("PULSE_SINK", "original")
    monkeypatch.setenv("PULSE_SOURCE", "original")
    return test, arguments


def test_all_stages_complete_and_cleanup_owned_streams(hardware):
    test, arguments = hardware
    try:
        test.run(arguments)
    finally:
        test.close()
    assert test.report["status"] == "PASS"
    assert len(test.report["checks"]) == 4
    assert test.labels == ["tone-440", "initial-capture", "baseline-playback",
                           "tone-880", "concurrent-capture", "concurrent-playback"]
    assert all(proc.returncode is not None for proc in test.children)
    assert not test.player_open


@pytest.mark.parametrize("failure,message", [
    ("silent_duplex", "concurrent-playback: silent"),
    ("wrong_duplex_route", "Wrong route"),
])
def test_duplex_failures_are_not_hidden_by_successful_baseline(hardware, failure, message):
    test, arguments = hardware
    setattr(test, failure, True)
    try:
        with pytest.raises(RuntimeError, match=message):
            test.run(arguments)
    finally:
        test.close()
    assert test.report["status"] == "FAIL"
    assert all(proc.returncode is not None for proc in test.children)
    assert not test.player_open


def test_missing_loopback_fails_before_spawning(hardware):
    test, arguments = hardware
    arguments.capture_sink = "missing"
    with pytest.raises(RuntimeError, match="existing Loopback"):
        test.run(arguments)
    assert test.children == []
