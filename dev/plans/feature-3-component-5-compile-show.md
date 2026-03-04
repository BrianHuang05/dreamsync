# Feature 3, Component 5 — compile_show() Orchestrator

**Status**: NOT STARTED

---

## Overview

The `compile_show()` orchestrator is the top-level entry point for the Show Compiler. It wires D3.1–D3.4 together into a single function call: `SongStructure` in, `ShowTimeline` out. It also provides two CLI subcommands — `compile` (structure JSON to show JSON) and `compile-and-play` (mp3 → analyze → compile → play in one step). The orchestrator itself contains no business logic beyond calling each component in order and passing results forward. It also exposes all tuning parameters as keyword arguments so callers can override defaults without modifying component internals.

---

## Architecture

```
┌──────────────────────────────────────────────┐
│  compile_show(structure, profile, **kwargs)    │
│                                               │
│  Step 1:  NarrativeArcPlanner.plan(sections)  │
│           → list[ArcWeight]                   │
│                                               │
│  Step 2:  TreatmentSelector(profile, seed)    │
│           .select(mood, label, bpm)           │
│           → list[Treatment]  (one per section)│
│                                               │
│  Step 3:  TransitionPlanner.plan(sections,    │
│                                  beat_grid)   │
│           → list[TransitionPlan]              │
│                                               │
│  Step 4:  TimelineAssembler.assemble(         │
│             structure, arc_weights,            │
│             treatments, transition_plans)      │
│           → ShowTimeline                      │
│                                               │
│  Return ShowTimeline                          │
└──────────────────────────────────────────────┘

CLI subcommands:

  dreamsync compile structure.json
      --profile midnight_rave
      --output show.json
      --seed 42
      --summary

  dreamsync compile-and-play song.mp3
      --profile midnight_rave
      --config devices.yaml
      --seed 42
```

### File Layout

```
src/dreamsync/
├── compiler/
│   ├── __init__.py          # Re-export compile_show
│   ├── arc.py               # D3.1 (already created)
│   ├── treatments.py        # D3.2 (already created)
│   ├── transitions.py       # D3.3 (already created)
│   ├── assemble.py          # D3.4 (already created)
│   └── compile.py           # compile_show() orchestrator
```

### Integration Points

- **NarrativeArcPlanner (D3.1)** — `plan(sections) → list[ArcWeight]`.
- **TreatmentSelector (D3.2)** — `select(mood, label, bpm) → Treatment` per section.
- **TransitionPlanner (D3.3)** — `plan(sections, beat_grid) → list[TransitionPlan]`.
- **TimelineAssembler (D3.4)** — `assemble(structure, arc_weights, treatments, transition_plans) → ShowTimeline`.
- **SongStructure (analyzer/models.py)** — Input model. Loaded from JSON file via CLI.
- **ShowTimeline (show/models.py)** — Output model. Written to JSON file via CLI.
- **ProfileConfig (profile.py)** — Optional input. Loaded via `resolve_profile_path()` + `load_profile()`.
- **analyze_song() (analyzer/analyze.py)** — Used by `compile-and-play` subcommand to analyze audio first.
- **run_show_playback() (show/runtime.py)** — Used by `compile-and-play` subcommand to play the compiled show.
- **cli.py** — Two new subcommands: `compile` and `compile-and-play`.

---

## D3.5: Orchestrator — `compile_show()`

### Design

The top-level entry point that wires D3.1–D3.4 together. Single function call: structure in, timeline out.

```python
def compile_show(
    structure: SongStructure,
    profile: ProfileConfig | None = None,
    *,
    seed: int | None = None,
    intro_intensity: float = 0.15,
    outro_intensity: float = 0.10,
    peak_boost: float = 1.2,
    buildup_ramp: float = 0.15,
    bridge_reduction: float = 0.80,
    default_fade_beats: int = 4,
    max_fade_beats: int = 16,
) -> ShowTimeline:
    """Compile a SongStructure into a ShowTimeline.

    Steps:
    1. Plan narrative arc → arc_weights
    2. Select treatments for each section → treatments
    3. Plan transitions → transition_plans
    4. Assemble timeline → ShowTimeline
    """
```

**Orchestration flow:**

1. Create `NarrativeArcPlanner(intro_intensity, outro_intensity, peak_boost, buildup_ramp, bridge_reduction)`.
2. Call `planner.plan(structure.sections)` → `arc_weights`.
3. Create `TreatmentSelector(profile, seed)`.
4. For each section in `structure.sections`, call `selector.select(section.mood, section.label, section.bpm)` → collect into `treatments` list.
5. Create `TransitionPlanner(default_fade_beats, max_fade_beats)`.
6. Call `transition_planner.plan(structure.sections, structure.beat_grid)` → `transition_plans`.
7. Create `TimelineAssembler()`.
8. Call `assembler.assemble(structure, arc_weights, treatments, transition_plans)` → `ShowTimeline`.
9. Return the `ShowTimeline`.

**CLI: `compile` subcommand:**

```
dreamsync compile structure.json --profile midnight_rave --output show.json --seed 42 --summary
```

Arguments:
- `structure_path` (positional): Path to a `SongStructure` JSON file.
- `--profile` (optional): Profile name or path. Resolved via `resolve_profile_path()` + `load_profile()`.
- `--output` / `-o` (optional): Path to write the `ShowTimeline` JSON. If omitted, prints JSON to stdout.
- `--seed` (optional): Integer seed for deterministic treatment selection.
- `--summary` (flag): Print a human-readable summary instead of (or in addition to) JSON output.

Execution:
1. Load `SongStructure.from_json(structure_path)`.
2. Load profile if `--profile` is provided.
3. Call `compile_show(structure, profile, seed=seed)`.
4. If `--output`, write `timeline.to_json(output_path)` and print confirmation.
5. If `--summary`, print summary: song path, duration, BPM, section count, cue count, and a table of cues with time, render_mode, effect, transition.
6. If neither `--output` nor `--summary`, dump JSON to stdout.

**CLI: `compile-and-play` subcommand:**

```
dreamsync compile-and-play song.mp3 --profile midnight_rave --config devices.yaml --seed 42
```

Arguments:
- `mp3_path` (positional): Path to an mp3 (or any audio) file.
- `--profile` (optional): Profile name or path.
- `--config` (required): Path to YAML device config file.
- `--seed` (optional): Seed for determinism.
- `--output` (optional): Path to save the compiled show JSON (for debugging/reuse).
- Audio/playback options inherited from the `play` subcommand: `--sample-rate`, `--audio-device`, `--fps`, `--brightness`, `--mirror/--no-mirror`, `--debug`.

Execution:
1. Print "Analyzing {mp3_path}..."
2. Call `analyze_song(mp3_path)` → `structure`.
3. Load profile if `--profile` is provided.
4. Print "Compiling show..."
5. Call `compile_show(structure, profile, seed=seed)` → `timeline`.
6. If `--output`, write `timeline.to_json(output_path)`.
7. Print "Playing show..."
8. Call `run_show_playback(mp3_path, timeline, config_path, ...)`.

**Summary format:**

```
Show compiled: song.mp3
  Duration:  3:42
  BPM:       128.0
  Sections:  7
  Cues:      7

  Time     Label     Mood    Effect         Transition  Beats  Intensity
  ──────── ───────── ─────── ────────────── ────────── ─────── ─────────
  0:00     intro     chill   slow_breathe   cut        0      0.15
  0:18     verse     groove  beat_pulse     fade       4      0.45
  0:52     chorus    hype    fast_scroll    fade       2      0.72
  1:24     verse     groove  color_scroll   fade       4      0.52
  1:58     bridge    chill   wave_drift     fade       4      0.35
  2:14     chorus    hype    fast_scroll    fade       2      0.86
  3:02     outro     chill   slow_breathe   fade       8      0.10
```

### Implementation Steps

1. Create `src/dreamsync/compiler/compile.py`:
   - Import all component classes: `NarrativeArcPlanner`, `TreatmentSelector`, `TransitionPlanner`, `TimelineAssembler`.
   - Import `SongStructure` from `dreamsync.analyzer.models`.
   - Import `ShowTimeline` from `dreamsync.show.models`.
   - Import `ProfileConfig` from `dreamsync.profile`.
   - `compile_show(structure, profile, *, seed, intro_intensity, outro_intensity, peak_boost, buildup_ramp, bridge_reduction, default_fade_beats, max_fade_beats)`:
     1. `arc_planner = NarrativeArcPlanner(intro_intensity, outro_intensity, peak_boost, buildup_ramp, bridge_reduction)`.
     2. `arc_weights = arc_planner.plan(structure.sections)`.
     3. `selector = TreatmentSelector(profile, seed)`.
     4. `treatments = [selector.select(s.mood, s.label, s.bpm) for s in structure.sections]`.
     5. `transition_planner = TransitionPlanner(default_fade_beats, max_fade_beats)`.
     6. `transition_plans = transition_planner.plan(structure.sections, structure.beat_grid)`.
     7. `assembler = TimelineAssembler()`.
     8. `timeline = assembler.assemble(structure, arc_weights, treatments, transition_plans)`.
     9. `return timeline`.
   - `format_summary(structure, timeline) -> str` — builds the human-readable summary string.

2. Update `src/dreamsync/compiler/__init__.py`:
   - Re-export `compile_show` for clean imports: `from dreamsync.compiler.compile import compile_show`.

3. Add CLI subcommands to `src/dreamsync/cli.py`:
   - `compile` subcommand:
     ```python
     compile_cmd = sub.add_parser(
         "compile",
         help="Compile a SongStructure into a ShowTimeline.",
     )
     compile_cmd.add_argument("structure_path", type=Path, help="Path to SongStructure JSON file.")
     compile_cmd.add_argument("--profile", type=str, default=None, help="Profile name or path.")
     compile_cmd.add_argument("--output", "-o", type=Path, default=None, help="Output show JSON path.")
     compile_cmd.add_argument("--seed", type=int, default=None, help="Random seed for determinism.")
     compile_cmd.add_argument("--summary", action="store_true", help="Print human-readable summary.")
     ```
   - `compile-and-play` subcommand:
     ```python
     cap_cmd = sub.add_parser(
         "compile-and-play",
         help="Analyze, compile, and play a show from an audio file.",
     )
     cap_cmd.add_argument("mp3_path", type=Path, help="Path to audio file.")
     cap_cmd.add_argument("--profile", type=str, default=None, help="Profile name or path.")
     cap_cmd.add_argument("--config", type=Path, required=True, help="Device config YAML.")
     cap_cmd.add_argument("--seed", type=int, default=None, help="Random seed for determinism.")
     cap_cmd.add_argument("--output", "-o", type=Path, default=None, help="Save compiled show JSON.")
     cap_cmd.add_argument("--sample-rate", type=int, default=44100, help="Audio sample rate.")
     cap_cmd.add_argument("--audio-device", type=int, default=None, help="Output audio device ID.")
     cap_cmd.add_argument("--fps", type=int, default=30, help="Device frame rate.")
     cap_cmd.add_argument("--brightness", type=float, default=1.0, help="Global brightness (0-1).")
     cap_cmd.add_argument("--mirror", dest="mirror", action="store_true", default=True)
     cap_cmd.add_argument("--no-mirror", dest="mirror", action="store_false")
     cap_cmd.add_argument("--debug", action="store_true", help="Print cue changes.")
     ```
   - Handler in `main()`:
     ```python
     elif args.command == "compile":
         from .analyzer.models import SongStructure
         from .compiler import compile_show
         from .compiler.compile import format_summary
         structure = SongStructure.from_json(args.structure_path)
         profile = _resolve_profile_from_args(args) if args.profile else None
         if profile == "error":
             return 1
         timeline = compile_show(structure, profile, seed=args.seed)
         if args.output:
             timeline.to_json(args.output)
             print(f"Show timeline written to {args.output}")
         if args.summary:
             print(format_summary(structure, timeline))
         if not args.output and not args.summary:
             import json
             print(json.dumps(timeline.to_dict(), indent=2))

     elif args.command == "compile-and-play":
         from .analyzer.analyze import analyze_song
         from .compiler import compile_show
         from .show.runtime import run_show_playback
         print(f"Analyzing {args.mp3_path}...")
         structure = analyze_song(args.mp3_path)
         profile = _resolve_profile_from_args(args) if args.profile else None
         if profile == "error":
             return 1
         print("Compiling show...")
         timeline = compile_show(structure, profile, seed=args.seed)
         if args.output:
             timeline.to_json(args.output)
             print(f"Show timeline saved to {args.output}")
         print("Playing show...")
         run_show_playback(
             mp3_path=args.mp3_path,
             timeline=timeline,
             config_path=args.config,
             sample_rate=args.sample_rate,
             audio_device=args.audio_device,
             fps=args.fps,
             brightness=args.brightness,
             mirror=args.mirror,
             debug=args.debug,
         )
     ```

### Done When

- [ ] `compile_show(structure)` produces a valid `ShowTimeline` with no profile (all built-in defaults)
- [ ] `compile_show(structure, profile)` respects profile overrides (treatments use profile pools)
- [ ] `compile_show(structure, seed=42)` produces identical output on repeated runs
- [ ] All kwargs (intro_intensity, peak_boost, etc.) are forwarded to the correct components
- [ ] Compilation completes in < 1 second for a typical song (no audio processing — just data mapping)
- [ ] CLI `dreamsync compile structure.json --output show.json` works end-to-end
- [ ] CLI `dreamsync compile structure.json --summary` prints human-readable overview
- [ ] CLI `dreamsync compile structure.json` (no --output, no --summary) prints JSON to stdout
- [ ] CLI `dreamsync compile-and-play song.mp3 --config devices.yaml` runs the full analyze → compile → play pipeline
- [ ] CLI `--profile` resolves profile names and paths correctly
- [ ] CLI `--seed` produces deterministic output
- [ ] Error handling: missing structure file, invalid profile, invalid JSON all produce clear error messages
- [ ] `compiler/__init__.py` re-exports `compile_show` for clean `from dreamsync.compiler import compile_show`
- [ ] 10 unit tests passing

---

## Tests

All tests in `dev/tests/test_compiler_compile.py`. Target: **10 tests**.

| # | Test Name | Description |
|---|-----------|-------------|
| 1 | `test_full_pipeline_no_profile` | Construct SongStructure in-memory, call compile_show(): produces valid ShowTimeline |
| 2 | `test_full_pipeline_with_profile` | Compile with a ProfileConfig: verify treatments use profile's effect/palette pools |
| 3 | `test_seed_determinism` | Two calls with same seed and structure produce identical timelines |
| 4 | `test_different_seeds_differ` | Two calls with different seeds produce different timelines (treatments differ) |
| 5 | `test_kwargs_forwarded` | Custom intro_intensity=0.5: verify first cue's intensity reflects the override |
| 6 | `test_single_section_song` | Song with one section: valid timeline with one cue |
| 7 | `test_many_sections` | Song with 10 sections: valid timeline with 10 cues, all in time order |
| 8 | `test_cli_compile_args` | Parse `compile structure.json --profile x --output y.json --seed 42 --summary`: verify all args captured |
| 9 | `test_cli_compile_and_play_args` | Parse `compile-and-play song.mp3 --config d.yaml --profile x --seed 42`: verify all args captured |
| 10 | `test_format_summary` | Call format_summary() with known data: verify output contains expected fields (duration, BPM, section count) |

### Test Strategy

- Construct `SongStructure` objects in-memory with `Section` tuples, `BeatGrid`, `TempoRegion`s, and placeholder metadata. No audio files needed.
- For profile tests: construct minimal `ProfileConfig` in-memory with known palette/effect pools.
- For seed tests: compare two `ShowTimeline` outputs field-by-field.
- For CLI tests: use `build_parser().parse_args(argv)` to verify argument parsing without executing the commands.
- For `format_summary` tests: verify the returned string contains expected substrings (duration formatting, BPM value, section count).
- No mocking of D3.1–D3.4 components — use real implementations for true integration testing.

---

## Parameters

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| `seed` | None | When None, treatment selection is random. When set, enables deterministic output for testing/debugging. |
| `intro_intensity` | 0.15 | Passed through to NarrativeArcPlanner |
| `outro_intensity` | 0.10 | Passed through to NarrativeArcPlanner |
| `peak_boost` | 1.2 | Passed through to NarrativeArcPlanner |
| `buildup_ramp` | 0.15 | Passed through to NarrativeArcPlanner |
| `bridge_reduction` | 0.80 | Passed through to NarrativeArcPlanner |
| `default_fade_beats` | 4 | Passed through to TransitionPlanner |
| `max_fade_beats` | 16 | Passed through to TransitionPlanner |

---

## Build Order

| Phase | Step | Files Created/Modified |
|-------|------|----------------------|
| 1 | Implement `compile_show()` and `format_summary()` | `src/dreamsync/compiler/compile.py` |
| 2 | Update `compiler/__init__.py` with re-export | `src/dreamsync/compiler/__init__.py` |
| 3 | Add `compile` and `compile-and-play` CLI subcommands | `src/dreamsync/cli.py` |
| 4 | Write unit tests | `dev/tests/test_compiler_compile.py` |
| 5 | Manual validation: compile 5+ songs across genres | — |

Note: This component depends on all four preceding components (D3.1–D3.4). It must be built after them. However, the CLI wiring and `format_summary()` can be developed in parallel with D3.4.

---

## Non-Goals

- **Batch compilation**: The `compile` CLI compiles one song at a time. A `compile-dir` batch command (like `analyze-dir`) is not in scope but could be added later by iterating over JSON files.
- **Show preview / dry run**: There is no "preview" mode that visualizes the show without hardware. The `--summary` flag provides a text-based overview, but no visual preview (like an HTML animation or terminal color display).
- **Auto-compile in session**: Automatic compilation when the Analyzer produces a new `SongStructure` during a Spotify-connected session is planned for future integration but not part of this component. The wiring in `session.py` is deferred.
- **Show caching**: The compiler always re-compiles from scratch. Caching compiled shows for re-use is Feature 4 (Show Cache), which builds on top of the compiler.
- **Interactive tuning**: There is no GUI for adjusting arc/transition parameters in real time. Parameters are set via kwargs or CLI flags.

---

## Dependencies

| Dependency | Type | Notes |
|------------|------|-------|
| `NarrativeArcPlanner`, `ArcWeight` | Feature 3, D3.1 | `src/dreamsync/compiler/arc.py` |
| `TreatmentSelector`, `Treatment` | Feature 3, D3.2 | `src/dreamsync/compiler/treatments.py` |
| `TransitionPlanner`, `TransitionPlan` | Feature 3, D3.3 | `src/dreamsync/compiler/transitions.py` |
| `TimelineAssembler` | Feature 3, D3.4 | `src/dreamsync/compiler/assemble.py` |
| `SongStructure` | Existing code | `src/dreamsync/analyzer/models.py` |
| `ShowTimeline` | Existing code | `src/dreamsync/show/models.py` |
| `ProfileConfig` | Existing code | `src/dreamsync/profile.py` |
| `load_profile`, `resolve_profile_path` | Existing code | `src/dreamsync/profile.py` — for CLI profile loading |
| `analyze_song` | Existing code | `src/dreamsync/analyzer/analyze.py` — for compile-and-play CLI |
| `run_show_playback` | Existing code | `src/dreamsync/show/runtime.py` — for compile-and-play CLI |
| `json` | Stdlib | JSON output to stdout |
| `argparse` | Stdlib | CLI subcommand definitions |

No new pip dependencies required. The orchestrator is pure wiring — all business logic lives in D3.1–D3.4.
