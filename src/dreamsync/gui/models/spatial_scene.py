"""Editor-side spatial projection models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectionConfig:
    origin_x: float = 240.0
    origin_y: float = 220.0
    scale_x: float = 140.0
    scale_y: float = 120.0
    depth_x: float = 64.0
    depth_y: float = 28.0


@dataclass(frozen=True)
class SceneNode:
    key: str
    label: str
    x: float
    y: float
    z: float = 0.0
    color: str = "#4f7fda"
    selected: bool = False
    address: str = ""
    physical_name: str = ""
    section_index: int | None = None
    section_count: int = 1
    is_section: bool = False


def project_point(
    x: float,
    y: float,
    z: float = 0.0,
    *,
    config: ProjectionConfig | None = None,
) -> tuple[float, float]:
    """Project a normalized room coordinate onto a fixed camera canvas."""
    cfg = config or ProjectionConfig()
    screen_x = cfg.origin_x + (x * cfg.scale_x) + (z * cfg.depth_x)
    screen_y = cfg.origin_y - (y * cfg.scale_y) + (z * cfg.depth_y)
    return (screen_x, screen_y)
