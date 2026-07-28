"""Application version and privacy-safe support information."""

from __future__ import annotations

import platform
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path


@dataclass(frozen=True)
class AppInfo:
    product_name: str
    version: str
    python_version: str
    platform_name: str
    config_path: str = ""

    def support_text(self) -> str:
        lines = [
            f"Product: {self.product_name}",
            f"Version: {self.version}",
            f"Python: {self.python_version}",
            f"Platform: {self.platform_name}",
        ]
        if self.config_path:
            lines.append(f"Device config: {self.config_path}")
        return "\n".join(lines)


class AppInfoService:
    """Resolve installed metadata with a source-tree fallback."""

    distribution_name = "dreamsync-music-sync"

    def version(self) -> str:
        try:
            return metadata.version(self.distribution_name)
        except metadata.PackageNotFoundError:
            from dreamsync import __version__

            return __version__

    def snapshot(self, *, config_path: Path | None = None) -> AppInfo:
        return AppInfo(
            product_name="DreamSync",
            version=self.version(),
            python_version=platform.python_version(),
            platform_name=platform.platform(),
            config_path=str(config_path) if config_path is not None else "",
        )
