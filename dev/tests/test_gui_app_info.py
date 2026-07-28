from __future__ import annotations

from pathlib import Path

from dreamsync.gui.services.app_info_service import AppInfoService


def test_app_info_support_text_contains_version_without_device_secrets(tmp_path: Path) -> None:
    config_path = tmp_path / "devices.yaml"
    info = AppInfoService().snapshot(config_path=config_path)

    support_text = info.support_text()

    assert info.version
    assert "Product: DreamSync" in support_text
    assert f"Version: {info.version}" in support_text
    assert f"Device config: {config_path}" in support_text
    assert "spotify" not in support_text.lower()
    assert "token" not in support_text.lower()
