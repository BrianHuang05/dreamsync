# C2 — Unicode-Safe Callback Output

## Component
Capture-Meta Fixes — C2

## Prerequisites
- None (independent fix)
- Understanding of the track-change callback chain:
  - `SpotifyQueueWatcher` calls `_on_track_changed(new_track, old_track)`
  - In `cli.py` and `session.py`, the original callback is a `lambda` that calls `print(f"Spotify: now playing '{new.name}' by {new.artist}")`
  - On Windows, the console's default `charmap` codec cannot encode non-ASCII characters (Japanese, emoji, etc.)

## Goal
Make all `print()` calls in track-change callbacks safe for non-ASCII characters on Windows, so that track names in any language are displayed (with substitutions if necessary) instead of raising `UnicodeEncodeError`.

---

## Problem

### Root Cause
Windows console uses `cp1252` (or similar `charmap` codec) by default. When `print()` outputs a string containing characters outside the codec's range, Python raises:
```
UnicodeEncodeError: 'charmap' codec can't encode characters in position 22-29: character maps to <undefined>
```

### Affected Code

**`cli.py` L1116-1118:**
```python
on_track_changed=lambda new, old: print(
    f"Spotify: now playing '{new.name}' by {new.artist}"
),
```

**`session.py` L159-161:**
```python
on_track_changed=lambda new, old: print(
    f"Spotify: now playing '{new.name}' by {new.artist}"
),
```

### Impact
When the exception is raised inside `_orig(new, old)` (the original lambda), it propagates up through `_capture_track_changed()` and prevents `capture_orchestrator.on_track_change()` from being called. Song boundaries for non-ASCII tracks are silently lost. (The propagation issue is addressed in C3; this component prevents the exception from being raised at all.)

---

## Design Decision

**Approach: `sys.stdout` encoding with `errors="replace"` is not feasible** — mutating `sys.stdout` globally would affect all output. Instead, use a safe-print helper that encodes/decodes with `errors="replace"` before printing.

**Chosen approach: `errors="replace"` in the format string output.**

The simplest fix is to encode the string to the console's encoding with `errors="replace"`, then decode back:

```python
def _safe_print(msg: str) -> None:
    """Print a string, replacing characters that can't be encoded by the console."""
    try:
        print(msg)
    except UnicodeEncodeError:
        encoded = msg.encode(sys.stdout.encoding or "utf-8", errors="replace")
        print(encoded.decode(sys.stdout.encoding or "utf-8", errors="replace"))
```

However, this is more complex than needed. The simplest approach is to just wrap the print in a try/except and fall back to ASCII-safe output:

**Simplest approach (chosen):** Encode the problematic parts (track name, artist) with `errors="replace"` before formatting:

```python
on_track_changed=lambda new, old: print(
    f"Spotify: now playing '{new.name}' by {new.artist}".encode(
        sys.stdout.encoding or "utf-8", errors="replace"
    ).decode(sys.stdout.encoding or "utf-8", errors="replace")
),
```

**Even simpler (final choice):** Since the only issue is `print()` raising on non-encodable chars, and this is a display-only message, wrapping in try/except with a fallback is the cleanest fix. But since C3 will wrap `_orig()` in try/except anyway, the simplest standalone fix is to use `errors="replace"` encoding on the string:

```python
on_track_changed=lambda new, old: print(
    "Spotify: now playing '{}' by {}".format(
        new.name.encode("ascii", errors="replace").decode("ascii"),
        new.artist.encode("ascii", errors="replace").decode("ascii"),
    )
),
```

**Final decision:** Use `sys.stdout.encoding` (not `"ascii"`) to preserve as many characters as possible. Only replace what the console truly can't display.

---

## Implementation

### File: `src/dreamsync/cli.py`

**Before (L1116-1118):**
```python
on_track_changed=lambda new, old: print(
    f"Spotify: now playing '{new.name}' by {new.artist}"
),
```

**After:**
```python
on_track_changed=lambda new, old: print(
    "Spotify: now playing '{}' by {}".format(
        new.name.encode(
            sys.stdout.encoding or "utf-8", errors="replace"
        ).decode(sys.stdout.encoding or "utf-8", errors="replace"),
        new.artist.encode(
            sys.stdout.encoding or "utf-8", errors="replace"
        ).decode(sys.stdout.encoding or "utf-8", errors="replace"),
    )
),
```

**Ensure `import sys` is present** at the top of `cli.py`.

### File: `src/dreamsync/session.py`

**Before (L159-161):**
```python
on_track_changed=lambda new, old: print(
    f"Spotify: now playing '{new.name}' by {new.artist}"
),
```

**After:**
```python
on_track_changed=lambda new, old: print(
    "Spotify: now playing '{}' by {}".format(
        new.name.encode(
            sys.stdout.encoding or "utf-8", errors="replace"
        ).decode(sys.stdout.encoding or "utf-8", errors="replace"),
        new.artist.encode(
            sys.stdout.encoding or "utf-8", errors="replace"
        ).decode(sys.stdout.encoding or "utf-8", errors="replace"),
    )
),
```

**Ensure `import sys` is present** at the top of `session.py`.

### Alternative (simpler, recommended)

If the lambda becomes too long, extract a helper function near the top of the callback setup section:

```python
def _safe_track_msg(new):
    """Format track info safely for console output."""
    enc = sys.stdout.encoding or "utf-8"
    name = new.name.encode(enc, errors="replace").decode(enc, errors="replace")
    artist = new.artist.encode(enc, errors="replace").decode(enc, errors="replace")
    return f"Spotify: now playing '{name}' by {artist}"
```

Then:
```python
on_track_changed=lambda new, old: print(_safe_track_msg(new)),
```

This is cleaner and reusable across both files. **This is the recommended approach.**

---

## Tests

Test file: `dev/tests/test_unicode_callback.py`

- [ ] `test_ascii_track_prints_normally` — Create a `SpotifyTrack` with ASCII name/artist. Call `_safe_track_msg(track)`. Assert output contains the exact name and artist.

- [ ] `test_japanese_track_no_exception` — Create a `SpotifyTrack` with `name="ロベリア"`, `artist="りぶ"`. Call `_safe_track_msg(track)`. Assert no exception raised.

- [ ] `test_japanese_track_contains_replacements` — On a system with non-UTF-8 stdout encoding (mock `sys.stdout.encoding = "cp1252"`), call `_safe_track_msg(track)` with Japanese text. Assert result contains `?` replacement characters (or equivalent) instead of raising.

- [ ] `test_mixed_ascii_unicode` — Track with `name="Otome Kaibou (乙女解剖)"`, `artist="Rib"`. Assert ASCII parts are preserved, non-encodable parts are replaced.

- [ ] `test_emoji_in_track_name` — Track with `name="Song 🎵"`. Assert no exception and output is printable.

---

## Acceptance Criteria

- [ ] `print()` calls in track-change lambdas never raise `UnicodeEncodeError`
- [ ] ASCII track names display unchanged
- [ ] Non-ASCII characters are replaced with `?` (or codec equivalent) when the console can't encode them
- [ ] Non-ASCII characters display correctly on consoles that support them (e.g., UTF-8 terminal)
- [ ] `sys` is imported in both `cli.py` and `session.py`
- [ ] All 5 tests pass
- [ ] The `on_segment_saved` callback (L1135-1138 in `cli.py`) should also be audited — it prints `meta.get('artist')` and `meta.get('song_title')` which could contain non-ASCII text. Apply same encoding-safe treatment if needed.
