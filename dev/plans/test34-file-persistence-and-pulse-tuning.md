# Plan: Test 34 Fixes — File Persistence & Pulse Decay Tuning

Priority: P1 | Effort: Low–Medium | Source: Test 34 live run (2026-03-13)

---

## Context

Test 34 (`compile-and-play`) revealed three issues on the first live run:

1. **No analysis file produced** — `analyze_song()` returns an in-memory `SongStructure` but never persists it. The pipeline is supposed to be file-based so artifacts can be inspected, re-used, and replayed.
2. **No compiled show file produced** — The show JSON was written to a temp file that got deleted after playback. The `--output` flag existed but nothing was saved by default.
3. **Lights too fast / strobing** — `pulse_decay=12.0` meant brightness dropped to <1% between beats at 136 BPM. Bulbs appeared to flash/strobe rather than pulse rhythmically.

### Key observation

Issue 3 (pulse_decay) has been tuned twice now:
- Original: 6.0 (too slow — bulbs appeared to breathe, not flash on beat)
- Issue 4 fix: 12.0 (too fast — strobing at high BPM)
- This fix: 4.0 for `beat_pulse`, 6.0 for `drop_blast` (rhythmic hold between beats)

The decay math at 136 BPM (beat interval = 0.44s):
| Decay | Brightness after 1 beat | Visual effect |
|-------|------------------------|---------------|
| 4.0   | 17% (e^{-1.76})        | Visible glow between beats, rhythmic |
| 6.0   | 7% (e^{-2.64})         | Dim but present, punchy |
| 12.0  | 0.5% (e^{-5.28})       | Strobe — fully dark between beats |

---

## Deliverable 1: Auto-Save Analysis and Show Files (DONE)

### Prerequisites
- `SongStructure.to_json(path)` — already exists in `analyzer/models.py`
- `ShowTimeline.to_json(path)` — already exists in `show/models.py`
- `compile-and-play` handler in `cli.py` (lines ~2057–2160)

### Problem
The `compile-and-play` command ran the full pipeline (analyze → compile → play) but:
- Analysis result (`SongStructure`) was only held in memory — never written to disk
- Show timeline was written to `tempfile.mktemp()` then deleted in a `finally` block
- The `--output` flag existed but saved nothing by default

This meant no artifacts survived a run — you couldn't inspect the analysis, replay the show, or debug the compiler output.

### Solution (implemented)
Derive artifact paths from the MP3 path and save automatically:

```python
mp3_stem = args.mp3_path.with_suffix("")
analysis_path = Path(f"{mp3_stem}.analysis.json")
show_path = args.output or Path(f"{mp3_stem}.show.json")
```

After analysis: `structure.to_json(analysis_path)`
After compilation: `timeline.to_json(show_path)`

The show file is then reused directly for `run_show_playback()` — no temp file needed.

### Files Modified
- `src/dreamsync/cli.py` — `compile-and-play` handler: save analysis and show, remove temp file logic

### Artifacts produced (example)
```
out/capture-boundary/2026-03-06_00-15-02_Vacation Manor_-_If Only for Tonight - Midnight Version.mp3
out/capture-boundary/2026-03-06_00-15-02_Vacation Manor_-_If Only for Tonight - Midnight Version.analysis.json
out/capture-boundary/2026-03-06_00-15-02_Vacation Manor_-_If Only for Tonight - Midnight Version.show.json
```

### Completion Criteria
- [x] `compile-and-play` produces `.analysis.json` alongside the MP3
- [x] `compile-and-play` produces `.show.json` alongside the MP3 (or at `--output` path)
- [x] Show file is reused for playback (no temp file created/deleted)
- [x] `--output` flag still works (overrides default show path)
- [x] Cache path (`--cache-dir`) still works — analysis saved before cache check
- [x] All 1676 tests pass

---

## Deliverable 2: Pulse Decay Tuning (DONE)

### Prerequisites
- `src/dreamsync/effects.py` — `EFFECTS["beat_pulse"]` and `EFFECTS["drop_blast"]` define `pulse_decay` in params
- `src/dreamsync/render.py` — `SegmentRenderer._pulse_decay` default, used when params don't specify a value
- `_render_pulse()` math: `brightness *= exp(-decay * dt)`, fires to 1.0 on beat

### Problem
At `pulse_decay=12.0` and 136 BPM (0.44s beat interval):
- Brightness after one beat: `e^(-12 * 0.44) = 0.5%`
- Below 10% after just 190ms
- Visual result: full strobe — LEDs appear to flash on/off rather than pulse rhythmically

### Solution (implemented)
Reduced decay values to hold visible brightness between beats:

| Preset | Old decay | New decay | Brightness after 1 beat (136 BPM) |
|--------|-----------|-----------|-----------------------------------|
| `beat_pulse` | 12.0 | 4.0 | 17% — visible glow, rhythmic feel |
| `drop_blast` | 10.0 | 6.0 | 7% — punchier, still readable |

Also updated `SegmentRenderer._pulse_decay` default from 12.0 → 4.0 to match.

### Files Modified
- `src/dreamsync/effects.py` — `beat_pulse` decay 12.0 → 4.0, `drop_blast` decay 10.0 → 6.0
- `src/dreamsync/render.py` — `_pulse_decay` default 12.0 → 4.0
- `dev/tests/test_effects.py` — updated `EffectDecayTests` to expect new values

### Completion Criteria
- [x] `beat_pulse` decay = 4.0
- [x] `drop_blast` decay = 6.0
- [x] Default renderer decay = 4.0
- [x] Tests updated and passing
- [x] All 1676 tests pass

---

## Deliverable 3: MP3 Compression at Session End (DONE)

### Prerequisites
- Deliverable 1 (file persistence) — `.analysis.json` and `.show.json` files now persist alongside MP3s
- Python `zipfile` stdlib module (no new dependencies)
- Storage context: 457 GB free on 932 GB drive (not urgent, but good hygiene)

### Problem
The file-based pipeline now produces three files per song:
- `song.mp3` — 3–10 MB (the bulk of storage)
- `song.analysis.json` — 5–20 KB (negligible)
- `song.show.json` — 2–10 KB (negligible)

Over many sessions, MP3s accumulate. JSON files are tiny and useful to keep accessible (re-run shows, inspect analysis, debug compiler). MP3s are the storage concern.

### Design Decision
- **Zip MP3s** at session end (or on demand) to reclaim space
- **Leave JSON files uncompressed** — they're tiny and should remain directly accessible for re-runs and inspection
- Do NOT delete any artifacts — compress, don't discard

### Solution

#### 3A: `archive_mp3s()` utility function

```python
# src/dreamsync/capture/archiver.py (new file)

def archive_mp3s(
    directory: Path,
    *,
    archive_name: str | None = None,
    delete_originals: bool = True,
    min_age_seconds: float = 0,
) -> Path | None:
    """Zip all MP3 files in *directory* into a single archive.

    Returns the archive path, or None if no MP3s found.
    Skips files newer than *min_age_seconds* (avoids archiving in-progress captures).
    JSON sidecar files (.analysis.json, .show.json, .meta.json) are left untouched.
    """
```

- Scans for `*.mp3` in the directory
- Creates `{directory}/{archive_name}.zip` (default: `archived_{timestamp}.zip`)
- Adds each MP3 with `ZIP_DEFLATED` compression (MP3s are already compressed, so zip mainly bundles them — expect ~5-10% size reduction)
- If `delete_originals=True`, removes the MP3 after successful addition to archive
- Returns the archive path

#### 3B: CLI `archive` subcommand

```bash
# Archive MP3s in a capture directory, keeping JSON files accessible
python -m dreamsync archive out/capture-boundary/

# Archive with custom name
python -m dreamsync archive out/live-test/ --name "session-2026-03-13"

# Dry run — show what would be archived
python -m dreamsync archive out/capture-boundary/ --dry-run

# Keep originals (just create the zip, don't delete MP3s)
python -m dreamsync archive out/capture-boundary/ --keep
```

#### 3C: Auto-archive at session end (optional hook)

Add an `--archive` flag to the `session` command that calls `archive_mp3s()` on the capture directory after `Ctrl+C` shutdown:

```python
# In session cleanup (after capture flush)
if args.archive and args.capture_dir:
    from dreamsync.capture.archiver import archive_mp3s
    archive_path = archive_mp3s(args.capture_dir)
    if archive_path:
        print(f"Archived MP3s to {archive_path}")
```

### Files to Create
| File | Purpose |
|------|---------|
| `src/dreamsync/capture/archiver.py` | `archive_mp3s()` utility |
| `dev/tests/test_archiver.py` | Unit tests |

### Files to Modify
| File | Change |
|------|--------|
| `src/dreamsync/cli.py` | Add `archive` subcommand, add `--archive` flag to `session` |

### Unit Tests (8)
1. `test_archive_creates_zip` — directory with 3 MP3s → zip created, contains 3 entries
2. `test_archive_deletes_originals` — `delete_originals=True` → MP3s removed after zip
3. `test_archive_keeps_originals` — `delete_originals=False` → MP3s still exist
4. `test_archive_leaves_json_untouched` — `.analysis.json`, `.show.json`, `.meta.json` files are not archived or deleted
5. `test_archive_empty_directory` — no MP3s → returns None, no zip created
6. `test_archive_min_age_skips_recent` — MP3 younger than `min_age_seconds` is skipped
7. `test_archive_custom_name` — `archive_name="my-session"` → `my-session.zip`
8. `test_archive_default_name` — default name includes timestamp

### Completion Criteria
- [x] `archive_mp3s()` zips MP3s and leaves JSON files untouched
- [x] `python -m dreamsync archive <dir>` works from CLI
- [x] `--dry-run` shows plan without modifying files
- [x] `--keep` creates zip without deleting originals
- [x] `session --archive` auto-archives on clean shutdown
- [x] All 8 new tests pass
- [x] All existing tests pass (no regressions)

### Verify
```bash
# Unit tests
python -m pytest dev/tests/test_archiver.py -v

# Manual: archive a capture directory
python -m dreamsync archive out/capture-boundary/ --dry-run
python -m dreamsync archive out/capture-boundary/

# Confirm JSON files remain, MP3s are in zip
ls out/capture-boundary/*.json
unzip -l out/capture-boundary/archived_*.zip
```

---

## Summary

| # | Deliverable | Status | Files | Tests |
|---|-------------|--------|-------|-------|
| 1 | Auto-save analysis + show files | DONE | `cli.py` | 0 new (existing pass) |
| 2 | Pulse decay tuning (4.0 / 6.0) | DONE | `effects.py`, `render.py`, `test_effects.py` | 2 updated |
| 3 | MP3 compression at session end | DONE | `archiver.py` (new), `cli.py`, `test_archiver.py` | 8 new |

## Final Verification

```bash
# Full test suite (no regressions)
python -m pytest dev/tests/ -v

# Compile-and-play produces artifacts
python -m dreamsync compile-and-play path/to/song.mp3 --config dev/devices.yaml --debug
ls path/to/song.analysis.json path/to/song.show.json

# Archive MP3s
python -m dreamsync archive out/capture-boundary/ --dry-run
```
