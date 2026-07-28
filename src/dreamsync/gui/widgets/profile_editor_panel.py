"""Profile editor widget builder."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileEditorWidgets:
    widget: object
    profile_name_label: object
    load_profile_button: object
    library_search_edit: object
    library_tags_edit: object
    refresh_library_button: object
    profile_library_list: object
    load_library_profile_button: object
    generation_seed_check: object
    generation_seed_spin: object
    generation_pool_size_spin: object
    preview_generated_profiles_button: object
    generated_profile_combo: object
    export_generated_profile_button: object
    preview_transition_button: object
    validation_label: object
    palette_combo: object
    add_palette_button: object
    palette_strip_host: object
    palette_strip_layout: object
    seed_color_buttons: tuple[object, ...]
    seed_scheme_combo: object
    seed_randomness_check: object
    generate_seed_palette_button: object
    quickshow_name_edit: object
    quickshow_energy_spin: object
    generate_quickshow_button: object
    palette_color_buttons: tuple[object, ...]
    add_color_button: object
    remove_color_button: object
    save_palette_button: object
    show_palette_set_combo: object
    add_show_palette_set_button: object
    show_palette_set_members_edit: object
    save_show_palette_set_button: object
    mood_combo: object
    effects_table: object
    add_effect_button: object
    remove_effect_button: object
    params_table: object
    add_param_button: object
    remove_param_button: object
    profile_eq_routes_table: object
    add_profile_eq_route_button: object
    remove_profile_eq_route_button: object
    mood_eq_routes_table: object
    add_mood_eq_route_button: object
    remove_mood_eq_route_button: object
    profile_instrument_routes_table: object
    add_profile_instrument_route_button: object
    remove_profile_instrument_route_button: object
    mood_instrument_routes_table: object
    add_mood_instrument_route_button: object
    remove_mood_instrument_route_button: object
    transitions_table: object
    add_transition_button: object
    remove_transition_button: object
    save_sections_button: object
    save_all_button: object
    status_label: object
    effects_group: object
    params_group: object
    profile_eq_group: object
    mood_eq_group: object
    profile_instrument_group: object
    mood_instrument_group: object
    transitions_group: object


def _build_table(
    QtWidgets,
    headers: tuple[str, ...],
    object_name: str,
):
    class RowActionTable(QtWidgets.QTableWidget):
        def _fit_to_contents(self):
            self.resizeColumnsToContents()
            for column, label in enumerate(headers):
                header_width = self.horizontalHeader().sectionSizeHint(column)
                self.setColumnWidth(column, max(104, header_width, self.columnWidth(column)))
            total_width = sum(self.columnWidth(column) for column in range(self.columnCount()))
            self.setFixedWidth(total_width + (self.frameWidth() * 2) + 2)

        def _fit_to_rows(self):
            header_height = self.horizontalHeader().sizeHint().height()
            row_height = self.verticalHeader().defaultSectionSize()
            self.setFixedHeight(header_height + max(1, self.rowCount()) * row_height + 4)

        def insertRow(self, row):  # pragma: no cover - Qt only
            super().insertRow(row)
            action = QtWidgets.QToolButton(self)
            action.setText("−")
            action.setToolTip("Remove this row")
            action.setAutoRaise(True)
            action.setFixedSize(24, 24)
            action.clicked.connect(lambda: self.removeRow(self.indexAt(action.pos()).row()))
            self.setCellWidget(row, len(headers), action)
            self._fit_to_contents()
            self._fit_to_rows()

        def setCellWidget(self, row, column, cell_widget):  # pragma: no cover - Qt only
            super().setCellWidget(row, column, cell_widget)
            self._fit_to_contents()

        def removeRow(self, row):  # pragma: no cover - Qt only
            super().removeRow(row)
            self._fit_to_rows()

    table = RowActionTable(0, len(headers) + 1)
    table.setObjectName(object_name)
    table.setHorizontalHeaderLabels([*headers, ""])
    table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(28)
    table.verticalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Fixed)
    table.horizontalHeader().setStretchLastSection(False)
    table.horizontalHeader().setSectionResizeMode(
        len(headers), QtWidgets.QHeaderView.ResizeMode.Fixed
    )
    table.setColumnWidth(len(headers), 30)
    table.setAlternatingRowColors(True)
    table.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Fixed,
        QtWidgets.QSizePolicy.Policy.Fixed,
    )
    table._fit_to_contents()
    table._fit_to_rows()
    return table


def _build_table_group(
    QtWidgets,
    *,
    title: str,
    headers: tuple[str, ...],
    object_name: str,
    add_label: str,
    remove_label: str,
):
    group = QtWidgets.QGroupBox(title)
    group.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Maximum,
        QtWidgets.QSizePolicy.Policy.Maximum,
    )
    layout = QtWidgets.QVBoxLayout(group)
    add_button = QtWidgets.QPushButton(add_label)
    add_button.setText("+")
    add_button.setToolTip(add_label)
    add_button.setFixedWidth(32)
    remove_button = None
    table = _build_table(QtWidgets, headers, object_name)
    layout.addWidget(table)
    toolbar = QtWidgets.QHBoxLayout()
    toolbar.addWidget(add_button)
    toolbar.addStretch(1)
    layout.addLayout(toolbar)
    return group, table, add_button, remove_button


def build_profile_editor_panel(qt_modules):
    QtWidgets = qt_modules.QtWidgets
    QtCore = qt_modules.QtCore

    root = QtWidgets.QWidget()
    root_layout = QtWidgets.QVBoxLayout(root)

    scroll = QtWidgets.QScrollArea()
    scroll.setWidgetResizable(True)
    root_layout.addWidget(scroll)

    widget = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(widget)
    scroll.setWidget(widget)

    profile_name_label = QtWidgets.QLabel("Profile: none")
    profile_name_label.setWordWrap(True)
    profile_name_label.setObjectName("profileNameLabel")
    layout.addWidget(profile_name_label)

    profile_actions = QtWidgets.QHBoxLayout()
    load_profile_button = QtWidgets.QPushButton("Load Profile")
    load_profile_button.setObjectName("loadProfileEditorButton")
    profile_actions.addWidget(load_profile_button)
    profile_actions.addStretch(1)
    layout.addLayout(profile_actions)

    library_group = QtWidgets.QGroupBox("Profile Library")
    library_layout = QtWidgets.QVBoxLayout(library_group)
    library_filters = QtWidgets.QHBoxLayout()
    library_search_edit = QtWidgets.QLineEdit()
    library_search_edit.setObjectName("profileLibrarySearchEdit")
    library_search_edit.setPlaceholderText("Search profile name or tag")
    library_tags_edit = QtWidgets.QLineEdit()
    library_tags_edit.setObjectName("profileLibraryTagsEdit")
    library_tags_edit.setPlaceholderText("Required tags (comma-separated)")
    refresh_library_button = QtWidgets.QPushButton("Refresh")
    refresh_library_button.setObjectName("refreshProfileLibraryButton")
    library_filters.addWidget(library_search_edit, 2)
    library_filters.addWidget(library_tags_edit, 1)
    library_filters.addWidget(refresh_library_button)
    library_layout.addLayout(library_filters)
    profile_library_list = QtWidgets.QListWidget()
    profile_library_list.setObjectName("profileLibraryList")
    profile_library_list.setMaximumHeight(150)
    library_layout.addWidget(profile_library_list)
    load_library_profile_button = QtWidgets.QPushButton("Load Selected Profile")
    load_library_profile_button.setObjectName("loadLibraryProfileButton")
    library_layout.addWidget(load_library_profile_button)
    generation_row = QtWidgets.QHBoxLayout()
    generation_seed_check = QtWidgets.QCheckBox("Seed")
    generation_seed_check.setObjectName("profileGenerationSeedCheck")
    generation_seed_spin = QtWidgets.QSpinBox()
    generation_seed_spin.setRange(-2147483647, 2147483647)
    generation_seed_spin.setObjectName("profileGenerationSeedSpin")
    generation_seed_spin.setEnabled(False)
    generation_seed_check.toggled.connect(generation_seed_spin.setEnabled)
    generation_pool_size_spin = QtWidgets.QSpinBox()
    generation_pool_size_spin.setRange(2, 32)
    generation_pool_size_spin.setValue(8)
    generation_pool_size_spin.setPrefix("Pool ")
    generation_pool_size_spin.setObjectName("profileGenerationPoolSizeSpin")
    preview_generated_profiles_button = QtWidgets.QPushButton("Preview Generated Pool")
    preview_generated_profiles_button.setObjectName("previewGeneratedProfilesButton")
    generated_profile_combo = QtWidgets.QComboBox()
    generated_profile_combo.setObjectName("generatedProfileCombo")
    export_generated_profile_button = QtWidgets.QPushButton("Save As Profile…")
    export_generated_profile_button.setObjectName("exportGeneratedProfileButton")
    preview_transition_button = QtWidgets.QPushButton("Preview Cross-fade")
    preview_transition_button.setObjectName("previewProfileTransitionButton")
    for control in (
        generation_seed_check,
        generation_seed_spin,
        generation_pool_size_spin,
        preview_generated_profiles_button,
        generated_profile_combo,
        export_generated_profile_button,
        preview_transition_button,
    ):
        generation_row.addWidget(control)
    library_layout.addLayout(generation_row)
    validation_label = QtWidgets.QLabel("Validation: no profile loaded.")
    validation_label.setObjectName("profileValidationLabel")
    validation_label.setWordWrap(True)
    library_layout.addWidget(validation_label)
    layout.addWidget(library_group)

    palette_group = QtWidgets.QGroupBox("Palette")
    palette_layout = QtWidgets.QGridLayout(palette_group)
    palette_layout.addWidget(QtWidgets.QLabel("Palette"), 0, 0)
    palette_combo = QtWidgets.QComboBox()
    palette_combo.setObjectName("profilePaletteCombo")
    palette_layout.addWidget(palette_combo, 0, 1)
    add_palette_button = QtWidgets.QPushButton("New Palette")
    add_palette_button.setObjectName("addProfilePaletteButton")
    palette_layout.addWidget(add_palette_button, 0, 2)

    palette_layout.addWidget(QtWidgets.QLabel("Current colors"), 1, 0, 1, 3)
    palette_strip_host = QtWidgets.QWidget()
    palette_strip_host.setObjectName("profilePaletteStripHost")
    palette_strip_layout = QtWidgets.QHBoxLayout(palette_strip_host)
    palette_strip_layout.setContentsMargins(0, 0, 0, 0)
    palette_layout.addWidget(palette_strip_host, 2, 0, 1, 3)

    palette_layout.addWidget(QtWidgets.QLabel("Generate from seed colors"), 3, 0, 1, 3)
    seed_row = QtWidgets.QHBoxLayout()
    seed_color_buttons = []
    for index, label in enumerate(("Seed 1", "Seed 2")):
        button = QtWidgets.QPushButton(label)
        button.setObjectName(f"profileSeedColorButton{index + 1}")
        button.setMinimumHeight(34)
        seed_row.addWidget(button)
        seed_color_buttons.append(button)
    seed_scheme_combo = QtWidgets.QComboBox()
    seed_scheme_combo.setObjectName("profileSeedSchemeCombo")
    for label, data in (
        ("Gradient", "gradient"),
        ("Complementary", "complementary"),
        ("Analogous", "analogous"),
        ("Triadic", "triadic"),
    ):
        seed_scheme_combo.addItem(label, data)
    seed_row.addWidget(seed_scheme_combo)
    seed_randomness_check = QtWidgets.QCheckBox("Randomness")
    seed_randomness_check.setToolTip("Salt the seed colors so Generate produces a new palette each time.")
    seed_randomness_check.setObjectName("profileSeedRandomnessCheck")
    seed_row.addWidget(seed_randomness_check)
    generate_seed_palette_button = QtWidgets.QPushButton("Generate From Seeds")
    generate_seed_palette_button.setObjectName("generateSeedPaletteButton")
    seed_row.addWidget(generate_seed_palette_button)
    palette_layout.addLayout(seed_row, 4, 0, 1, 3)

    quickshow_row = QtWidgets.QHBoxLayout()
    quickshow_name_edit = QtWidgets.QLineEdit()
    quickshow_name_edit.setObjectName("quickshowNameEdit")
    quickshow_name_edit.setPlaceholderText("Quickshow name (optional)")
    quickshow_row.addWidget(quickshow_name_edit, 2)
    quickshow_energy_spin = QtWidgets.QDoubleSpinBox()
    quickshow_energy_spin.setObjectName("quickshowEnergySpin")
    quickshow_energy_spin.setRange(0.3, 2.0)
    quickshow_energy_spin.setSingleStep(0.05)
    quickshow_energy_spin.setValue(1.0)
    quickshow_energy_spin.setPrefix("Energy ")
    quickshow_row.addWidget(quickshow_energy_spin)
    generate_quickshow_button = QtWidgets.QPushButton("Generate Quickshow")
    generate_quickshow_button.setObjectName("generateQuickshowButton")
    quickshow_row.addWidget(generate_quickshow_button)
    palette_layout.addLayout(quickshow_row, 5, 0, 1, 3)

    palette_layout.addWidget(QtWidgets.QLabel("Pick colors"), 6, 0, 1, 3)
    color_grid = QtWidgets.QGridLayout()
    palette_color_buttons = []
    for index in range(8):
        button = QtWidgets.QPushButton(f"Color {index + 1}")
        button.setObjectName(f"profilePaletteColorButton{index + 1}")
        button.setMinimumHeight(34)
        button.setVisible(False)
        color_grid.addWidget(button, index // 4, index % 4)
        palette_color_buttons.append(button)
    palette_layout.addLayout(color_grid, 7, 0, 1, 3)

    palette_actions = QtWidgets.QHBoxLayout()
    add_color_button = QtWidgets.QPushButton("Add Color")
    add_color_button.setObjectName("addProfilePaletteColorButton")
    remove_color_button = QtWidgets.QPushButton("Remove Color")
    remove_color_button.setObjectName("removeProfilePaletteColorButton")
    save_palette_button = QtWidgets.QPushButton("Save Palette")
    save_palette_button.setObjectName("saveProfilePaletteButton")
    palette_actions.addWidget(add_color_button)
    palette_actions.addWidget(remove_color_button)
    palette_actions.addWidget(save_palette_button)
    palette_actions.addStretch(1)
    palette_layout.addLayout(palette_actions, 8, 0, 1, 3)
    layout.addWidget(palette_group)

    show_palette_set_group = QtWidgets.QGroupBox("Show Palette Sets")
    show_palette_set_layout = QtWidgets.QGridLayout(show_palette_set_group)
    show_palette_set_layout.addWidget(QtWidgets.QLabel("Set"), 0, 0)
    show_palette_set_combo = QtWidgets.QComboBox()
    show_palette_set_combo.setObjectName("showPaletteSetCombo")
    show_palette_set_layout.addWidget(show_palette_set_combo, 0, 1)
    add_show_palette_set_button = QtWidgets.QPushButton("New Set")
    add_show_palette_set_button.setObjectName("addShowPaletteSetButton")
    show_palette_set_layout.addWidget(add_show_palette_set_button, 0, 2)
    show_palette_set_layout.addWidget(QtWidgets.QLabel("Palettes"), 1, 0)
    show_palette_set_members_edit = QtWidgets.QLineEdit()
    show_palette_set_members_edit.setObjectName("showPaletteSetMembersEdit")
    show_palette_set_members_edit.setPlaceholderText("winter-white, evergreen, gold")
    show_palette_set_members_edit.setToolTip(
        "Comma-separated profile palette names. Reactive mode uses one palette per detected song."
    )
    show_palette_set_layout.addWidget(show_palette_set_members_edit, 1, 1, 1, 2)
    save_show_palette_set_button = QtWidgets.QPushButton("Save Set")
    save_show_palette_set_button.setObjectName("saveShowPaletteSetButton")
    show_palette_set_layout.addWidget(save_show_palette_set_button, 2, 1, 1, 2)
    layout.addWidget(show_palette_set_group)

    mood_row = QtWidgets.QHBoxLayout()
    mood_row.addWidget(QtWidgets.QLabel("Mood"))
    mood_combo = QtWidgets.QComboBox()
    mood_combo.setObjectName("profileMoodCombo")
    mood_row.addWidget(mood_combo, 1)
    layout.addLayout(mood_row)

    effects_group, effects_table, add_effect_button, remove_effect_button = _build_table_group(
        QtWidgets,
        title="Mood Effects",
        headers=("Effect", "Weight"),
        object_name="profileEffectsTable",
        add_label="Add Effect",
        remove_label="Remove Effect",
    )

    params_group, params_table, add_param_button, remove_param_button = _build_table_group(
        QtWidgets,
        title="Mood Params",
        headers=("Parameter", "Value"),
        object_name="profileParamsTable",
        add_label="Add Param",
        remove_label="Remove Param",
    )

    profile_eq_group, profile_eq_routes_table, add_profile_eq_route_button, remove_profile_eq_route_button = _build_table_group(
        QtWidgets,
        title="Profile EQ Routes",
        headers=("Band", "When", "Render", "Color", "Intensity", "Spatial"),
        object_name="profileEqRoutesTable",
        add_label="Add Profile EQ Route",
        remove_label="Remove Selected",
    )

    mood_eq_group, mood_eq_routes_table, add_mood_eq_route_button, remove_mood_eq_route_button = _build_table_group(
        QtWidgets,
        title="Mood EQ Routes",
        headers=("Band", "When", "Render", "Color", "Intensity", "Spatial"),
        object_name="profileMoodEqRoutesTable",
        add_label="Add Mood EQ Route",
        remove_label="Remove Selected",
    )

    profile_instrument_group, profile_instrument_routes_table, add_profile_instrument_route_button, remove_profile_instrument_route_button = _build_table_group(
        QtWidgets,
        title="Profile Instrument Routes",
        headers=("Instrument", "When", "Render", "Color", "Intensity", "Spatial", "Pan", "Width", "Confidence"),
        object_name="profileInstrumentRoutesTable",
        add_label="Add Profile Instrument Route",
        remove_label="Remove Selected",
    )

    mood_instrument_group, mood_instrument_routes_table, add_mood_instrument_route_button, remove_mood_instrument_route_button = _build_table_group(
        QtWidgets,
        title="Mood Instrument Routes",
        headers=("Instrument", "When", "Render", "Color", "Intensity", "Spatial", "Pan", "Width", "Confidence"),
        object_name="profileMoodInstrumentRoutesTable",
        add_label="Add Mood Instrument Route",
        remove_label="Remove Selected",
    )

    transitions_group, transitions_table, add_transition_button, remove_transition_button = _build_table_group(
        QtWidgets,
        title="Transitions",
        headers=("From", "To", "Palette"),
        object_name="profileTransitionsTable",
        add_label="Add Transition",
        remove_label="Remove Selected",
    )
    sections_grid = QtWidgets.QGridLayout()
    sections_grid.setAlignment(
        QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop
    )
    sections_grid.setHorizontalSpacing(12)
    section_groups = (
        effects_group,
        params_group,
        transitions_group,
    )
    expanded = {group: False for group in section_groups}

    def relayout_sections():
        while sections_grid.count():
            item = sections_grid.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(widget)
        expanded_groups = [group for group in section_groups if expanded[group]]
        if expanded_groups:
            for row, group in enumerate(expanded_groups):
                group.setSizePolicy(
                    QtWidgets.QSizePolicy.Policy.Expanding,
                    QtWidgets.QSizePolicy.Policy.Maximum,
                )
                sections_grid.addWidget(group, row, 0, 1, 3)
            row = len(expanded_groups)
        else:
            row = 0
        for index, group in enumerate(group for group in section_groups if not expanded[group]):
            group.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Maximum,
                QtWidgets.QSizePolicy.Policy.Maximum,
            )
            sections_grid.addWidget(
                group,
                row + index // 3,
                index % 3,
                QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop,
            )

    def make_expandable(group):
        title = group.title()
        group.setTitle("")
        header = QtWidgets.QToolButton()
        header.setText(f"▸ {title}")
        header.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        header.setCheckable(True)
        header.setChecked(False)
        header.setStyleSheet("QToolButton { font-weight: 600; text-align: left; }")
        group.layout().insertWidget(0, header)
        def toggle(checked):
            expanded[group] = bool(checked)
            header.setText(f"{'▾' if checked else '▸'} {title}")
            relayout_sections()
        header.toggled.connect(toggle)

    # Routing is shown in the Show editor, but remains part of the profile model.
    for group in section_groups:
        make_expandable(group)
    layout.addLayout(sections_grid)
    relayout_sections()

    action_row = QtWidgets.QHBoxLayout()
    save_all_button = QtWidgets.QPushButton("Save All")
    save_all_button.setObjectName("saveProfileAllButton")
    action_row.addWidget(save_all_button)
    action_row.addStretch(1)
    layout.addLayout(action_row)

    status_label = QtWidgets.QLabel("")
    status_label.setWordWrap(True)
    status_label.setObjectName("profileEditorStatusLabel")
    layout.addWidget(status_label)
    layout.addStretch(1)

    return ProfileEditorWidgets(
        widget=root,
        profile_name_label=profile_name_label,
        load_profile_button=load_profile_button,
        library_search_edit=library_search_edit,
        library_tags_edit=library_tags_edit,
        refresh_library_button=refresh_library_button,
        profile_library_list=profile_library_list,
        load_library_profile_button=load_library_profile_button,
        generation_seed_check=generation_seed_check,
        generation_seed_spin=generation_seed_spin,
        generation_pool_size_spin=generation_pool_size_spin,
        preview_generated_profiles_button=preview_generated_profiles_button,
        generated_profile_combo=generated_profile_combo,
        export_generated_profile_button=export_generated_profile_button,
        preview_transition_button=preview_transition_button,
        validation_label=validation_label,
        palette_combo=palette_combo,
        add_palette_button=add_palette_button,
        palette_strip_host=palette_strip_host,
        palette_strip_layout=palette_strip_layout,
        seed_color_buttons=tuple(seed_color_buttons),
        seed_scheme_combo=seed_scheme_combo,
        seed_randomness_check=seed_randomness_check,
        generate_seed_palette_button=generate_seed_palette_button,
        quickshow_name_edit=quickshow_name_edit,
        quickshow_energy_spin=quickshow_energy_spin,
        generate_quickshow_button=generate_quickshow_button,
        palette_color_buttons=tuple(palette_color_buttons),
        add_color_button=add_color_button,
        remove_color_button=remove_color_button,
        save_palette_button=save_palette_button,
        show_palette_set_combo=show_palette_set_combo,
        add_show_palette_set_button=add_show_palette_set_button,
        show_palette_set_members_edit=show_palette_set_members_edit,
        save_show_palette_set_button=save_show_palette_set_button,
        mood_combo=mood_combo,
        effects_table=effects_table,
        add_effect_button=add_effect_button,
        remove_effect_button=remove_effect_button,
        params_table=params_table,
        add_param_button=add_param_button,
        remove_param_button=remove_param_button,
        profile_eq_routes_table=profile_eq_routes_table,
        add_profile_eq_route_button=add_profile_eq_route_button,
        remove_profile_eq_route_button=remove_profile_eq_route_button,
        mood_eq_routes_table=mood_eq_routes_table,
        add_mood_eq_route_button=add_mood_eq_route_button,
        remove_mood_eq_route_button=remove_mood_eq_route_button,
        profile_instrument_routes_table=profile_instrument_routes_table,
        add_profile_instrument_route_button=add_profile_instrument_route_button,
        remove_profile_instrument_route_button=remove_profile_instrument_route_button,
        mood_instrument_routes_table=mood_instrument_routes_table,
        add_mood_instrument_route_button=add_mood_instrument_route_button,
        remove_mood_instrument_route_button=remove_mood_instrument_route_button,
        transitions_table=transitions_table,
        add_transition_button=add_transition_button,
        remove_transition_button=remove_transition_button,
        save_sections_button=None,
        save_all_button=save_all_button,
        status_label=status_label,
        effects_group=effects_group,
        params_group=params_group,
        profile_eq_group=profile_eq_group,
        mood_eq_group=mood_eq_group,
        profile_instrument_group=profile_instrument_group,
        mood_instrument_group=mood_instrument_group,
        transitions_group=transitions_group,
    )
