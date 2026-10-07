"""Verify frame pacing and payload behavior without lighting hardware."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def script():
    path = Path(__file__).parents[1] / "scripts" / "test_lan_update_rates.py"
    spec = importlib.util.spec_from_file_location("lan_rate_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def target(ip="192.0.2.1"):
    return dict(name="strip", address=ip, segments=12, transport="razer")


def simulate(script, rate, delay=0, fail=False):
    now = [0.0]
    packets = []

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def sendto(self, payload, destination):
            import base64
            msg = json.loads(payload)["msg"]
            frame = msg["cmd"] == "razer" and base64.b64decode(msg["data"]["pt"])[3] == 0xB0
            if frame:
                packets.append((now[0], base64.b64decode(msg["data"]["pt"])))
                now[0] += delay
                if fail:
                    raise OSError("simulated send failure")

    def sleep(seconds):
        now[0] += seconds

    rows = script.run_stage([target()], rate, 2, "walk", clock=lambda: now[0],
                            sleep=sleep, socket_factory=lambda *args: Socket())
    return rows[0], packets


@pytest.mark.parametrize("rate", [5, 10, 20, 30])
def test_requested_rate_changes_actual_frames_and_markers(script, rate):
    row, packets = simulate(script, rate)
    assert row["frames_sent"] == rate * 2
    assert row["observed_send_hz"] == pytest.approx(rate)
    assert row["gap_ms_p95"] == pytest.approx(1000 / rate)
    assert row["skipped_frame_slots"] == 0
    for index, (_, packet) in enumerate(packets):
        rgb = [tuple(packet[n:n + 3]) for n in range(6, len(packet) - 1, 3)]
        assert [i for i, color in enumerate(rgb) if color != (0, 0, 0)] == [index % 12]


def test_slow_sender_skips_slots_instead_of_catchup_burst(script):
    row, packets = simulate(script, 20, delay=.12)
    assert row["skipped_frame_slots"] > 0
    assert row["frames_sent"] < 40
    assert all(b[0] - a[0] >= .119 for a, b in zip(packets, packets[1:]))


def test_udp_failures_are_reported_not_counted_as_success(script):
    row, _ = simulate(script, 5, fail=True)
    assert row["frames_sent"] == 0
    assert row["send_errors"] == 10
    assert row["send_ms_median"] is None


def test_config_excludes_ble_disabled_and_preserves_transport(script, tmp_path):
    config = tmp_path / "devices.yaml"
    config.write_text('''devices:
- {name: overhead, address: 192.0.2.1, type: lan, transport: razer, segments: 25}
- {name: blinds, address: 192.0.2.2, type: auto, transport: ptreal, segments: 12}
- {name: bulb, address: 'AA:BB:CC:DD:EE:FF', type: ble}
- {name: disabled, address: 192.0.2.3, type: lan, enabled: false}
''')
    rows = script.load_targets(config)
    assert [(r["name"], r["segments"], r["transport"]) for r in rows] == [
        ("overhead", 25, "razer"), ("blinds", 12, "ptreal")]


def test_sweep_saves_all_phases_and_rates(script, tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    monkeypatch.setattr(script, "load_targets", lambda path: [target(), target("192.0.2.2")])
    calls = []
    def stage(targets, rate, seconds, pattern):
        calls.append((len(targets), rate))
        return [{"frames_sent": 10, "send_errors": 0} for t in targets]
    monkeypatch.setattr(script, "run_stage", stage)
    monkeypatch.setattr("sys.argv", ["test", "--rates", "5", "10", "--output", str(output)])
    assert script.main() == 0
    assert calls == [(1, 5), (1, 10), (1, 5), (1, 10), (2, 5), (2, 10)]
    assert len(json.loads(output.read_text())["results"]) == 8
