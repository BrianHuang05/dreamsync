"""Simple fixed-camera spatial canvas widget."""

from __future__ import annotations

from dreamsync.gui.models.spatial_scene import (
    ProjectionConfig,
    SceneNode,
    group_section_chains,
    project_point,
    validate_layout,
)


def _all_nodes_at_origin(nodes: list[SceneNode]) -> bool:
    return bool(nodes) and all(node.x == 0.0 and node.y == 0.0 and node.z == 0.0 for node in nodes)


def _spatial_hint_text(nodes: list[SceneNode]) -> str:
    if not nodes:
        return "No devices loaded from the current config."
    if _all_nodes_at_origin(nodes):
        return "All devices are still at the default origin (0.0, 0.0, 0.0). Add spatial coordinates to spread them out."
    return ""


def _visible_axes_for_view(view_mode: str) -> tuple[str, str] | None:
    if view_mode == "xy":
        return ("x", "y")
    if view_mode == "xz":
        return ("x", "z")
    if view_mode == "yz":
        return ("z", "y")
    return None


def _cycle_axis_name(axis_name: str) -> str:
    axis_order = ("x", "y", "z")
    try:
        index = axis_order.index(axis_name)
    except ValueError:
        return "x"
    return axis_order[(index + 1) % len(axis_order)]


def _cycle_view_mode(view_mode: str) -> str:
    view_order = ("room", "xy", "xz", "yz")
    try:
        index = view_order.index(view_mode)
    except ValueError:
        return "room"
    return view_order[(index + 1) % len(view_order)]


def _chain_links(nodes: list[SceneNode]) -> list[tuple[SceneNode, SceneNode]]:
    links: list[tuple[SceneNode, SceneNode]] = []
    for chain in group_section_chains(nodes).values():
        links.extend(zip(chain, chain[1:]))
    return links


def _chain_representatives(nodes: list[SceneNode]) -> list[SceneNode]:
    representatives: list[SceneNode] = []
    for chain in group_section_chains(nodes).values():
        if chain:
            representatives.append(chain[0])
    return representatives


def _project_node_for_view(node: SceneNode, *, view_mode: str, config: ProjectionConfig) -> tuple[float, float]:
    if view_mode == "xy":
        return (
            config.origin_x + (node.x * config.scale_x),
            config.origin_y - (node.y * config.scale_y),
        )
    if view_mode == "xz":
        return (
            config.origin_x + (node.x * config.scale_x),
            config.origin_y - (node.z * config.scale_y),
        )
    if view_mode == "yz":
        return (
            config.origin_x + (node.z * config.scale_x),
            config.origin_y - (node.y * config.scale_y),
        )
    return project_point(node.x, node.y, node.z, config=config)


def _axis_endpoints_for_node(node: SceneNode, *, axis_name: str, view_mode: str, config: ProjectionConfig) -> tuple[tuple[float, float], tuple[float, float]]:
    start = SceneNode(
        key=node.key,
        label=node.label,
        x=node.x,
        y=node.y,
        z=node.z,
    )
    end = SceneNode(
        key=node.key,
        label=node.label,
        x=node.x,
        y=node.y,
        z=node.z,
    )
    if axis_name == "x":
        start = SceneNode(key=node.key, label=node.label, x=-1.0, y=node.y, z=node.z)
        end = SceneNode(key=node.key, label=node.label, x=1.0, y=node.y, z=node.z)
    elif axis_name == "y":
        start = SceneNode(key=node.key, label=node.label, x=node.x, y=-1.0, z=node.z)
        end = SceneNode(key=node.key, label=node.label, x=node.x, y=1.0, z=node.z)
    else:
        start = SceneNode(key=node.key, label=node.label, x=node.x, y=node.y, z=-1.0)
        end = SceneNode(key=node.key, label=node.label, x=node.x, y=node.y, z=1.0)
    return (
        _project_node_for_view(start, view_mode=view_mode, config=config),
        _project_node_for_view(end, view_mode=view_mode, config=config),
    )


def build_spatial_canvas(qt_modules, nodes: list[SceneNode], *, interactive: bool = True, background_theme: str = "dark"):
    QtCore = qt_modules.QtCore
    QtGui = qt_modules.QtGui
    QtWidgets = qt_modules.QtWidgets
    cfg = ProjectionConfig()

    class SpatialCanvas(QtWidgets.QWidget):
        nodeSelected = QtCore.Signal(str)
        nodeAxisCycleRequested = QtCore.Signal(str, str)
        nodeDragged = QtCore.Signal(str, float, float)
        selectionStepRequested = QtCore.Signal(int)
        axisNudgeRequested = QtCore.Signal(str, int)
        viewCycleRequested = QtCore.Signal(str)

        def __init__(self) -> None:
            super().__init__()
            self._nodes = list(nodes)
            self._active_axis = "x"
            self._view_mode = "room"
            self._background_theme = background_theme
            self._interactive = interactive
            self._individual_node_editing = False
            self._strip_render_mode = "bounds" if interactive else "segments"
            self._drag_key = ""
            self._last_drag_pos = None
            self.setObjectName("spatialCanvas" if interactive else "simulationSpatialCanvas")
            self.setMinimumSize(420, 320)
            self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
            self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.PreventContextMenu)

        def set_nodes(self, next_nodes: list[SceneNode]) -> None:
            self._nodes = list(next_nodes)
            self.update()

        def set_active_axis(self, axis_name: str) -> None:
            self._active_axis = axis_name
            self.update()

        def set_view_mode(self, view_mode: str) -> None:
            self._view_mode = view_mode
            self.update()

        def set_individual_node_editing(self, enabled: bool) -> None:
            self._individual_node_editing = bool(enabled)
            self.update()

        def set_strip_render_mode(self, mode: str) -> None:
            self._strip_render_mode = (
                "bounds" if str(mode).strip().lower() == "bounds" else "segments"
            )
            self.update()

        def strip_render_mode(self) -> str:
            return self._strip_render_mode

        def _renders_strip_segments(self) -> bool:
            return (
                self._individual_node_editing
                or self._strip_render_mode == "segments"
            )

        def set_background_theme(self, background_name: str) -> None:
            self._background_theme = "light" if background_name == "light" else "dark"
            self.update()

        def current_view_mode(self) -> str:
            return self._view_mode

        def _hit_test(self, point) -> SceneNode | None:
            # Point bulbs are rendered above strip boxes, so give them first
            # refusal when their hit areas overlap.
            best_node = None
            best_distance = 16.0
            for node in self._nodes:
                if node.is_section:
                    continue
                px, py = _project_node_for_view(node, view_mode=self._view_mode, config=cfg)
                dx = float(point.x()) - px
                dy = float(point.y()) - py
                distance = (dx * dx + dy * dy) ** 0.5
                if distance <= best_distance:
                    best_node = node
                    best_distance = distance
            if best_node is not None:
                return best_node
            if not self._individual_node_editing:
                for node in _chain_representatives(self._nodes):
                    chain = [candidate for candidate in self._nodes if candidate.chain_key == node.chain_key]
                    if not chain:
                        continue
                    projected = [
                        _project_node_for_view(candidate, view_mode=self._view_mode, config=cfg)
                        for candidate in chain
                    ]
                    min_x = min(value[0] for value in projected) - 12.0
                    max_x = max(value[0] for value in projected) + 12.0
                    min_y = min(value[1] for value in projected) - 12.0
                    max_y = max(value[1] for value in projected) + 12.0
                    if min_x <= float(point.x()) <= max_x and min_y <= float(point.y()) <= max_y:
                        return node
            best_node = None
            best_distance = 16.0
            for node in self._nodes:
                px, py = _project_node_for_view(node, view_mode=self._view_mode, config=cfg)
                dx = float(point.x()) - px
                dy = float(point.y()) - py
                distance = (dx * dx + dy * dy) ** 0.5
                if distance <= best_distance:
                    best_node = node
                    best_distance = distance
            return best_node

        def _selected_node(self) -> SceneNode | None:
            return next((node for node in self._nodes if node.selected), None)

        def event(self, event):  # pragma: no cover - Qt only
            if self._interactive and event.type() == QtCore.QEvent.Type.KeyPress:
                modifiers = event.modifiers()
                if (
                    event.key() in (QtCore.Qt.Key.Key_Tab, QtCore.Qt.Key.Key_Backtab)
                    and not modifiers & QtCore.Qt.KeyboardModifier.ControlModifier
                ):
                    step = -1 if (
                        event.key() == QtCore.Qt.Key.Key_Backtab
                        or modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier
                    ) else 1
                    self.selectionStepRequested.emit(step)
                    event.accept()
                    return True
                if modifiers & QtCore.Qt.KeyboardModifier.ControlModifier:
                    selected = self._selected_node()
                    if event.key() == QtCore.Qt.Key.Key_A and selected is not None:
                        self.nodeAxisCycleRequested.emit(selected.key, self._next_axis_name())
                        event.accept()
                        return True
                    if event.key() == QtCore.Qt.Key.Key_V:
                        self.viewCycleRequested.emit(_cycle_view_mode(self._view_mode))
                        event.accept()
                        return True
                if self._view_mode == "room":
                    if event.key() == QtCore.Qt.Key.Key_Left:
                        self.axisNudgeRequested.emit(self._active_axis, -1)
                        event.accept()
                        return True
                    if event.key() == QtCore.Qt.Key.Key_Right:
                        self.axisNudgeRequested.emit(self._active_axis, 1)
                        event.accept()
                        return True
                else:
                    visible_axes = _visible_axes_for_view(self._view_mode)
                    if visible_axes is not None:
                        horizontal_axis, vertical_axis = visible_axes
                        key_axis_direction = {
                            QtCore.Qt.Key.Key_Left: (horizontal_axis, -1),
                            QtCore.Qt.Key.Key_Right: (horizontal_axis, 1),
                            QtCore.Qt.Key.Key_Up: (vertical_axis, 1),
                            QtCore.Qt.Key.Key_Down: (vertical_axis, -1),
                        }.get(event.key())
                        if key_axis_direction is not None:
                            self.axisNudgeRequested.emit(*key_axis_direction)
                            event.accept()
                            return True
            return super().event(event)

        def _next_axis_name(self) -> str:
            visible_axes = _visible_axes_for_view(self._view_mode)
            if visible_axes is None:
                return _cycle_axis_name(self._active_axis)
            if self._active_axis not in visible_axes:
                return visible_axes[0]
            return visible_axes[(visible_axes.index(self._active_axis) + 1) % len(visible_axes)]

        def mousePressEvent(self, event) -> None:  # pragma: no cover - Qt only
            if not self._interactive:
                return
            self.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)
            hit = self._hit_test(event.position())
            if hit is None:
                self._drag_key = ""
                self._last_drag_pos = None
                return
            if event.button() == QtCore.Qt.MouseButton.RightButton:
                self._drag_key = ""
                self._last_drag_pos = None
                self.nodeAxisCycleRequested.emit(hit.key, self._next_axis_name())
                event.accept()
                return
            if event.button() != QtCore.Qt.MouseButton.LeftButton:
                return
            self._drag_key = hit.key
            self._last_drag_pos = event.position()
            self.nodeSelected.emit(hit.key)

        def mouseMoveEvent(self, event) -> None:  # pragma: no cover - Qt only
            if not self._interactive:
                return
            if not self._drag_key or self._last_drag_pos is None:
                return
            delta_x = float(event.position().x() - self._last_drag_pos.x())
            delta_y = float(event.position().y() - self._last_drag_pos.y())
            self._last_drag_pos = event.position()
            self.nodeDragged.emit(self._drag_key, delta_x, delta_y)

        def mouseReleaseEvent(self, _event) -> None:  # pragma: no cover - Qt only
            if not self._interactive:
                return
            self._drag_key = ""
            self._last_drag_pos = None

        def _draw_plane_grid(self, painter) -> None:
            visible_axes = _visible_axes_for_view(self._view_mode)
            if visible_axes is None:
                return
            painter.setPen(QtGui.QPen(QtGui.QColor("#243346"), 1.0))
            horizontal_axis, vertical_axis = visible_axes
            tick_values = (-1.0, -0.5, 0.0, 0.5, 1.0)
            for value in tick_values:
                if horizontal_axis == "x":
                    start = SceneNode(key="", label="", x=value, y=-1.0, z=-1.0)
                    end = SceneNode(key="", label="", x=value, y=1.0, z=1.0)
                elif horizontal_axis == "z":
                    start = SceneNode(key="", label="", x=-1.0, y=-1.0, z=value)
                    end = SceneNode(key="", label="", x=1.0, y=1.0, z=value)
                else:
                    start = SceneNode(key="", label="", x=-1.0, y=value, z=-1.0)
                    end = SceneNode(key="", label="", x=1.0, y=value, z=1.0)
                sx, sy = _project_node_for_view(start, view_mode=self._view_mode, config=cfg)
                ex, ey = _project_node_for_view(end, view_mode=self._view_mode, config=cfg)
                painter.drawLine(QtCore.QPointF(sx, 40.0), QtCore.QPointF(sx, self.height() - 40.0))
                if vertical_axis == "y":
                    vertical_y = cfg.origin_y - (value * cfg.scale_y)
                else:
                    vertical_y = cfg.origin_y - (value * cfg.scale_y)
                painter.drawLine(QtCore.QPointF(40.0, vertical_y), QtCore.QPointF(self.width() - 40.0, vertical_y))

        def _draw_axes(self, painter) -> None:
            origin_x = int(cfg.origin_x)
            origin_y = int(cfg.origin_y)
            if self._view_mode == "room":
                painter.drawLine(40, origin_y, max(380, self.width() - 40), origin_y)
                painter.drawLine(origin_x, 40, origin_x, max(280, self.height() - 40))
                painter.drawText(max(270, self.width() - 150), origin_y - 5, "x (left/right)")
                painter.drawText(origin_x + 4, 52, "y (height)")
                painter.drawText(60, 80, "z (depth)")
                painter.setPen(QtGui.QPen(QtGui.QColor("#627da1")))
                painter.drawText(origin_x + 10, origin_y - 10, "origin (0, 0, 0)")
                return

            visible_axes = _visible_axes_for_view(self._view_mode)
            if visible_axes is None:
                return
            horizontal_axis, vertical_axis = visible_axes
            painter.drawLine(40, origin_y, max(380, self.width() - 40), origin_y)
            painter.drawLine(origin_x, 40, origin_x, max(280, self.height() - 40))
            painter.drawText(max(350, self.width() - 70), origin_y - 5, horizontal_axis)
            painter.drawText(origin_x + 4, 52, vertical_axis)
            painter.setPen(QtGui.QPen(QtGui.QColor("#627da1")))
            painter.drawText(origin_x + 10, origin_y - 10, f"{self._view_mode.upper()} plane")

        def _draw_selected_guides(self, painter) -> None:
            selected = next((node for node in self._nodes if node.selected), None)
            if selected is None:
                return
            px, py = _project_node_for_view(selected, view_mode=self._view_mode, config=cfg)
            painter.setPen(QtGui.QPen(QtGui.QColor("#587291"), 1.0, QtCore.Qt.PenStyle.DotLine))
            painter.drawLine(QtCore.QPointF(40.0, py), QtCore.QPointF(self.width() - 40.0, py))
            painter.drawLine(QtCore.QPointF(px, 40.0), QtCore.QPointF(px, self.height() - 40.0))
            axis_color = QtGui.QColor("#f4b125" if self._active_axis == "x" else "#1f95fa" if self._active_axis == "y" else "#e6e005")
            painter.setPen(QtGui.QPen(axis_color, 2.0))
            start, end = _axis_endpoints_for_node(selected, axis_name=self._active_axis, view_mode=self._view_mode, config=cfg)
            painter.drawLine(QtCore.QPointF(*start), QtCore.QPointF(*end))

        def _draw_strip_links(self, painter, *, dark_theme: bool) -> None:
            invalid_links = {
                link
                for validation in validate_layout(self._nodes)
                for link in validation.invalid_links
            }
            selected = self._selected_node()
            selected_chain_key = selected.chain_key if selected is not None else ""
            for first, second in _chain_links(self._nodes):
                first_point = _project_node_for_view(first, view_mode=self._view_mode, config=cfg)
                second_point = _project_node_for_view(second, view_mode=self._view_mode, config=cfg)
                link_key = (first.key, second.key)
                if link_key in invalid_links:
                    color = QtGui.QColor("#ef5350")
                    width = 3.0
                elif selected_chain_key and first.chain_key == selected_chain_key:
                    color = QtGui.QColor("#ffd166")
                    width = 3.0
                else:
                    color = QtGui.QColor("#7894b8" if dark_theme else "#66758a")
                    width = 2.0
                painter.setPen(QtGui.QPen(color, width))
                painter.drawLine(QtCore.QPointF(*first_point), QtCore.QPointF(*second_point))

        def _draw_strip_boxes(self, painter, *, dark_theme: bool) -> None:
            selected = self._selected_node()
            selected_chain_key = selected.chain_key if selected is not None else ""
            for chain in group_section_chains(self._nodes).values():
                if not chain:
                    continue
                projected = [
                    _project_node_for_view(node, view_mode=self._view_mode, config=cfg)
                    for node in chain
                ]
                min_x = min(value[0] for value in projected) - 12.0
                max_x = max(value[0] for value in projected) + 12.0
                min_y = min(value[1] for value in projected) - 12.0
                max_y = max(value[1] for value in projected) + 12.0
                chain_selected = bool(selected_chain_key and chain[0].chain_key == selected_chain_key)
                color = QtGui.QColor("#ffd166" if chain_selected else "#7894b8" if dark_theme else "#66758a")
                painter.setBrush(QtGui.QColor(chain[0].color))
                painter.setPen(QtGui.QPen(color, 3.0 if chain_selected else 2.0))
                painter.drawRect(QtCore.QRectF(min_x, min_y, max_x - min_x, max_y - min_y))
                painter.setPen(QtGui.QPen(QtGui.QColor("#d8e1ee" if dark_theme else "#1b2431")))
                painter.drawText(
                    QtCore.QPointF(min_x + 4.0, min_y - 4.0),
                    chain[0].physical_name or chain[0].label,
                )

        def _draw_strip_segment_labels(self, painter, *, dark_theme: bool) -> None:
            painter.setPen(
                QtGui.QPen(
                    QtGui.QColor("#d8e1ee" if dark_theme else "#1b2431")
                )
            )
            for chain in group_section_chains(self._nodes).values():
                if not chain:
                    continue
                midpoint = chain[len(chain) // 2]
                px, py = _project_node_for_view(
                    midpoint,
                    view_mode=self._view_mode,
                    config=cfg,
                )
                painter.drawText(
                    QtCore.QPointF(px + 8.0, py - 9.0),
                    chain[0].physical_name or chain[0].label,
                )

        def paintEvent(self, _event) -> None:  # pragma: no cover - Qt only
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            dark_theme = self._background_theme != "light"
            background = QtGui.QColor("#11161f" if dark_theme else "#f7f8fb")
            axis_color = QtGui.QColor("#3e4f68" if dark_theme else "#a8b6c7")
            text_color = QtGui.QColor("#d8e1ee" if dark_theme else "#1b2431")
            outline_color = QtGui.QColor("#ffffff" if dark_theme else "#0b0f16")
            painter.fillRect(self.rect(), background)
            pen = QtGui.QPen(axis_color)
            painter.setPen(pen)
            self._draw_plane_grid(painter)
            painter.setPen(pen)
            self._draw_axes(painter)

            hint = _spatial_hint_text(self._nodes)
            if hint:
                painter.setPen(QtGui.QPen(text_color))
                painter.drawText(QtCore.QRectF(20.0, 16.0, self.width() - 40.0, 40.0), hint)
            painter.setPen(QtGui.QPen(text_color))
            painter.drawText(
                QtCore.QRectF(20.0, 48.0, self.width() - 40.0, 20.0),
                f"View: {self._view_mode.upper()} | Drag axis: {self._active_axis.upper()}",
            )
            render_strip_segments = self._renders_strip_segments()
            if self._individual_node_editing:
                self._draw_selected_guides(painter)
            if render_strip_segments:
                self._draw_strip_links(painter, dark_theme=dark_theme)

            selected = self._selected_node()
            selected_chain_key = selected.chain_key if selected is not None else ""
            if not render_strip_segments:
                self._draw_strip_boxes(painter, dark_theme=dark_theme)
            for node in self._nodes:
                if node.is_section and not render_strip_segments:
                    continue
                px, py = _project_node_for_view(node, view_mode=self._view_mode, config=cfg)
                painter.setBrush(QtGui.QColor(node.color))
                chain_selected = bool(selected_chain_key and node.chain_key == selected_chain_key)
                painter.setPen(
                    QtGui.QPen(
                        outline_color
                        if node.selected
                        else QtGui.QColor("#ffd166")
                        if chain_selected
                        else QtGui.QColor("#0b0f16" if dark_theme else "#4f5d73")
                    )
                )
                radius = (
                    12.0
                    if node.selected
                    else 6.0
                    if node.is_section and not self._individual_node_editing
                    else 9.0
                )
                painter.drawEllipse(QtCore.QPointF(px, py), radius, radius)
                if node.is_section:
                    node_label = (
                        str((node.section_index or 0) + 1)
                        if (
                            node.section_index is not None
                            and self._individual_node_editing
                        )
                        else ""
                    )
                else:
                    node_label = node.label
                if node_label:
                    painter.setPen(QtGui.QPen(text_color))
                    painter.drawText(
                        QtCore.QPointF(px + 12.0, py + 4.0),
                        node_label,
                    )
            if render_strip_segments and not self._individual_node_editing:
                self._draw_strip_segment_labels(
                    painter,
                    dark_theme=dark_theme,
                )

    return SpatialCanvas()
