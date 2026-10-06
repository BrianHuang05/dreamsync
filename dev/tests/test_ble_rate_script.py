"""Config selection and sweep behavior of the manual BLE benchmark."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def script():
    path = Path(__file__).parents[1] / "scripts" / "test_ble_update_rates.py"
    spec = importlib.util.spec_from_file_location("ble_rate_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_config_selection_preserves_protocol_and_skips_lan_disabled(script, tmp_path):
    path = tmp_path / "devices.yaml"
    path.write_text('''devices:
  - {name: bulb, address: "AA:BB", type: ble, protocol: bulb, segments: 1}
  - {name: strip, address: "CC:DD", type: auto, protocol: segment, segments: 7}
  - {name: lan, address: "192.168.1.4", type: auto}
  - {name: disabled, address: "EE:FF", type: ble, enabled: false}
''')
    targets = script.load_targets(SimpleNamespace(config=path))
    assert [(t.name, t.protocol.value, t.segments) for t in targets] == [
        ("bulb", "bulb", 1), ("strip", "segment", 7),
    ]


def test_config_sweep_continues_after_failure_and_runs_fleet(script, tmp_path, monkeypatch):
    path = tmp_path / "devices.yaml"
    path.write_text('''devices:
  - {name: first, address: "AA", type: ble, protocol: bulb}
  - {name: second, address: "BB", type: ble, protocol: segment}
''')
    output = tmp_path / "results.json"
    calls = []
    def stage(args, rate, targets):
        addresses = [t.address for t in targets]
        calls.append((rate, addresses))
        if addresses == ["AA"]:
            raise RuntimeError("unreachable")
        return [{"address": a, "write_errors": 0, "observed_disconnects": 0,
                 "connected_at_end": True, "color_writes": 10} for a in addresses]
    monkeypatch.setattr(script, "run_stage", stage)
    monkeypatch.setattr("sys.argv", ["test", "--config", str(path), "--rates", "5", "10",
                                     "--output", str(output)])
    assert script.main() == 1
    assert calls == [(5, ["AA"]), (5, ["BB"]), (10, ["BB"]),
                     (5, ["AA", "BB"]), (10, ["AA", "BB"])]
    report = json.loads(output.read_text())
    assert report["failures"][0]["addresses"] == ["AA"]
    assert {r["phase"] for r in report["results"]} == {"individual", "together"}


def test_explicit_address_mode_remains_supported(script):
    targets = script.load_targets(SimpleNamespace(
        config=None, address=["aa", "AA"], protocol="bulb", segments=1,
    ))
    assert len(targets) == 1
    assert targets[0].address == "AA"
