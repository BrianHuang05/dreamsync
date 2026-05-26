"""Profile editor widget builder."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileEditorWidgets:
    widget: object
    profile_name_label: object
    load_profile_button: object
    palette_combo: object
    add_palette_button: object
    palette_strip_host: object
    palette_strip_layout: object
    seed_color_buttons: tuple[object, ...]
    seed_scheme_combo: object
    generate_seed_palette_button: object
    quickshow_name_edit: object
    quickshow_energy_spin: object
    generate_quickshow_button: object
    palette_color_buttons: tuple[object, ...]
    add_color_button: object
    remove_color_button: object
    save_palette_button: object
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


def _build_table(
    QtWidgets,
    headers: tuple[str, ...],
    object_name: str,
):
    table = QtWidgets.QTableWidget(0, len(headers))
    table.setObjectName(object_name)
    table.setHorizontalHeaderLabels(list(headers))
    table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setStretchLastSection(True)
    table.setAlternatingRowColors(True)
    table.setMinimumHeight(140)
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
    layout = QtWidgets.QVBoxLayout(group)
    toolbar = QtWidgets.QHBoxLayout()
    add_button = QtWidgets.QPushButton(add_label)
    remove_button = QtWidgets.QPushButton(remove_label)
    toolbar.addWidget(add_button)
    toolbar.addWidget(remove_button)
    toolbar.addStretch(1)
    layout.addLayout(toolbar)
    table = _build_table(QtWidgets, headers, object_name)
    layout.addWidget(table)
    return group, table, add_button, remove_button


def build_profile_editor_panel(qt_modules):
    QtWidgets = qt_modules.QtWidgets

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
    layout.addWidget(effects_group)

    params_group, params_table, add_param_button, remove_param_button = _build_table_group(
        QtWidgets,
        title="Mood Params",
        headers=("Parameter", "Value"),
        object_name="profileParamsTable",
        add_label="Add Param",
        remove_label="Remove Param",
    )
    layout.addWidget(params_group)

    profile_eq_group, profile_eq_routes_table, add_profile_eq_route_button, remove_profile_eq_route_button = _build_table_group(
        QtWidgets,
        title="Profile EQ Routes",
        headers=("Band", "When", "Render", "Color", "Intensity", "Spatial"),
        object_name="profileEqRoutesTable",
        add_label="Add Profile EQ Route",
        remove_label="Remove Selected",
    )
    layout.addWidget(profile_eq_group)

    mood_eq_group, mood_eq_routes_table, add_mood_eq_route_button, remove_mood_eq_route_button = _build_table_group(
        QtWidgets,
        title="Mood EQ Routes",
        headers=("Band", "When", "Render", "Color", "Intensity", "Spatial"),
        object_name="profileMoodEqRoutesTable",
        add_label="Add Mood EQ Route",
        remove_label="Remove Selected",
    )
    layout.addWidget(mood_eq_group)

    profile_instrument_group, profile_instrument_routes_table, add_profile_instrument_route_button, remove_profile_instrument_route_button = _build_table_group(
        QtWidgets,
        title="Profile Instrument Routes",
        headers=("Instrument", "When", "Render", "Color", "Intensity", "Spatial", "Pan", "Width", "Confidence"),
        object_name="profileInstrumentRoutesTable",
        add_label="Add Profile Instrument Route",
        remove_label="Remove Selected",
    )
    layout.addWidget(profile_instrument_group)

    mood_instrument_group, mood_instrument_routes_table, add_mood_instrument_route_button, remove_mood_instrument_route_button = _build_table_group(
        QtWidgets,
        title="Mood Instrument Routes",
        headers=("Instrument", "When", "Render", "Color", "Intensity", "Spatial", "Pan", "Width", "Confidence"),
        object_name="profileMoodInstrumentRoutesTable",
        add_label="Add Mood Instrument Route",
        remove_label="Remove Selected",
    )
    layout.addWidget(mood_instrument_group)

    transitions_group, transitions_table, add_transition_button, remove_transition_button = _build_table_group(
        QtWidgets,
        title="Transitions",
        headers=("From", "To", "Palette"),
        object_name="profileTransitionsTable",
        add_label="Add Transition",
        remove_label="Remove Selected",
    )
    layout.addWidget(transitions_group)

    action_row = QtWidgets.QHBoxLayout()
    save_sections_button = QtWidgets.QPushButton("Save Sections")
    save_sections_button.setObjectName("saveProfileSectionsButton")
    save_all_button = QtWidgets.QPushButton("Save All")
    save_all_button.setObjectName("saveProfileAllButton")
    action_row.addWidget(save_sections_button)
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
        palette_combo=palette_combo,
        add_palette_button=add_palette_button,
        palette_strip_host=palette_strip_host,
        palette_strip_layout=palette_strip_layout,
        seed_color_buttons=tuple(seed_color_buttons),
        seed_scheme_combo=seed_scheme_combo,
        generate_seed_palette_button=generate_seed_palette_button,
        quickshow_name_edit=quickshow_name_edit,
        quickshow_energy_spin=quickshow_energy_spin,
        generate_quickshow_button=generate_quickshow_button,
        palette_color_buttons=tuple(palette_color_buttons),
        add_color_button=add_color_button,
        remove_color_button=remove_color_button,
        save_palette_button=save_palette_button,
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
        save_sections_button=save_sections_button,
        save_all_button=save_all_button,
        status_label=status_label,
    )
