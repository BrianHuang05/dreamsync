"""In-app help content and dialog for the DreamSync desktop application."""

from __future__ import annotations

from dreamsync.gui.qt import QtModules


_SECTIONS: tuple[tuple[str, str], ...] = (
    (
        "Start here",
        """
        <h1>DreamSync how-to guide</h1>
        <p>DreamSync turns music into room-aware lighting for supported LAN devices. A reliable first run is: create a device configuration, discover or add devices, arrange them in <b>Room Layout</b>, create a palette, then use either <b>Shows</b> for prepared tracks or <b>Live</b> for real-time audio.</p>
        <h2>Before you begin</h2>
        <ul><li>Put the computer and lights on the same LAN and enable LAN control on each device.</li><li>Use <b>Devices</b> to scan, test, and add devices to the configuration.</li><li>Use simulation/preview first when changing a room layout or show.</li></ul>
        <p>Save Configuration after changing paths, devices, layout, or live settings. The status bar shows the active device configuration and palette profile.</p>
        """,
    ),
    (
        "VB-Cable loopback",
        """
        <h1>Windows system-audio loopback with VB-Cable</h1>
        <p>Use this for <b>Audio Loopback Capture</b> and any workflow that needs DreamSync to receive your computer's system audio. VB-Cable's playback endpoint is named <b>CABLE Input</b>; its recording endpoint is named <b>CABLE Output</b>. Audio sent to Input is forwarded to Output.</p>
        <h2>Download and install</h2>
        <ol><li>Open the official <a href="https://vb-audio.com/Cable/">VB-Audio VB-CABLE download page</a>.</li><li>Download the current Windows driver package, extract it, then run the included setup program <b>as Administrator</b>.</li><li>Restart Windows after installation. If Windows does not show both CABLE devices, restart again before troubleshooting.</li></ol>
        <h2>Route Windows audio into DreamSync</h2>
        <ol><li>Open <b>Settings &gt; System &gt; Sound</b>.</li><li>Under <b>Output</b>, choose <b>CABLE Input (VB-Audio Virtual Cable)</b>. Apps using the default output will now feed the cable.</li><li>In DreamSync <b>Config</b>, select or enter the capture device pattern that matches <b>CABLE Output (VB-Audio Virtual Cable)</b>.</li><li>Start <b>Audio Loopback Capture</b> from the Live/Config capture controls and confirm its status changes to running.</li></ol>
        <p><b>Important:</b> select <i>CABLE Input</i> in Windows as an output, but select <i>CABLE Output</i> as DreamSync's capture source. Selecting CABLE Input for playback inside DreamSync creates a feedback route.</p>
        <h2>How you hear the music</h2>
        <p>For capture-to-show playback, DreamSync can capture CABLE Output and play processed audio through the normal speakers/headphones selected as its playback device—no Windows “Listen to this device” setting is needed. For real-time listening where no playback path is selected, open <b>Control Panel &gt; Sound &gt; Recording &gt; CABLE Output &gt; Properties &gt; Listen</b>, enable <b>Listen to this device</b>, and choose your real speakers. This adds latency; turn it off when not needed.</p>
        <h2>Verify and recover</h2>
        <p>If no audio arrives, first play audio and watch the app's input/diagnostic levels. Confirm Windows is outputting to CABLE Input, then confirm CABLE Output is visible as a recording device. If the driver disappears, uninstall/reinstall VB-Cable from Device Manager, reboot, reinstall the current official package as Administrator, and reboot again.</p>
        """,
    ),
    (
        "Config and devices",
        """
        <h1>Configuration and devices</h1>
        <h2>Create or open a configuration</h2>
        <p>In <b>Config</b>, choose the device/room configuration path, then save it. The configuration is the shared source of truth for device addresses, segment data, and layout. Start from the project's device template when creating a new file, or use <b>Devices</b> to build entries from discovery.</p>
        <h2>Edit devices</h2>
        <ol><li>Open <b>Devices</b> and scan the LAN.</li><li>Select a discovered light, test it if needed, then use <b>Add to Config</b> (or <b>Update Config Entry</b>).</li><li>Give the entry a stable name and ensure its IP address, device type, and segment settings match the device.</li><li>Save the configuration, then reload it when you need to discard unsaved layout changes.</li></ol>
        <p>If a device is marked unavailable, check power, LAN control, IP address, firewall/network isolation, and that the device is on the same subnet. Simulation lets you continue editing without sending to hardware.</p>
        """,
    ),
    (
        "Room layout",
        """
        <h1>Room Layout</h1>
        <p>The room canvas maps each device or strip section into X (left/right), Y (height), and Z (front/back depth). This lets effects travel across physical space rather than only along device order.</p>
        <ol><li>Select a strip or node from the list or canvas.</li><li>Choose <b>Move Strip</b> to reposition a whole device, <b>Orient Strip</b> to lay it on a cardinal line, or <b>Individual Nodes</b> for per-section placement.</li><li>Switch between Room, XY, XZ, and YZ views to edit the intended axis.</li><li>Use the coordinate fields or drag handles; save with <b>Save Layout</b>.</li></ol>
        <p>Use Groups to name areas such as “stage,” “left,” or “ceiling.” Groups can be enabled by default and then targeted by show or live routing. The validation message identifies incomplete strip chains or invalid placements before they affect output.</p>
        """,
    ),
    (
        "Palettes",
        """
        <h1>Palettes and profiles</h1>
        <p>In <b>Palettes</b>, load an existing profile or create one with <b>New Palette</b>. Add colors, reorder or remove them, and save the palette/profile. Seed colors can generate related palettes or a Quickshow profile.</p>
        <p>A profile can also define moods, effects, EQ routes, and instrument routes. Profile-level routes apply broadly; mood-level routes refine behavior when that mood is active. Keep palettes readable in the preview: use a small, intentional color set before adding complex routing.</p>
        <p>In Live, <b>Cycle Palette</b> changes the active choice from the current profile. For a prepared show, use <b>Recompile Show</b> to rebuild cue data after a structural change, or <b>Recolor</b> to apply a palette-only update when available.</p>
        """,
    ),
    (
        "Shows and preview",
        """
        <h1>Compile, preview, and play a show</h1>
        <ol><li>Open <b>Shows</b>, create a new show, and add local audio tracks.</li><li>Choose palettes and edit cues, transitions, routing, and optional per-cue palette overrides in the show editor.</li><li>Select a track and choose <b>Compile</b>, or use <b>Compile All Tracks</b>. Compilation analyzes the music and prepares lighting cues.</li><li>Use the simulation/preview canvas to inspect the result before hardware playback.</li><li>Save the show. Later use <b>Open Saved Show</b> to continue editing or playback.</li><li>Use Play Selected Cue to audition from a point, Pause to hold playback, and Stop/Escape to end it.</li></ol>
        <p>A preview may be simulation-only when no device configuration is loaded; that is expected and does not mean compilation failed. Recompile after changing timing, routing, effects, or layout. A palette-only recolor is faster but does not recalculate the show structure.</p>
        """,
    ),
    (
        "Live modes",
        """
        <h1>Live modes</h1>
        <h2>Queue / prepared playback</h2><p>Queue local audio, pair tracks with palettes, and start/pause/stop playback. DreamSync can compile queued items as they are needed.</p>
        <h2>Reactive</h2><p>Choose Reactive to configure the interface, then press Start (or Space) to begin listening. Select the live input device in Config, choose a profile, palette behavior, effect bank, tempo, and optional routing. Stop with Stop, Escape, or Space. If it starts without callbacks, revisit the selected audio input and Windows permissions/routing.</p>
        <h2>Raw Visualizer</h2><p>Use this direct frequency-driven mode when you want less musical interpretation. Configure the gradient origins, frequency colors, noise threshold, and optional automatic palette rotation, then start it from Live.</p>
        <h2>Spotify Live — Learning</h2><p>Connect Spotify in Config, enable the Spotify queue loopback option, then choose the Learning live mode. It captures and learns from the playing queue while retaining the selected profile, palette, effect, and diagnostic settings. The Spotify queue toggle is separate from the Windows system-audio capture device.</p>
        """,
    ),
    (
        "Options and diagnostics",
        """
        <h1>Config options and diagnostics</h1>
        <p><b>Config</b> stores application paths (profiles, shows, queue, capture and analysis folders), output mode, fallback-to-simulation behavior, audio input/output selection, capture folders/device pattern, theme, startup live mode, reactive settings, Spotify learning, and baked-playback preference. Save after edits; invalid fields are reported before the app writes them.</p>
        <h2>Reading Diagnostics</h2>
        <ul><li><b>Input/audio levels and callbacks:</b> should move while music is playing. Flat or missing values usually mean the wrong input device, mute, Windows privacy access, or VB-Cable routing is wrong.</li><li><b>Beat/BPM/confidence:</b> estimates become steadier after a short listening period. Low confidence is normal for silence, speech, quiet passages, or unclear percussion; it is not a device failure.</li><li><b>Palette/effect/routing:</b> confirms the requested and applied values. A mismatch usually indicates a profile override, cue override, unavailable palette, or a safety fallback.</li><li><b>Device status:</b> “preview/simulation” means no hardware is being sent to. Offline/unavailable devices point to LAN control, addressing, power, or reachability.</li><li><b>Playback mode:</b> baked means prepared frames were used; live means the show is being rendered at runtime. Missing or stale baked artifacts normally fall back to live rendering.</li><li><b>Warnings/errors:</b> read the first explicit cause, then use the active config/profile path in the status bar to verify you edited the file actually in use.</li></ul>
        <p>For a clean support report, open <b>Help &gt; About DreamSync</b> and use <b>Copy Support Info</b>, then include the relevant Diagnostics messages without tokens or passwords.</p>
        """,
    ),
)


def show_help_guide(parent, qt_modules: QtModules) -> None:  # pragma: no cover - Qt only
    """Show the searchable in-app how-to guide."""

    QtCore = qt_modules.QtCore
    QtWidgets = qt_modules.QtWidgets
    dialog = QtWidgets.QDialog(parent)
    dialog.setObjectName("dreamSyncHelpGuideDialog")
    dialog.setWindowTitle("DreamSync How-to Guide")
    dialog.resize(900, 680)
    layout = QtWidgets.QVBoxLayout(dialog)

    search = QtWidgets.QLineEdit()
    search.setObjectName("dreamSyncHelpGuideSearch")
    search.setPlaceholderText("Search the guide…")
    layout.addWidget(search)

    splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
    sections = QtWidgets.QListWidget()
    sections.setObjectName("dreamSyncHelpGuideSections")
    browser = QtWidgets.QTextBrowser()
    browser.setObjectName("dreamSyncHelpGuideText")
    browser.setOpenExternalLinks(True)
    splitter.addWidget(sections)
    splitter.addWidget(browser)
    splitter.setStretchFactor(1, 1)
    layout.addWidget(splitter, 1)

    for title, _content in _SECTIONS:
        sections.addItem(title)

    def _display(row: int) -> None:
        if row >= 0:
            browser.setHtml(_SECTIONS[row][1])

    def _filter(query: str) -> None:
        terms = query.casefold().split()
        first_visible = -1
        for row, (title, content) in enumerate(_SECTIONS):
            item = sections.item(row)
            visible = all(term in (title + " " + content).casefold() for term in terms)
            item.setHidden(not visible)
            if visible and first_visible < 0:
                first_visible = row
        if first_visible >= 0 and sections.currentRow() != first_visible:
            sections.setCurrentRow(first_visible)

    sections.currentRowChanged.connect(_display)
    search.textChanged.connect(_filter)
    sections.setCurrentRow(0)

    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()
