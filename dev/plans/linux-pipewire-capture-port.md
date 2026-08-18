# Linux PipeWire/Pulse Capture Port Plan

Status: proposed implementation plan; no production-code changes in this document

Target branch: `codex/linux-port`

## Objective

Port DreamSync's system-audio capture path from its current Windows-only
DirectShow/VB-Cable implementation to Linux PipeWire through its PulseAudio
compatibility server. Preserve the existing capture, analysis, compilation,
lighting, and playback contracts wherever they are platform-independent.

The target Linux route is:

```text
Audio application -> DreamSync_Capture virtual sink
                      -> dreamsync_capture.monitor -> DreamSync capture
                      -> PipeWire loopback -> physical speakers/headphones
```

DreamSync captures the monitor source. It must not replay the captured PCM to
speakers; PipeWire owns that loopback route.

## Confirmed host baseline

- Ubuntu 22.04 (Jammy) with PipeWire's PulseAudio server.
- ALSA exposes `HDA Intel PCH` / `ALC298 Analog` for both playback and capture.
- PipeWire device discovery required WirePlumber; `pipewire-media-session` only
  exposed the `auto_null` fallback sink.
- After switching to WirePlumber, a `module-null-sink` named
  `dreamsync_capture` and a `module-loopback` route successfully created
  `dreamsync_capture.monitor`.
- `ffmpeg -f pulse -i dreamsync_capture.monitor` can read the monitor source.
- The Python test suite runs reliably with `QT_QPA_PLATFORM=minimal`. The
  `offscreen` backend can abort in threaded GUI tests.

## Current Windows-only assumptions to remove

1. `capture/ffmpeg_device.py` uses `ffmpeg -f dshow -list_devices true` and
   parses DirectShow output.
2. `capture/capture_process.py` always builds `-f dshow -i audio=<name>`.
3. `CaptureConfig.device_pattern` defaults to `CABLE Output`, a Windows device
   name.
4. README and GUI help describe VB-Cable/DirectShow as the only route.
5. Capture tests assert Windows-specific device and command details.

`resolve_ffmpeg()` already returns a valid executable on Linux. Tests must
allow either a bare command or an absolute executable path.

## Design decisions

### Backend selection

Create a small capture-backend boundary selected from `sys.platform`:

| Platform | Discovery | FFmpeg input |
| --- | --- | --- |
| Windows | Existing DirectShow discovery | `-f dshow -i audio=<device>` |
| Linux | Pulse source discovery | `-f pulse -i <source>` |

Do not infer a Linux backend from a source name alone. Keep an explicit
backend value in the resolved capture-device record so diagnostics and tests
can explain what was selected.

### Linux source discovery

Use `pactl list short sources` as the primary source inventory. Parse its
tabular rows into a small model containing at least:

- source name, for example `dreamsync_capture.monitor`;
- source index;
- sample format, channel count, and sample rate;
- current state;
- whether the name ends in `.monitor`.

Discovery must use a subprocess with a bounded timeout and return a useful
error when `pactl` is unavailable, PipeWire/Pulse is not running, or no source
matches. It must not need `wpctl`; WirePlumber supplies it on modern hosts, but
the Pulse API is sufficient for the application backend.

Matching priority on Linux:

1. exact source name supplied by the user;
2. case-insensitive exact source name;
3. case-insensitive substring match to the configured pattern;
4. a single available monitor source only when an explicit auto-select policy
   permits it.

Ambiguous matches must fail with a list of candidates rather than silently
choosing a microphone or arbitrary monitor.

### Capture command

For the resolved Pulse source, construct:

```text
ffmpeg -hide_banner -loglevel warning -thread_queue_size <N>
       -f pulse -i <source>
       -f s16le -acodec pcm_s16le -ar <rate> -ac <channels> pipe:1
```

Keep the existing stdout PCM contract unchanged. The split processor, encoder,
metadata writer, analyzer, and compiler should remain unaware of the operating
system and capture backend.

The source's reported rate is normally 48 kHz on PipeWire. Do not hard-code
44.1 kHz; either use the selected effective rate consistently or make an
explicit downstream resampling decision with tests.

## Implementation phases

### Phase 1: Model and backend boundary

- Add a `CaptureBackend` enum or equivalent resolved-device model.
- Keep DirectShow implementation behavior unchanged on Windows.
- Add platform selection in one place, avoiding scattered `sys.platform`
  checks.
- Make error messages name the backend and source/device that failed.

### Phase 2: Pulse discovery

- Add a Pulse source lister/parser adjacent to `ffmpeg_device.py`, or rename
  that module to a backend-neutral capture-device module.
- Unit-test valid rows, malformed rows, command failures, timeout, no match,
  exact match, substring match, monitor preference, and ambiguity.
- Add a Linux default pattern such as `dreamsync_capture.monitor`; retain the
  Windows `CABLE Output` default only for the Windows backend.

### Phase 3: Backend-specific FFmpeg command construction

- Refactor `_build_command()` in `CaptureProcessManager` into a common output
  section plus DirectShow/Pulse input sections.
- Add Windows regression tests for the existing DirectShow command.
- Add Linux tests asserting `-f pulse`, the unmodified Pulse source name, and
  the same PCM stdout options.
- Preserve process group, stderr-drain, stop, and error behavior on both
  platforms.

### Phase 4: CLI, config, and diagnostics

- Add a backend-neutral capture-source option while retaining legacy
  `--device-pattern` compatibility.
- Make `dreamsync devices` report Pulse sources and mark monitor sources on
  Linux.
- Show the resolved backend, source, effective format, and matching rule in
  debug/diagnostic output.
- Update dummy/no-device flows so Linux developers can run analysis and
  simulation without a physical Govee device.

### Phase 5: GUI and documentation

- Replace Windows-only capture wording in GUI help with platform-specific
  instructions.
- For Linux, document WirePlumber, `dreamsync_capture.monitor`, and that the
  PipeWire virtual sink/loopback provides audible routing.
- Do not expose or implement a DreamSync "monitor captured audio to speakers"
  switch.
- Keep the existing Windows VB-Cable instructions intact under a Windows
  heading.

### Phase 6: Verification and release

- Run focused capture, CLI, and session tests on Linux with
  `QT_QPA_PLATFORM=minimal`.
- Run the full suite and classify any remaining GUI/audio-timing failures
  separately from capture backend failures.
- Perform the manual validation below before merging to `main`.

## Automated tests

Required new or adjusted coverage:

- FFmpeg executable assertions accept a resolved absolute path.
- DirectShow command behavior remains unchanged on Windows.
- Pulse command uses `-f pulse` and `-i dreamsync_capture.monitor`.
- Pulse source parser preserves names, state, rate, and channel count.
- Exact source selection wins over substring selection.
- Ambiguous source selection fails descriptively.
- A missing Pulse server or missing source produces a recoverable diagnostic.
- `CaptureProcessManager.start()` passes the resolved source to the selected
  backend and retains its current lifecycle semantics.
- CLI/device-list output is backend-aware.
- Linux GUI tests use `QT_QPA_PLATFORM=minimal` in CI or test configuration;
  preserve native-display testing separately where appropriate.

Current baseline notes:

- The capture-process executable-path assertion is Linux-sensitive because
  `resolve_ffmpeg()` returns `/usr/bin/ffmpeg` rather than `ffmpeg`.
- Some full-suite GUI interaction failures under `minimal` are backend-specific
  test behavior, not evidence that the production GUI cannot launch.
- Show-player callback/timing failures should be re-run after the capture work
  and categorized independently; they are not a reason to change the Pulse
  capture contract.

## Manual Linux acceptance test

1. Confirm `pactl list short sinks` shows a physical HDA sink and
   `pactl list short sources` shows `dreamsync_capture.monitor`.
2. Route a test application to `dreamsync_capture` and keep audible output on
   the physical sink through PipeWire's loopback.
3. Verify the source has non-silent samples:

   ```bash
   ffmpeg -hide_banner -f pulse -i dreamsync_capture.monitor \
     -t 5 -af volumedetect -f null -
   ```

4. Run the DreamSync capture command against the same source. Confirm PCM
   arrives, no Govee hardware is required in dry-run/simulation mode, and
   audible playback is unchanged.
5. Run a real capture for at least five minutes. Verify produced MP3/sidecar
   artifacts and no PipeWire underruns or capture-process zombie remains.
6. Stop the session with `Ctrl+C`. Verify FFmpeg exits, the PipeWire route
   remains healthy, and later capture sessions can reopen the same source.
7. Repeat with an intentionally invalid source. Verify a clear error listing
   available Pulse sources rather than a DirectShow/VB-Cable message.

## Non-goals

- Replacing PipeWire, WirePlumber, or the user's desktop audio policy.
- Automatically creating or persisting the virtual sink; document commands
  first, then consider an opt-in helper in a later release.
- Changing Govee LAN/BLE transport behavior.
- Rewriting offline analysis, the show compiler, or the renderer.
- Removing Windows DirectShow/VB-Cable support.

## Definition of done

- DreamSync selects a Pulse monitor source on Linux and launches FFmpeg with
  `-f pulse`.
- The existing DirectShow path continues to pass Windows regression tests.
- Capture, analysis, compile, and dry-run playback operate on Linux.
- The Linux pipeline can capture `dreamsync_capture.monitor` while PipeWire,
  not DreamSync, keeps audio audible at the physical speakers.
- Error messages, CLI output, GUI help, and tests describe the selected backend
  accurately.
