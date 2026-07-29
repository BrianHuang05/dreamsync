"""Editor-side spatial projection and strip-chain models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


SPATIAL_STEP = 0.05
SPATIAL_TOLERANCE = 1e-6
DIRECTION_VECTORS: dict[str, tuple[float, float, float]] = {
    "x+": (1.0, 0.0, 0.0),
    "x-": (-1.0, 0.0, 0.0),
    "y+": (0.0, 1.0, 0.0),
    "y-": (0.0, -1.0, 0.0),
    "z+": (0.0, 0.0, 1.0),
    "z-": (0.0, 0.0, -1.0),
}


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
    groups: tuple[str, ...] = ()
    exclude_groups: tuple[str, ...] = ()
    inherited_groups: tuple[str, ...] = ()

    @property
    def chain_key(self) -> str:
        return self.address if self.is_section else ""

    @property
    def effective_groups(self) -> frozenset[str]:
        return frozenset(
            (set(self.inherited_groups) | set(self.groups))
            - set(self.exclude_groups)
        )


@dataclass(frozen=True)
class ChainValidation:
    chain_key: str
    label: str
    valid: bool
    errors: tuple[str, ...] = ()
    invalid_links: tuple[tuple[str, str], ...] = ()


def group_section_chains(nodes: Iterable[SceneNode]) -> dict[str, list[SceneNode]]:
    chains: dict[str, list[SceneNode]] = {}
    for node in nodes:
        if not node.is_section or node.section_index is None:
            continue
        chains.setdefault(node.chain_key, []).append(node)
    for chain in chains.values():
        chain.sort(key=lambda item: item.section_index if item.section_index is not None else -1)
    return chains


def chain_center(nodes: Iterable[SceneNode]) -> tuple[float, float, float]:
    chain = list(nodes)
    if not chain:
        return (0.0, 0.0, 0.0)
    count = float(len(chain))
    return (
        sum(node.x for node in chain) / count,
        sum(node.y for node in chain) / count,
        sum(node.z for node in chain) / count,
    )


def is_cardinal_step(
    first: SceneNode,
    second: SceneNode,
    *,
    step: float = SPATIAL_STEP,
    tolerance: float = SPATIAL_TOLERANCE,
) -> bool:
    deltas = (abs(second.x - first.x), abs(second.y - first.y), abs(second.z - first.z))
    moving_axes = sum(1 for delta in deltas if abs(delta - step) <= tolerance)
    stationary_axes = sum(1 for delta in deltas if delta <= tolerance)
    return moving_axes == 1 and stationary_axes == 2


def validate_chain(
    nodes: Iterable[SceneNode],
    *,
    step: float = SPATIAL_STEP,
    tolerance: float = SPATIAL_TOLERANCE,
) -> ChainValidation:
    chain = sorted(
        list(nodes),
        key=lambda item: item.section_index if item.section_index is not None else -1,
    )
    if not chain:
        return ChainValidation("", "Strip", True)

    chain_key = chain[0].chain_key
    label = chain[0].physical_name or chain[0].label or chain_key
    errors: list[str] = []
    invalid_links: list[tuple[str, str]] = []
    expected_count = max(node.section_count for node in chain)
    actual_indices = [node.section_index for node in chain]
    expected_indices = list(range(expected_count))
    if actual_indices != expected_indices:
        errors.append(
            f"expected section indices 1-{expected_count}, found "
            + ", ".join(str((index or 0) + 1) for index in actual_indices)
        )

    for node in chain:
        if any(value < -1.0 - tolerance or value > 1.0 + tolerance for value in (node.x, node.y, node.z)):
            section_number = (node.section_index or 0) + 1
            errors.append(f"section {section_number} is outside room bounds")

    for first, second in zip(chain, chain[1:]):
        if is_cardinal_step(first, second, step=step, tolerance=tolerance):
            continue
        first_number = (first.section_index or 0) + 1
        second_number = (second.section_index or 0) + 1
        errors.append(
            f"sections {first_number}-{second_number} must be one {step:.2f} cardinal step apart"
        )
        invalid_links.append((first.key, second.key))

    return ChainValidation(
        chain_key=chain_key,
        label=label,
        valid=not errors,
        errors=tuple(errors),
        invalid_links=tuple(invalid_links),
    )


def validate_layout(nodes: Iterable[SceneNode]) -> list[ChainValidation]:
    return [validate_chain(chain) for chain in group_section_chains(nodes).values()]


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
