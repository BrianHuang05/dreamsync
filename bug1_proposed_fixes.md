# Bug 1 Proposed Fixes: Song Boundary Detector Over-Triggers

Five root causes identified, with a proposed fix for each.

---

## Fix 1: Raise min_song_seconds to 120

**Problem**: `min_song_seconds=45.0` is the dominant gating factor — every inter-boundary
delta clusters at 31–55 seconds, proving the silence condition is met nearly continuously
and the floor is the only thing preventing constant boundary firing. Real songs are
180–300 seconds; a 45-second floor lets 3–5x too many boundaries through.

**File**: `src/dreamsync/live.py`, `SongBoundaryDetector.__init__`

**Change**: Raise the minimum song length from 45.0s to 120.0s.

```python
# Before (line 342)
min_song_seconds: float = 45.0,

# After
min_song_seconds: float = 120.0,
```

**Effect**: In the 600s test with `min_song_seconds=30`, 16 boundaries fired (one every
~37s). Raising to 120s means at most 5 boundaries can fire in 600s, and only if the
silence condition is also met each time. Real songs at 180–300s still trigger correctly;
only genuinely short tracks (<2 min) would be missed, which are rare in bar/DJ contexts.

**Tradeoff**: If a DJ plays a short interlude or jingle (<2 min), the boundary won't fire
and state won't reset until the next real gap after the 120s floor. This is acceptable —
a missed reset is far less disruptive than a false reset (which causes BPM loss, mood
flash, and visual jarring).

---

## Fix 2: Raise silence_threshold_rms from 0.005 to 0.015

**Problem**: `silence_threshold_rms=0.005` is near digital silence (~-46 dBFS). In a bar
environment with system audio loopback, normal musical content momentarily dips below
this threshold during vocal-only passages, synth pad transitions, DJ crossfade zones,
and breakdowns. These are not song boundaries — they are normal musical dynamics.

**File**: `src/dreamsync/live.py`, `SongBoundaryDetector.__init__`

**Change**: Raise the silence threshold from 0.005 to 0.015.

```python
# Before (line 340)
silence_threshold_rms: float = 0.005,

# After
silence_threshold_rms: float = 0.015,
```

**Effect**: 0.015 RMS corresponds to roughly -36 dBFS, still well below any audible music
but above the momentary dips that occur during musical dynamics. Real track gaps (true
silence between songs) typically drop to RMS < 0.001, so 0.015 still catches them easily.
The investigation data shows normalized Director energy of 0.3–0.8 right before false
boundaries — meaning the raw signal was clearly audible but had sub-second micro-dips
below 0.005.

**Tradeoff**: In a very quiet venue with low system volume, the RMS of actual track gaps
might stay above 0.015 due to ambient noise floor bleed. If the threshold is set too high
(>0.03), real gaps would be missed entirely. 0.015 is a conservative middle ground.

---

## Fix 3: Raise min_silence_seconds from 0.8 to 2.0

**Problem**: `min_silence_seconds=0.8` requires only 68 consecutive frames of silence
(at 44100/512). Musical breakdowns, buildups, and inter-note gaps in sparse arrangements
easily produce 0.8 seconds of below-threshold RMS. Real track gaps are 1–3 seconds.

**File**: `src/dreamsync/live.py`, `SongBoundaryDetector.__init__`

**Change**: Raise the minimum silence duration from 0.8s to 2.0s.

```python
# Before (line 341)
min_silence_seconds: float = 0.8,

# After
min_silence_seconds: float = 2.0,
```

**Effect**: At 44100 Hz / 512 hop, the required consecutive silent frames increases from
68 (`int(0.8 * 44100 / 512)`) to 172 (`int(2.0 * 44100 / 512)`). Musical breakdowns
rarely sustain below-threshold RMS for 2+ contiguous seconds, but real track gaps do.
A kick drum pattern at 128 BPM has inter-onset intervals of ~0.47s — even during a sparse
breakdown, gaps between hits won't accumulate 2 seconds of contiguous silence.

**Tradeoff**: Very tight DJ crossfades (where silence lasts only 1–1.5s) would be missed.
This is acceptable — tight crossfades are musically continuous and arguably should not
trigger a boundary reset anyway.

---

## Fix 4: Add explicit cooldown timer

**Problem**: After a boundary fires, `_frames_since_reset` is set to 0 and the cycle
restarts immediately. The only guard against re-triggering is `min_song_frames`, which is
a frame counter that starts from zero. There is no wall-clock cooldown, so if the silence
condition is met continuously (as the data proves), boundaries fire at exactly the floor
interval with no additional protection.

**File**: `src/dreamsync/live.py`, `SongBoundaryDetector.__init__` and `SongBoundaryDetector.update`

**Changes**:

### 4a. Add cooldown field to __init__

```python
# Before (lines 338-351)
def __init__(
    self,
    silence_threshold_rms: float = 0.005,
    min_silence_seconds: float = 0.8,
    min_song_seconds: float = 45.0,
    hop_size: int = 512,
    sample_rate: int = 44100,
) -> None:
    self.silence_threshold_rms = silence_threshold_rms
    self.min_silence_frames = int(min_silence_seconds * sample_rate / hop_size)
    self.min_song_frames = int(min_song_seconds * sample_rate / hop_size)
    self._silent_frames = 0
    self._frames_since_reset = 0
    self._boundary_count = 0

# After
def __init__(
    self,
    silence_threshold_rms: float = 0.015,
    min_silence_seconds: float = 2.0,
    min_song_seconds: float = 120.0,
    cooldown_seconds: float = 90.0,
    hop_size: int = 512,
    sample_rate: int = 44100,
) -> None:
    self.silence_threshold_rms = silence_threshold_rms
    self.min_silence_frames = int(min_silence_seconds * sample_rate / hop_size)
    self.min_song_frames = int(min_song_seconds * sample_rate / hop_size)
    self._cooldown_frames = int(cooldown_seconds * sample_rate / hop_size)
    self._silent_frames = 0
    self._frames_since_reset = 0
    self._boundary_count = 0
```

### 4b. Add cooldown check to update

```python
# Before (lines 353-369)
def update(self, rms: float) -> bool:
    """Feed one frame's RMS. Returns True on song boundary detection."""
    self._frames_since_reset += 1
    if rms < self.silence_threshold_rms:
        self._silent_frames += 1
    else:
        if (
            self._silent_frames >= self.min_silence_frames
            and self._frames_since_reset >= self.min_song_frames
        ):
            # Silence gap ended — this is the start of a new song
            self._silent_frames = 0
            self._frames_since_reset = 0
            self._boundary_count += 1
            return True
        self._silent_frames = 0
    return False

# After
def update(self, rms: float) -> bool:
    """Feed one frame's RMS. Returns True on song boundary detection."""
    self._frames_since_reset += 1
    if rms < self.silence_threshold_rms:
        self._silent_frames += 1
    else:
        if (
            self._silent_frames >= self.min_silence_frames
            and self._frames_since_reset >= self.min_song_frames
            and self._frames_since_reset >= self._cooldown_frames
        ):
            # Silence gap ended — this is the start of a new song
            self._silent_frames = 0
            self._frames_since_reset = 0
            self._boundary_count += 1
            return True
        self._silent_frames = 0
    return False
```

**Effect**: The cooldown provides a hard wall-clock floor (90s) that is independent of
the `min_song_frames` check. Even if `min_song_seconds` were somehow lowered in the
future, the cooldown prevents rapid re-triggering. With `min_song_seconds=120` and
`cooldown_seconds=90`, the cooldown is redundant (120 > 90), but it serves as a safety
net and makes the intent explicit in the code.

**Tradeoff**: If a DJ actually plays two short tracks back-to-back (both under 90s), the
second boundary would be missed. This is a rare edge case and the cost of a missed
boundary (slightly stale BPM/mood) is far lower than the cost of a false one (full state
reset, visual jarring, BPM loss).

---

## Fix 5: Require sustained post-silence energy

**Problem**: The current logic triggers a boundary on the *first* non-silent frame after
enough silent frames have accumulated (line 358–367). A single frame of noise — a mic
pop, a brief transient, or a single above-threshold sample during an ongoing quiet
passage — is enough to trigger the boundary. This makes the detector vulnerable to
isolated noise spikes during extended quiet sections.

**File**: `src/dreamsync/live.py`, `SongBoundaryDetector.__init__` and `SongBoundaryDetector.update`

**Changes**:

### 5a. Add confirmation counter to __init__

```python
# Add after self._boundary_count = 0 in __init__:
self._confirm_frames = 0
self._confirm_required = 3  # consecutive above-threshold frames needed
self._silence_armed = False
```

### 5b. Replace single-frame trigger with sustained energy check

```python
# Before (lines 353-369)
def update(self, rms: float) -> bool:
    """Feed one frame's RMS. Returns True on song boundary detection."""
    self._frames_since_reset += 1
    if rms < self.silence_threshold_rms:
        self._silent_frames += 1
    else:
        if (
            self._silent_frames >= self.min_silence_frames
            and self._frames_since_reset >= self.min_song_frames
        ):
            # Silence gap ended — this is the start of a new song
            self._silent_frames = 0
            self._frames_since_reset = 0
            self._boundary_count += 1
            return True
        self._silent_frames = 0
    return False

# After
def update(self, rms: float) -> bool:
    """Feed one frame's RMS. Returns True on song boundary detection."""
    self._frames_since_reset += 1
    if rms < self.silence_threshold_rms:
        self._silent_frames += 1
        # If we were confirming a boundary, reset — silence resumed
        self._confirm_frames = 0
        if (
            self._silent_frames >= self.min_silence_frames
            and self._frames_since_reset >= self.min_song_frames
        ):
            self._silence_armed = True
    else:
        if self._silence_armed:
            self._confirm_frames += 1
            if self._confirm_frames >= self._confirm_required:
                # Sustained energy after silence — this is a real boundary
                self._silent_frames = 0
                self._frames_since_reset = 0
                self._boundary_count += 1
                self._silence_armed = False
                self._confirm_frames = 0
                return True
        else:
            self._silent_frames = 0
            self._confirm_frames = 0
    return False
```

**Effect**: Instead of triggering on the first non-silent frame, the detector requires 3
consecutive above-threshold frames (~35ms at 44100/512) before confirming a boundary.
This filters out isolated noise spikes and single-frame transients that occur during
otherwise quiet passages. Real song starts have sustained energy that easily meets this
requirement.

**Tradeoff**: Adds ~35ms of latency to boundary detection (3 frames at ~11.6ms each).
This is imperceptible. The logic is slightly more complex, with a new `_silence_armed`
state that must be tracked. If silence dips back below threshold during the confirmation
window, the confirmation resets and re-arms only after the silence condition is met again.

---

## Implementation Priority

1. **Fix 1** (raise min_song_seconds to 120) — Highest impact, lowest risk. The
   investigation data proves the floor is the sole gating factor. This single change
   would reduce the 600s test from 16 boundaries to ~5 and the 300s test from 5 to ~2.
   No behavioral complexity added.

2. **Fix 3** (raise min_silence_seconds to 2.0) — High impact. Filters out the brief
   musical dips that arm the silence detector during normal playback. Combined with
   Fix 1, this makes false triggers extremely unlikely.

3. **Fix 2** (raise silence_threshold_rms to 0.015) — High impact. Raises the bar for
   what counts as "silence," eliminating micro-dips during audible music. Works
   synergistically with Fix 3.

4. **Fix 4** (explicit cooldown timer) — Medium impact. Provides defense-in-depth as a
   hard safety net. Redundant with Fix 1 at current values but protects against future
   parameter regressions and makes the minimum-gap intent explicit in code.

5. **Fix 5** (sustained post-silence energy) — Lower impact but improves correctness at
   the margins. Prevents edge cases where a single noise spike during a quiet passage
   triggers a false boundary. Worth implementing for robustness.

## Testing

After applying fixes, re-run the bar test sessions and compare:

- **Boundaries per 600s session** (target: 2–4, currently 16 with `min_song_seconds=30`,
  5 with `min_song_seconds=45`)
- **Mean inter-boundary delta** (target: 150–300s, currently 34s / 55s)
- **Min inter-boundary delta** (target: >120s, currently 31s / 32s)
- **False positive rate**: count boundaries where Director energy was >0.3 in the 10
  frames before the boundary fired (target: 0, currently ~10 of 16)
- **False negative rate**: play a playlist with known track boundaries and verify all
  real transitions are detected (target: 0 missed in a 30-minute session)
- **BPM resets per session**: should drop proportionally with boundary count (target:
  2–4, currently 16)
- **Hype flash count from Bug 3**: each false boundary triggers a mood reset → false
  HYPE flash; reducing boundaries directly reduces these (target: 0 false flashes)
