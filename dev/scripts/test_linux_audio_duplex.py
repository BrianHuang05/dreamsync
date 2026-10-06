"""Exercise actual Linux audio streams; no GUI, Spotify account, or lights required."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback
import wave

import numpy as np

RATE = 48000
SECONDS = 4
ROOT = Path(__file__).resolve().parents[2]


def endpoints(text: str) -> dict[str, str]:
    """Resolve names to this snapshot's indices, never persisted indices."""
    return {fields[1]: fields[0] for line in text.splitlines()
            if len(fields := line.split()) >= 2}


def stream_targets(text: str, pid: int, field: str) -> list[str]:
    # Legacy pactl text works on Ubuntu 22.04, without pactl's JSON support.
    targets = []
    for block in re.split(r"(?m)^(?:Sink Input|Source Output) #\d+\s*$", text)[1:]:
        process = re.search(r'application\.process\.id\s*=\s*"(\d+)"', block)
        target = re.search(rf"(?m)^\s*{field}:\s*(\d+)\s*$", block)
        if process and int(process[1]) == pid and target:
            targets.append(target[1])
    return targets


def read_pcm(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as audio:
        if audio.getsampwidth() != 2 or audio.getframerate() != RATE:
            raise RuntimeError(f"Unexpected PCM format: {path}")
        channels = audio.getnchannels()
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
    return samples.astype(np.float32).reshape(-1, channels).mean(axis=1) / 32768


def signal_check(path: Path, expected: int, unwanted: int) -> dict:
    """Require sustained expected tone; silence, wrong tones and leakage fail."""
    pcm = read_pcm(path)
    # Inspect each middle second separately: a brief good burst cannot mask a dropout.
    windows = [pcm[RATE:2 * RATE], pcm[2 * RATE:3 * RATE]]
    if len(pcm) < int(3.5 * RATE):
        raise RuntimeError(f"Incomplete recording ({len(pcm) / RATE:.2f}s): {path.name}")
    measurements = []
    for window in windows:
        power = np.abs(np.fft.rfft(window * np.hanning(len(window)))) ** 2
        frequencies = np.fft.rfftfreq(len(window), 1 / RATE)
        wanted = float(power[np.abs(frequencies - expected) <= 5].sum())
        other = float(power[np.abs(frequencies - unwanted) <= 5].sum())
        total = float(power.sum())
        rms = float(np.sqrt(np.mean(window ** 2)))
        measurements.append({"rms_dbfs": 20 * np.log10(max(rms, 1e-12)),
                             "tone_fraction": wanted / max(total, 1e-20),
                             "unwanted_fraction": other / max(wanted, 1e-20)})
    passed = all(m["rms_dbfs"] > -60 and m["tone_fraction"] > 0.75
                 and m["unwanted_fraction"] < 0.01 for m in measurements)
    return {"file": path.name, "expected_hz": expected, "passed": passed,
            "windows": measurements}


class DuplexTest:
    def __init__(self, directory: Path):
        self.directory = directory
        self.children: list[subprocess.Popen] = []
        self.logs = []
        self.env = {**os.environ, "LC_ALL": "C"}
        self.env.pop("DISPLAY", None)
        self.player = None
        self.report: dict = {"status": "FAIL", "checks": []}
        self.snapshot_number = 0

    def pactl(self, *args: str) -> str:
        result = subprocess.run(["pactl", *args], env=self.env, capture_output=True,
                                text=True, timeout=5)
        if result.returncode:
            raise RuntimeError(f"pactl {' '.join(args)}: {result.stderr.strip()}")
        return result.stdout

    def snapshot(self, label: str) -> dict[str, str]:
        self.snapshot_number += 1
        data = {kind: self.pactl("list", kind) for kind in
                ("sinks", "sources", "sink-inputs", "source-outputs")}
        data["sink_ids"] = self.pactl("list", "short", "sinks")
        data["source_ids"] = self.pactl("list", "short", "sources")
        path = self.directory / f"{self.snapshot_number:03d}-{label}.json"
        path.write_text(json.dumps(data, indent=2))
        return data

    def spawn(self, label: str, args: list[str]) -> subprocess.Popen:
        log = (self.directory / f"{label}.log").open("wb")
        self.logs.append(log)
        proc = subprocess.Popen(["ffmpeg", "-hide_banner", "-nostdin", "-y", *args],
                                env=self.env, stdout=log, stderr=subprocess.STDOUT)
        self.children.append(proc)
        self.report.setdefault("processes", []).append(
            {"label": label, "pid": proc.pid, "args": args})
        return proc

    def tone(self, frequency: int, sink: str) -> subprocess.Popen:
        return self.spawn(f"tone-{frequency}", [
            "-re", "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate={RATE}",
            "-t", "30", "-ac", "2", "-ar", str(RATE),
            "-f", "pulse", "-device", sink, "DreamSync SSH duplex tone"])

    def record(self, label: str, source: str) -> subprocess.Popen:
        return self.spawn(label, [
            "-f", "pulse", "-sample_rate", str(RATE), "-channels", "2",
            "-fragment_size", "4096", "-i", source, "-t", str(SECONDS),
            "-ac", "2", "-ar", str(RATE), "-c:a", "pcm_s16le",
            str(self.directory / f"{label}.wav")])

    def routes(self, requests: list[tuple[str, int, str]], label: str) -> bool:
        data = self.snapshot(label)
        for kind, pid, name in requests:
            field, mapping = (("Sink", "sink_ids") if kind == "sink-inputs"
                              else ("Source", "source_ids"))
            index = endpoints(data[mapping]).get(name)
            if index is None:
                raise RuntimeError(f"Endpoint disappeared: {name}")
            targets = stream_targets(data[kind], pid, field)
            if not targets:
                return False
            if any(target != index for target in targets):
                raise RuntimeError(f"Wrong route: PID {pid} {kind} targets {targets}; "
                                   f"expected {name} (current index {index})")
        return True

    def wait_routes(self, requests, processes, label):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            self.alive(processes)
            if self.routes(requests, label):
                print(f"  PASS: {label} stream destinations", flush=True)
                return
            time.sleep(0.2)
        raise RuntimeError(f"No matching active stream for {label}; see route snapshots")

    @staticmethod
    def alive(processes):
        for proc in processes:
            if proc.poll() is not None:
                raise RuntimeError(f"FFmpeg PID {proc.pid} exited early ({proc.returncode}); "
                                   "see its process log")

    @staticmethod
    def stop(proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)

    def wait_recordings(self, recorders, ongoing, routes, label):
        deadline = time.monotonic() + 15
        observations = 0
        while any(proc.poll() is None for proc in recorders):
            self.alive(ongoing)
            if time.monotonic() > deadline:
                raise RuntimeError(f"{label} capture timed out; see FFmpeg logs")
            # Source and playback streams must remain on the intended sinks
            # throughout capture, not just during their startup snapshots.
            if not self.routes(routes, label):
                raise RuntimeError(f"{label} source/playback stream disappeared during capture")
            observations += 1
            time.sleep(0.4)
        if any(proc.returncode for proc in recorders):
            raise RuntimeError(f"{label} FFmpeg recording failed; see process logs")
        if observations < 2:
            raise RuntimeError(f"{label} did not observe sustained concurrent streams")

    def check(self, label, expected, unwanted):
        result = signal_check(self.directory / f"{label}.wav", expected, unwanted)
        self.report["checks"].append(result)
        level = min(m["rms_dbfs"] for m in result["windows"])
        print(f"  {'PASS' if result['passed'] else 'FAIL'}: {label}: "
              f"{expected} Hz, minimum RMS {level:.1f} dBFS", flush=True)
        if not result["passed"]:
            raise RuntimeError(f"{label}: silent, wrong tone, leakage, or dropout; see report.json")

    def play(self, path: Path):
        from dreamsync.show.player import AudioPlayer

        self.player = AudioPlayer(path)
        self.player.play()

    def stop_player(self):
        if self.player is not None:
            try:
                self.player.stop()
            finally:
                self.player = None

    def run(self, args):
        self.pactl("info")
        sinks = endpoints(self.pactl("list", "short", "sinks"))
        sources = endpoints(self.pactl("list", "short", "sources"))
        physical = args.physical_sink
        if physical not in sinks or not physical.startswith("alsa_output."):
            raise RuntimeError(f"Physical ALSA sink unavailable: {physical}; "
                               "inspect pactl list short sinks")
        candidates = [name for name in sinks if re.search(
            r"(snd[_-]?aloop|alsa[_-]?loopback|loopback)", name, re.I)]
        capture_sink = args.capture_sink
        if not capture_sink:
            if len(candidates) != 1:
                raise RuntimeError(f"Select --capture-sink; Loopback sinks: {candidates}")
            capture_sink = candidates[0]
        if capture_sink not in candidates or capture_sink == physical:
            raise RuntimeError("Capture must be an existing Loopback sink distinct from physical")
        source = args.capture_source or f"{capture_sink}.monitor"
        if source not in sources or f"{physical}.monitor" not in sources:
            raise RuntimeError(f"Missing capture source or physical monitor: {source}")
        if source == f"{physical}.monitor":
            raise RuntimeError("Capture source must differ from the physical output monitor")
        self.report["endpoints"] = {"physical": physical, "capture_sink": capture_sink,
                                    "capture_source": source}
        os.environ["PULSE_SINK"] = physical
        os.environ["PULSE_SOURCE"] = source
        print(f"Playback: {physical}\nCapture:  {source}", flush=True)

        print("[1/3] Capture a 440 Hz source tone through Loopback (~5 seconds).", flush=True)
        tone = self.tone(440, capture_sink)
        source_route = [("sink-inputs", tone.pid, capture_sink)]
        self.wait_routes(source_route, [tone], "source-440")
        recorder = self.record("initial-capture", source)
        self.wait_routes([("source-outputs", recorder.pid, source)],
                         [tone, recorder], "initial-recorder")
        self.wait_recordings([recorder], [tone], source_route, "initial-capture")
        self.stop(tone)
        self.check("initial-capture", 440, 880)

        # Repeat the actual recording so all timed monitor captures fit inside
        # playback. No hand-picked file or timing of another terminal is needed.
        pcm = read_pcm(self.directory / "initial-capture.wav")
        replay = np.tile(pcm, int(np.ceil(20 * RATE / len(pcm))))[:20 * RATE]
        playback_file = self.directory / "replay.wav"
        with wave.open(str(playback_file), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(RATE)
            audio.writeframes((np.clip(replay, -1, 1) * 32767).astype("<i2").tobytes())

        player_route = [("sink-inputs", os.getpid(), physical)]
        print("[2/3] DreamSync playback alone, measured at physical monitor (~5 seconds).",
              flush=True)
        self.play(playback_file)
        self.wait_routes(player_route, [], "baseline-player")
        recorder = self.record("baseline-playback", f"{physical}.monitor")
        self.wait_routes([("source-outputs", recorder.pid, f"{physical}.monitor")],
                         [recorder], "baseline-recorder")
        self.wait_recordings([recorder], [], player_route, "baseline-playback")
        self.stop_player()
        self.check("baseline-playback", 440, 880)

        print("[3/3] Concurrent 880 Hz capture and recorded 440 Hz playback (~5 seconds).",
              flush=True)
        tone = self.tone(880, capture_sink)
        source_route = [("sink-inputs", tone.pid, capture_sink)]
        self.wait_routes(source_route, [tone], "source-880")
        self.play(playback_file)
        self.wait_routes(player_route, [tone], "concurrent-player")
        incoming = self.record("concurrent-capture", source)
        outgoing = self.record("concurrent-playback", f"{physical}.monitor")
        self.wait_routes([("source-outputs", incoming.pid, source),
                          ("source-outputs", outgoing.pid, f"{physical}.monitor")],
                         [tone, incoming, outgoing], "concurrent-recorders")
        self.wait_recordings([incoming, outgoing], [tone], source_route + player_route,
                             "concurrent-streams")
        self.stop_player()
        self.stop(tone)
        self.check("concurrent-capture", 880, 440)
        self.check("concurrent-playback", 440, 880)
        self.report["status"] = "PASS"

    def close(self):
        errors = []
        try:
            self.stop_player()
        except Exception as exc:
            errors.append(f"AudioPlayer cleanup: {exc}")
        for proc in self.children:
            try:
                self.stop(proc)
            except Exception as exc:
                errors.append(f"PID {proc.pid} cleanup: {exc}")
        for log in self.logs:
            try:
                log.close()
            except Exception as exc:
                errors.append(f"Log cleanup: {exc}")
        if errors:
            self.report["status"] = "FAIL"
            self.report["cleanup_errors"] = errors
            print(f"FAIL: cleanup: {'; '.join(errors)}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical-sink", required=True, help="exact physical ALSA sink name")
    parser.add_argument("--capture-sink", default=os.environ.get("DREAMSYNC_ALOOP_PLAYBACK_SINK"))
    parser.add_argument("--capture-source", default=os.environ.get("DREAMSYNC_ALOOP_CAPTURE_SOURCE"))
    parser.add_argument("--output-dir", type=Path, help="new directory for logs and test WAVs")
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.error("Run this on the Linux audio host, as its desktop user (SSH is supported)")
    for command in ("pactl", "ffmpeg", "ffprobe"):
        if not shutil.which(command):
            parser.error(f"Required command unavailable: {command}")
    directory = args.output_dir or ROOT / "logs/audio-duplex" / (
        datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}")
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    test = DuplexTest(directory)
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    print(f"Diagnostics: {directory}", flush=True)
    try:
        test.run(args)
    except (Exception, KeyboardInterrupt) as exc:
        test.report["error"] = str(exc) or "Interrupted"
        (directory / "error.log").write_text(traceback.format_exc())
        print(f"FAIL: {test.report['error']}", flush=True)
        try:
            test.snapshot("failure")
        except Exception:
            pass
    finally:
        test.close()
        (directory / "report.json").write_text(json.dumps(test.report, indent=2))
    if test.report["status"] == "PASS":
        print("PASS: capture and real DreamSync playback work simultaneously at the Pulse "
              "monitors. Confirm speaker audibility in person.", flush=True)
    print(f"Results: {directory / 'report.json'}", flush=True)
    return 0 if test.report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
