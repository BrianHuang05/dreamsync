from __future__ import annotations

from dreamsync.output.govee_cloud import shutdown_active_dreamviews


def _device(*, sku: str, device: str, name: str, capabilities: list[dict]):
    return {
        "sku": sku,
        "device": device,
        "deviceName": name,
        "capabilities": capabilities,
    }


def test_shutdown_disables_scenic_and_physical_dreamviews():
    requests = []

    def request(method, path, headers, body):
        requests.append((method, path, headers, body))
        if method == "GET":
            return {"data": [
                _device(
                    sku="DreamViewScenic",
                    device="scenic-1",
                    name="Living room scenic",
                    capabilities=[{
                        "type": "devices.capabilities.on_off",
                        "instance": "powerSwitch",
                    }],
                ),
                _device(
                    sku="H6097",
                    device="tv-1",
                    name="TV Backlight",
                    capabilities=[{
                        "type": "devices.capabilities.toggle",
                        "instance": "dreamViewToggle",
                    }],
                ),
                _device(
                    sku="H6006",
                    device="bulb-1",
                    name="Bulb",
                    capabilities=[{
                        "type": "devices.capabilities.on_off",
                        "instance": "powerSwitch",
                    }],
                ),
            ]}
        return {"code": 200, "msg": "success"}

    result = shutdown_active_dreamviews(api_key="test-key", request=request)

    assert result.attempted is True
    assert result.disabled == ("Living room scenic", "TV Backlight")
    assert result.failures == ()
    controls = [body["payload"] for method, _path, _headers, body in requests if method == "POST"]
    assert controls == [
        {
            "sku": "DreamViewScenic",
            "device": "scenic-1",
            "capability": {
                "type": "devices.capabilities.on_off",
                "instance": "powerSwitch",
                "value": 1,
            },
        },
        {
            "sku": "H6097",
            "device": "tv-1",
            "capability": {
                "type": "devices.capabilities.toggle",
                "instance": "dreamViewToggle",
                "value": 0,
            },
        },
    ]


def test_shutdown_skips_when_no_key(monkeypatch):
    monkeypatch.delenv("GOVEE_API_KEY", raising=False)
    monkeypatch.delenv("DREAMSYNC_SECRETS_FILE", raising=False)

    result = shutdown_active_dreamviews()

    assert result.attempted is False


def test_shutdown_reads_key_from_configured_secrets_file(tmp_path, monkeypatch):
    secrets_file = tmp_path / "dreamsync.secrets.env"
    secrets_file.write_text("# Local only\nexport GOVEE_API_KEY='file-key'\n", encoding="utf-8")
    monkeypatch.delenv("GOVEE_API_KEY", raising=False)
    monkeypatch.setenv("DREAMSYNC_SECRETS_FILE", str(secrets_file))
    observed_headers = []

    def request(method, path, headers, body):
        observed_headers.append(headers)
        return {"data": []}

    result = shutdown_active_dreamviews(request=request)

    assert result.attempted is True
    assert observed_headers == [{"Govee-API-Key": "file-key"}]


def test_shutdown_reports_control_failures_without_stopping_other_devices():
    def request(method, path, headers, body):
        if method == "GET":
            return {"data": [
                _device(
                    sku="DreamViewScenic",
                    device="scenic-1",
                    name="Scenic",
                    capabilities=[{
                        "type": "devices.capabilities.on_off",
                        "instance": "powerSwitch",
                    }],
                ),
                _device(
                    sku="H6601",
                    device="music-1",
                    name="Music puck",
                    capabilities=[{
                        "type": "devices.capabilities.toggle",
                        "instance": "dreamViewToggle",
                    }],
                ),
            ]}
        if body["payload"]["device"] == "scenic-1":
            return {"code": 500, "msg": "temporary failure"}
        return {"code": 200, "msg": "success"}

    result = shutdown_active_dreamviews(api_key="test-key", request=request)

    assert result.disabled == ("Music puck",)
    assert result.failures == ("Scenic: temporary failure",)
