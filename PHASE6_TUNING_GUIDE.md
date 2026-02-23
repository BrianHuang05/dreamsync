# Phase 6: Tuning & Calibration Guide

This is the hands-on tuning phase for Director V2. Unlike Phases 1-5 (code), this phase is iterative: you play music, observe the lights, write down what feels wrong, and then we adjust thresholds/weights/palettes together.

---

## How the Composite Energy Metric Works

Instead of relying on raw RMS (which is useless through a laptop mic due to room acoustic compression), the system now computes a **composite energy score** (0.0-1.0) from four sub-features:

| Sub-feature        | Weight | What it measures                                                | Why it matters                                                               |
| ------------------ | ------ | --------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| **Normalized RMS** | 0.25   | Relative volume (auto-calibrated against rolling floor/ceiling) | Captures dynamics *within* a song regardless of absolute mic level           |
| **Spectral flux**  | 0.30   | Frame-to-frame spectral change                                  | Strongest signal for "punchiness" - high during percussive/rhythmic sections |
| **Bass ratio**     | 0.20   | Proportion of energy below 200Hz                                | Rhythmic music has prominent bass; distinguishes genres                      |
| **Onset strength** | 0.25   | Percussive transient strength                                   | Detects drum hits, attacks; high during beats                                |

The Director computes EMA-smoothed values for each sub-feature, normalizes them to [0,1], then takes their weighted sum. This composite `energy` score feeds the MoodClassifier instead of raw `ema_rms`.

**Self-calibration**: RMS is normalized against a slow-tracking floor (alpha=0.005) and faster-tracking ceiling (alpha=0.02). Spectral flux and onset strength normalize against a rolling max with decay (0.998/frame). This means the system auto-calibrates to any mic setup within ~10-15 seconds.

---

## My Devices

| Device | Model | IP | Segments | Transport |
|--------|-------|----|----------|-----------|
| LED strip (small) | H612F | `10.126.166.180` | 7 | `ptreal` |
| LED strip (large) | H808A | `10.126.166.156` | 25 | `razer` |

### Copy-paste device flags

Single device (H612F):
```
--device 10.126.166.180:7:primary:ptreal
```

Single device (H808A):
```
--device 10.126.166.156:25:primary:razer
```

Both devices:
```
--device 10.126.166.180:7:primary:ptreal --device 10.126.166.156:25:primary:razer
```

---

## Prerequisites

Before starting, confirm everything works:

```bash
# Run the test suite -- all should pass
python -m pytest tests/ -x -q

# Verify devices are reachable
python -c "import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.sendto(b'hello', ('10.126.166.180', 4003)); print('H612F OK')"
python -c "import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.sendto(b'hello', ('10.126.166.156', 4003)); print('H808A OK')"
```

---

## Step 1: Baseline Run with Debug Logging

**Goal:** See the system in action with full diagnostic output so you know what it's doing.

### 1a. Run with `--debug-mood`

Both devices, 5-minute session:
```bash
python -m dreamsync govee-live \
  --device 10.126.166.180:7:primary:ptreal \
  --device 10.126.166.156:25:primary:razer \
  --duration 300 \
  --debug-mood \
  --auto-cycle \
  --brightness 1.0
```

Single device (H612F only):
```bash
python -m dreamsync govee-live \
  --device 10.126.166.180:7:primary:ptreal \
  --duration 300 \
  --debug-mood \
  --auto-cycle \
  --brightness 1.0
```

### 1b. What debug output looks like

```
mood=chill effect=slow_breathe palette=cool mode=breathe energy=0.1234 stability=0.0456 bpm=120.0
```

Key values:
- `energy=` -- the composite energy score (0.0-1.0). Should span a range during music (quiet ~0.05-0.15, moderate ~0.25-0.45, loud ~0.50-0.80+)
- `stability=` -- beat stability (lower = more stable rhythm)
- `bpm=` -- smoothed BPM estimate

### 1c. Play a test playlist

Play these tracks (or equivalents) to exercise all four moods:

| Mood target | What to play | Why |
|---|---|---|
| CHILL | Ambient/downtempo intro, spoken word, or a quiet acoustic track | Low energy, unstable beat |
| GROOVE | Mid-tempo pop/funk/R&B verse (100-130 BPM, steady beat) | Moderate energy, stable rhythm |
| HYPE | EDM chorus, rock chorus, high-energy section (120-180 BPM) | High energy, fast stable beat |
| DROP | EDM build -> drop, breakdown -> chorus transition | Energy dip then spike |

### 1d. What to write down

While watching the lights with `--debug-mood` output scrolling, note:

```
FEEDBACK LOG -- Session 1 (Baseline)
Date: ____
Song: ____
Device: H612F (10.126.166.180, 7 seg, ptreal) / H808A (10.126.166.156, 25 seg, razer)

For each song, note:
---------------------------------------
Track: [song name / artist]
Genre: [genre]
BPM (approx): [if you know it]

Mood accuracy:
  - Did CHILL activate during quiet parts?        [ YES / NO / N/A ]
  - Did GROOVE activate during verses?             [ YES / NO / N/A ]
  - Did HYPE activate during choruses?             [ YES / NO / N/A ]
  - Did DROP fire on build->drop transitions?      [ YES / NO / N/A ]

Energy range observed:
  - Lowest energy= seen: ____
  - Highest energy= seen: ____
  - Typical quiet section: ____
  - Typical loud section: ____

Mood problems:
  - Stuck in wrong mood? Which one, and for how long?
  - Thrashing between moods? Between which two?
  - DROP false positives? When did they happen?
  - DROP missed? Describe what the audio was doing.

Effect feel:
  - Did effects match the energy?
  - Any effect feel wrong for the mood? Which one?
  - Cycling too fast (jarring)? Too slow (boring)?

Colors:
  - Palettes look good on your hardware?
  - Any palette too dim or washed out?

Transitions:
  - Mood transitions smooth or jarring?
  - Effect cycling transitions smooth or jarring?
---------------------------------------
```

---

## Step 2: Threshold Calibration

After Step 1, we review your feedback and adjust values.

### Current Thresholds (MoodConfig defaults)

All thresholds are on the composite energy scale (0.0-1.0):

| Parameter | Default | What it controls |
|---|---|---|
| `chill_energy_ceiling` | 0.20 | Max energy to enter CHILL |
| `chill_energy_exit` | 0.28 | Energy must exceed this to leave CHILL |
| `groove_energy_ceiling` | 0.50 | Energy above this -> HYPE (if beat is stable) |
| `groove_energy_exit_low` | 0.15 | Drop below this from GROOVE -> CHILL |
| `groove_energy_exit_high` | 0.58 | Exceed this from GROOVE -> HYPE |
| `hype_energy_exit` | 0.40 | Drop below this from HYPE -> GROOVE |
| `stability_threshold` | 0.07 | Max stability value for "stable beat" (lower = more stable) |
| `stability_exit` | 0.09 | Stability above this -> beat considered unstable |
| `min_bpm_for_groove` | 70.0 | BPM must be above this for GROOVE/HYPE |
| `drop_energy_spike` | 0.25 | Required energy jump magnitude for DROP detection |
| `drop_energy_dip` | 0.15 | Energy must dip below this before a DROP can fire |
| `drop_window` | 0.5s | Spike must occur within this time after dip |
| `drop_cooldown` | 10.0s | Minimum seconds between DROP detections |
| `drop_duration` | 3.0s | How long DROP mood lasts before auto-expiring |
| `min_dwell_seconds` | 4.0s | Minimum time in any mood before switching |

### Common problems and fixes

**"Energy stuck low (always < 0.15), never leaves CHILL"**
- Check composite weights: if your mic barely picks up bass, try `w_bass_ratio=0.10` and redistribute to `w_spectral_flux=0.35`
- Lower `chill_energy_ceiling` (try 0.12 or 0.15)
- Check mic placement: closer to speakers helps
- Check system volume: louder playback gives more dynamic range

**"Energy always high (> 0.5), never enters CHILL"**
- Raise `chill_energy_ceiling` (try 0.30 or 0.35)
- The auto-calibration may need more time; wait 10-15s after starting

**"Stuck in CHILL during moderate music"**
- Lower `chill_energy_ceiling` (try 0.15)
- Lower `chill_energy_exit` (try 0.22)

**"Never enters CHILL, always GROOVE or HYPE"**
- Raise `chill_energy_ceiling` (try 0.30)
- Audio input may be hot. Try moving mic further from speakers.

**"Thrashing between GROOVE and HYPE"**
- Widen the hysteresis gap: lower `hype_energy_exit` (e.g. 0.35) and/or raise `groove_energy_exit_high` (e.g. 0.65)
- Increase `min_dwell_seconds` (e.g. 6.0 or 8.0)

**"Thrashing between CHILL and GROOVE"**
- Widen the gap: lower `groove_energy_exit_low` (e.g. 0.10) and/or raise `chill_energy_exit` (e.g. 0.32)
- Increase `min_dwell_seconds`

**"DROP never fires"**
- Lower `drop_energy_spike` (e.g. 0.18 or 0.20)
- Raise `drop_energy_dip` (e.g. 0.20) so more sections qualify as "dip"
- Widen `drop_window` (e.g. 1.0s)

**"DROP fires on random loud moments"**
- Raise `drop_energy_spike` (e.g. 0.35)
- Lower `drop_energy_dip` (e.g. 0.10) to require a deeper dip
- Increase `drop_cooldown` (e.g. 15.0 or 20.0s)

**"Moods change too quickly"**
- Increase `min_dwell_seconds` (e.g. 6.0, 8.0, or even 10.0)

**"Moods change too slowly"**
- Decrease `min_dwell_seconds` (e.g. 2.0 or 3.0). Be careful -- below 2.0 will cause thrashing.

---

## Step 3: Composite Energy Weight Tuning

If the overall energy score doesn't match what you hear, adjust the sub-feature weights in `DirectorConfig`:

| Weight | Default | Increase if... | Decrease if... |
|---|---|---|---|
| `w_rms` | 0.25 | Volume differences matter more for your setup | Mic has poor dynamic range |
| `w_spectral_flux` | 0.30 | You want more sensitivity to rhythmic changes | Getting false "punchy" readings from noise |
| `w_bass_ratio` | 0.20 | Bass-heavy music should push energy higher | Mic doesn't capture bass well |
| `w_onset_strength` | 0.25 | Percussive transients should matter more | Getting spiky/noisy energy readings |

Weights should sum to 1.0.

### EMA smoothing alphas

Higher alpha = more responsive but noisier. Lower alpha = smoother but laggier.

| Parameter | Default | Range |
|---|---|---|
| `ema_alpha_spectral_flux` | 0.15 | 0.05 - 0.30 |
| `ema_alpha_bass_ratio` | 0.15 | 0.05 - 0.30 |
| `ema_alpha_onset_strength` | 0.15 | 0.05 - 0.30 |
| `ema_alpha_rms` | 0.20 | 0.05 - 0.40 |

### Normalization parameters

| Parameter | Default | What it does |
|---|---|---|
| `rms_floor_alpha` | 0.005 | How slowly the RMS floor tracks (lower = slower adaptation) |
| `rms_ceil_alpha` | 0.02 | How slowly the RMS ceiling tracks (lower = slower adaptation) |
| `flux_max_decay` | 0.998 | Rolling max decay for spectral flux (closer to 1.0 = slower decay) |
| `onset_max_decay` | 0.998 | Rolling max decay for onset strength (closer to 1.0 = slower decay) |

---

## Step 4: Effect Cycling Tuning

### Current Effect Pools & Weights

```
CHILL:   warm_glow (2.0), slow_breathe (3.0), color_breathe (1.0), wave_drift (2.0), gradient_flow (2.0)
GROOVE:  color_breathe (1.0), beat_pulse (3.0), color_scroll (2.0), wave_drift (1.0)
HYPE:    fast_scroll (2.0), beat_pulse (1.0)
DROP:    drop_blast (1.0)
```

Weights are relative. Higher weight = picked more often.

### Common problems and fixes

**"Effects cycle too fast / feel chaotic"**
- Increase `--cycle-interval` (try 24 or 32)

**"Effects cycle too slowly / feel monotonous"**
- Decrease `--cycle-interval` (try 8 or 12)

**"A specific effect appears too often"**
- Lower its weight in `src/dreamsync/effects.py` `MOOD_EFFECTS` dict

---

## Step 5: Color Palette Tuning

### Current Palettes

| Name | Colors | Used in |
|---|---|---|
| warm | amber/orange/gold | CHILL, GROOVE |
| cool | teal/blue/indigo | CHILL |
| pastel | soft pink/blue/green | CHILL |
| ice | blue/white/cyan | CHILL |
| vivid | red/green/blue/orange/purple/cyan | GROOVE, HYPE |
| sunset | coral/pink/purple | GROOVE, HYPE |
| neon | magenta/cyan/yellow | GROOVE, HYPE, DROP |
| fire | red/orange/yellow | HYPE, DROP |

Test each palette in isolation:

```bash
python -m dreamsync govee-live \
  --device 10.126.166.180:7:primary:ptreal \
  --device 10.126.166.156:25:primary:razer \
  --duration 60 \
  --no-auto-cycle \
  --render-mode scroll \
  --colors '#ff4400,#ff8800,#ffcc00,#ff6600,#ffaa00,#ff5500'
```

---

## Step 6: Mic-Specific Tips

Since this system is designed for laptop mic capture (not line-in/loopback):

- **Placement matters**: laptop closer to speakers = more dynamic range = better energy differentiation
- **System volume**: louder playback helps the mic pick up spectral detail (bass, transients)
- **Background noise**: the dynamic normalization handles steady background noise well, but sudden noise spikes (door slam, conversation) can trigger false mood changes
- **Calibration period**: the first 10-15 seconds after starting are for auto-calibration. Energy readings during this time may be unreliable -- this is normal.
- **Multiple sound sources**: if music comes from multiple directions, the mic will blend them. Best results with a single source.

---

## Quick Reference: All Tunable Parameters

### MoodConfig (`src/dreamsync/mood.py`)
| Parameter | Default | Range |
|---|---|---|
| `chill_energy_ceiling` | 0.20 | 0.10 - 0.35 |
| `chill_energy_exit` | 0.28 | `chill_energy_ceiling` + 0.05 - 0.10 |
| `groove_energy_ceiling` | 0.50 | 0.35 - 0.65 |
| `groove_energy_exit_low` | 0.15 | 0.08 - 0.25 |
| `groove_energy_exit_high` | 0.58 | `groove_energy_ceiling` + 0.05 - 0.10 |
| `hype_energy_exit` | 0.40 | 0.30 - 0.50 |
| `stability_threshold` | 0.07 | 0.03 - 0.12 |
| `stability_exit` | 0.09 | `stability_threshold` + 0.01 - 0.04 |
| `min_bpm_for_groove` | 70.0 | 50.0 - 90.0 |
| `drop_energy_spike` | 0.25 | 0.15 - 0.40 |
| `drop_energy_dip` | 0.15 | 0.08 - 0.25 |
| `drop_window` | 0.5 | 0.3 - 1.5 |
| `drop_cooldown` | 10.0 | 5.0 - 20.0 |
| `drop_duration` | 3.0 | 1.5 - 5.0 |
| `min_dwell_seconds` | 4.0 | 2.0 - 10.0 |

### DirectorConfig -- Composite Energy (`src/dreamsync/director.py`)
| Parameter | Default | Range | Notes |
|---|---|---|---|
| `w_rms` | 0.25 | 0.0 - 0.5 | Should sum to 1.0 with other weights |
| `w_spectral_flux` | 0.30 | 0.0 - 0.5 | Strongest punchiness signal |
| `w_bass_ratio` | 0.20 | 0.0 - 0.5 | Genre sensitivity |
| `w_onset_strength` | 0.25 | 0.0 - 0.5 | Percussive transient sensitivity |
| `ema_alpha_spectral_flux` | 0.15 | 0.05 - 0.30 | Higher = more responsive |
| `ema_alpha_bass_ratio` | 0.15 | 0.05 - 0.30 | |
| `ema_alpha_onset_strength` | 0.15 | 0.05 - 0.30 | |
| `rms_floor_alpha` | 0.005 | 0.001 - 0.02 | RMS floor tracking speed |
| `rms_ceil_alpha` | 0.02 | 0.005 - 0.05 | RMS ceiling tracking speed |
| `flux_max_decay` | 0.998 | 0.990 - 0.9999 | Rolling max decay |
| `onset_max_decay` | 0.998 | 0.990 - 0.9999 | Rolling max decay |

### DirectorConfig -- General (`src/dreamsync/director.py`)
| Parameter | Default | Notes |
|---|---|---|
| `ema_alpha_rms` | 0.2 | Higher = more responsive, noisier |
| `ema_alpha_bpm` | 0.12 | Higher = faster BPM tracking |
| `intensity_floor` | 0.15 | Min brightness during quiet parts |
| `intensity_ceiling` | 0.85 | Max brightness |
| `intensity_gamma` | 0.7 | < 1 = brighter overall, > 1 = more dynamic range |

### EffectCyclerConfig (`src/dreamsync/effects.py`)
| Parameter | Default | Range |
|---|---|---|
| `cycle_interval` | 16.0 | 8.0 - 32.0 |

### Render params (per effect preset, `src/dreamsync/effects.py`)
| Param | Used by | Default | Notes |
|---|---|---|---|
| `pulse_decay` | PULSE | 6.0 | Higher = faster decay (shorter flash) |
| `breathe_rate_mult` | BREATHE | 1.0 | 0.5 = half speed, 2.0 = double speed |
| `scroll_inject_width` | SCROLL | 0.2 | Fraction of strip injected on beat |
| `wave_rate_mult` | WAVE | 1.0 | Wave oscillation speed multiplier |
| `wave_wavelength` | WAVE | 1.0 | Spatial wavelength (higher = wider) |
| `gradient_speed` | GRADIENT | 0.1 | Rotation speed |

---

## Iteration Cadence

1. **Session 1:** Baseline run (Step 1). 3-5 songs. Collect feedback. Pay attention to `energy=` range.
2. **Session 2:** Apply threshold/weight fixes based on Session 1. Re-test same songs + 2-3 new ones.
3. **Session 3:** Effect pool/weight adjustments. Palette swaps. Test across genres.
4. **Session 4+:** Fine-tuning. One issue per iteration. Converge on values that work for your setup and music taste.

Each session should take 15-30 minutes of active listening/watching.
