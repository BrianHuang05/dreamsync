# Plan: Stable Beat-Grid Decoder and Subdivision Suppression

## Context

The current event-candidate experiment is useful for finding likely rhythmic
events, but it does not yet model the musical distinction between a beat and a
strong subdivision. It rewards a local candidate when another candidate exists
at a plausible prior interval, then applies only a 240 ms non-maximum
suppression window. Consequently, a persistent eighth-note, sixteenth-note, or
triplet stream can be emitted as a valid double-time beat sequence.

The verified-track overlays make the problem visible:

- `log_mel` spikes and brilliance edges identify useful local events;
- `sub_ratio` corners often add supporting evidence but are not decisive alone;
- detected events can be visually close to the verified grid while still occur
  at the wrong metrical level;
- the current 250 ms evaluation tolerance is appropriate for perceptual
  lighting validation, but it is wide enough that a stable metrical decoder is
  necessary to distinguish quarter-note beats from subdivisions.

This plan replaces independent local tempo support with a stable, adaptive
beat-grid decoder. It preserves the current truth data and keeps difficult
tracks, including *Last Train Home*, as explicitly reported hard cases.

## Goals

1. Produce one musically coherent beat sequence per track rather than a set of
   independent onset candidates.
2. Favor a stable quarter-note pulse and suppress candidates at `T/2`, `T/3`,
   and `T/4` unless a durable tempo/metrical transition is supported.
3. Permit measured tempo changes, metric modulation, and ritards without
   allowing arbitrary beat-to-beat period changes.
4. Incorporate harmonic/tonal novelty alongside transient features for tracks
   where beat salience is not dominated by peaks.
5. Evaluate at 100 ms, 250 ms, and 300 ms tolerance while separately reporting
   false positives, missed beats, and likely metrical-level errors.
6. Make every prediction inspectable in the existing tiled overlay format.

## Non-goals

- Do not edit, snap, or replace verified beat labels.
- Do not remove *Last Train Home* from the dataset or silently exclude it from
  aggregate reporting.
- Do not use held-out tracks to select thresholds, feature families, or model
  hyperparameters after the development protocol is frozen.
- Do not immediately replace the application’s runtime beat system. This plan
  first produces an offline decoder and a reproducible evidence trail.

## Current artifacts and baseline

- Feature cache: `out/beat-ml-analysis/frame_features.csv.gz`
- Verified manifest and split: `out/beat-ml-analysis/verified_dataset_manifest.csv`
  and `out/beat-ml-analysis/track_split.csv`
- Candidate analysis: `scripts/analyze_beat_event_candidates.py`
- Full-song waveform tiles: `scripts/plot_verified_track_waveform_tiles.py`
- Verified/predicted overlays: `scripts/plot_predicted_beat_overlays.py`
- Existing baseline output: `out/beat-band-diagnostics/large-scale-validation/`

The current candidate pipeline already provides a useful first layer:

| Family | Current role | Intended role after this plan |
|---|---|---|
| Pooled `log_mel_26..31` spikes | Precise but sparse local candidates | High-confidence onset evidence |
| Brilliance autocorrelation edges | Repeated-event/periodicity evidence | Phase and periodicity support |
| Sub-ratio corners | Broad local rhythmic changes | Secondary evidence only |
| Cross-signal agreement | Candidate clustering | Observation confidence for the grid decoder |

## Proposed architecture

```text
feature frames
  -> event extractors
       mel-spike, transient-edge, harmonic-novelty, sub-corner
  -> candidate clustering
       timestamp + confidence + contributing families
  -> metrical-grid decoder
       hidden state = beat phase, quarter-note period, tempo slope
       output = one beat per selected grid position
  -> candidate snapping
       snap only to a high-confidence candidate within a bounded local window
  -> evaluation and overlay reports
```

The decoder owns beat placement. Local events may refine a grid beat, but an
event must not create a new beat solely because it is strong.

## Phase 1 — Freeze a clean evaluation protocol

1. Record the current candidate settings and the current full-corpus metrics as
   an immutable baseline.
2. Use only the eight development tracks for feature selection, thresholds,
   and transition penalties.
3. Reserve the two `test` tracks for one final evaluation after each planned
   decoder milestone; do not inspect their overlay images while tuning.
4. Partition reports into three cohorts:

   - development aggregate;
   - held-out aggregate;
   - hard-case aggregate containing *Last Train Home*.

5. Add an explicit `track_role` field to output summaries. Suggested values:
   `development`, `heldout`, `hard_case`.

### Deliverables

- `baseline_metrics.json` copied into each experiment directory.
- `experiment_config.json` containing feature families, thresholds, tempo
  range, tolerance sweep, split identifiers, and source-cache hash.
- A comparison script that refuses to combine results from incompatible source
  cache or split versions.

### Acceptance criteria

- Every result can be traced to one configuration and one split definition.
- Held-out overlays are generated only after the development configuration is
  selected.

## Phase 2 — Improve event observations without deciding meter

### 2.1 Mel-band spike pool

Replace the fixed `log_mel_31` dependence with a local pooled feature over
`log_mel_26..31`.

1. Normalize each band per track with robust median/MAD normalization.
2. Compute per-band local peak prominence and width.
3. Pool only the strongest adjacent-band event in a short neighborhood, rather
   than summing all bands.
4. Emit `mel_spike` observations with:

   - time;
   - prominence;
   - width;
   - winning band;
   - pooled confidence.

This retains the observed strength of narrow `log_mel_31` spikes while making
the detector less dependent on a particular instrument occupying one bin.

### 2.2 Transient and corner observations

1. Emit separate rising-edge and falling-edge observations for
   `brilliance_ratio__autocorr_*`; do not treat both edges of one rolling pulse
   as independent beat evidence.
2. Record the autocorrelation lag and whether an edge is an entry or expiry
   boundary. The expiry boundary should receive lower initial confidence.
3. Use the absolute derivative and second derivative of the sub-ratio feature
   to identify corners, but cap its confidence unless another family agrees.
4. Preserve source-family identifiers through clustering; do not reduce every
   candidate to one unlabelled scalar prematurely.

### 2.3 Harmonic novelty observations

The cache already contains chroma and spectral descriptors. Add a tonal family
before considering a more expensive FFT redesign:

1. L1/cosine novelty between normalized chroma vectors across short and
   medium windows.
2. Change in chroma entropy and dominant chroma class.
3. Spectral-centroid/rolloff change over a longer smoothing window.
4. A harmonic-novelty candidate when tonal change is locally prominent and
   temporally isolated.

Harmonic novelty is especially important for *Last Train Home*, where audible
meter may be carried by harmony and phrasing rather than sharp attacks.

### Tests

- Synthetic spikes in an adjacent mel band produce one pooled event.
- A broad plateau produces lower confidence than a narrow impulse of equal
  peak value.
- One autocorrelation pulse does not create two equally weighted observations.
- Harmonic novelty is invariant to uniform chroma gain.

## Phase 3 — Candidate clustering and metrical-level classification

### 3.1 Cluster local observations

Cluster observations within 80–120 ms into a candidate with:

```python
@dataclass(frozen=True)
class BeatCandidate:
    t: float
    local_confidence: float
    mel_score: float
    transient_score: float
    harmonic_score: float
    corner_score: float
    family_count: int
    source_events: tuple[EventObservation, ...]
```

Use the highest-confidence event time as the candidate time, while retaining
all contributing events for diagnostics.

### 3.2 Classify likely subdivisions

For a provisional period `T`, assign every candidate a nearest-grid residual
and one of:

- `beat` — close to an integer multiple of `T`;
- `eighth` — close to `(n + 1/2) * T`;
- `triplet` — close to `(n + 1/3)` or `(n + 2/3) * T`;
- `sixteenth` — close to quarter offsets;
- `off_grid` — none of the above.

Do not permanently discard subdivisions at this stage. Store their class and
confidence so overlays can explain why an audible event was not emitted as a
beat.

### Tests

- Synthetic 120 BPM quarter/eighth/sixteenth sequences classify correctly
  within one hop.
- A tempo change does not force all candidates after the change into
  `off_grid`.

## Phase 4 — Stable adaptive beat-grid decoder

### 4.1 State representation

Decode a sequence of grid beats with a dynamic-programming or beam-search
state:

```python
@dataclass(frozen=True)
class MeterState:
    beat_time: float
    period_seconds: float
    tempo_slope: float
    cumulative_score: float
    previous_index: int | None
```

Constrain the initial quarter-note period to a practical range, initially
0.30–1.00 seconds. Search this range in coarse bins, then refine around the
best path.

### 4.2 Transition score

For a transition from period `T_prev` to `T_next`, use:

```text
score = observation_score
      - phase_error_penalty
      - tempo_change_penalty * abs(log(T_next / T_prev))
      - subdivision_penalty
      - missing-observation_penalty
```

Rules:

1. Small gradual period change is allowed every beat.
2. Large changes require several consecutive high-confidence observations
   before the new tempo becomes active.
3. A temporary `T/2` or `2T` path must beat the current path by a material
   margin for multiple beats before an octave transition is accepted.
4. An onset near a predicted grid point may snap the beat within a bounded
   window, initially ±100 ms; otherwise retain the predicted grid time.
5. A missing local observation does not remove a beat. Emit a lower-confidence
   grid beat to maintain musical continuity.

### 4.3 Metric modulation and ritard handling

1. Permit a larger tempo-slope transition only after a sustained evidence
   window, for example 3–6 beats.
2. Expose a `transition_confidence` in the decoded output so visually obvious
   tempo changes can be inspected.
3. Treat end-of-track slowdown as a continuous tempo-slope problem, not a
   sequence of independent missed peaks.
4. Add a configurable grace period at track start/end to avoid overfitting
   analysis-window edges.

### Decoder outputs

- `decoded_beats.csv`: time, period, BPM, local confidence, grid confidence,
  candidate-snapped flag, and subdivision class of the nearest unused event.
- `decoder_path.json`: state/transition summary for reproducibility.
- `tempo_curve.csv`: per-beat period and BPM for plotting.

### Tests

- Stable quarter-note grid with interleaved eighth notes emits quarter notes
  only.
- Sustained true double-tempo change is accepted after the configured evidence
  span.
- Isolated double-time candidates do not cause a tempo octave flip.
- Linear ritard synthetic data produces monotonic period growth with bounded
  phase error.
- Missing onsets preserve a grid beat rather than shifting phase to the next
  subdivision.

## Phase 5 — Calibration and reporting

### 5.1 Metrics

Evaluate each method and the final decoder at 100, 250, and 300 ms.

Report:

- precision, recall, F1;
- matched timing MAE;
- false positives/minute;
- false negatives/minute;
- candidate-to-beat snap distribution;
- beat-period variance and tempo-slope distribution;
- tempo-octave transitions;
- predicted events classified as eighth, triplet, or sixteenth relative to the
  final grid.

### 5.2 Segment metrics

Produce metrics in consecutive 30-second windows and flag windows with:

- low grid confidence;
- high subdivision density;
- rapid tempo change;
- low harmonic/transient agreement.

This should reveal whether a detector succeeds only in particular song
sections, such as the stronger second half of *If Only for Tonight*.

### 5.3 Overlay updates

Extend `plot_predicted_beat_overlays.py` with an optional grid-decoder mode:

- verified beat: solid black;
- decoder beat matched within selected tolerance: green dashed;
- decoder beat unmatched: red dashed;
- suppressed subdivision candidate: thin purple dotted;
- candidate snapped to grid: optional blue marker;
- low-confidence grid beat: optional gray dashed line.

Keep the current raw-candidate overlay mode for comparison. Never overwrite
prior experiment outputs; use a unique experiment directory per configuration.

## Phase 6 — Hard-case policy for *Last Train Home*

1. Keep the track in the verified manifest and final report.
2. Label it `hard_case` for tuning dashboards.
3. Exclude it only from an explicitly named core-development aggregate if it
   would otherwise dominate threshold selection.
4. Always publish both results:

   - core-development/held-out metrics;
   - metrics including hard cases.

5. Use it to decide whether harmonic novelty and longer temporal context add
   value. Failure on this track is an expected diagnostic, not justification
   for deleting it.

## Implementation sequence

1. Add typed observation/candidate models and unit tests.
2. Refactor the current candidate script so feature extraction is reusable and
   preserves family-level evidence.
3. Add pooled-mel and harmonic-novelty extractors; compare them to the frozen
   baseline on development tracks.
4. Implement subdivision classification against a provisional grid.
5. Implement the stable dynamic-programming/beam-search grid decoder with
   synthetic tests before using real tracks.
6. Tune transition and subdivision penalties only on development tracks.
7. Generate development overlays, inspect failure categories, and lock the
   configuration.
8. Run the held-out evaluation once and generate held-out overlays.
9. Render predicted-timeline lighting shows for perceptual comparison with
   truth-driven shows.

## Acceptance criteria

The decoder is ready for an offline lighting-show trial when all of the
following are true:

1. It improves or matches the frozen baseline’s 250 ms F1 while materially
   lowering false positives/minute on development tracks.
2. It reduces visually obvious double-time and subdivision detections in the
   overlay tiles.
3. It maintains a coherent phase through missing local peaks.
4. It handles at least one known tempo change/ritard without a tempo-octave
   flip.
5. Held-out performance is reported separately and does not collapse relative
   to development performance.
6. The hard-case report remains visible, whether or not *Last Train Home*
   meets the core threshold.
7. Predicted-timeline lighting shows are judged perceptually acceptable at the
   selected error margin.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Decoder locks to half/double tempo early | Multi-hypothesis initialization; sustained-evidence requirement for octave transitions |
| A wide evaluation margin masks poor meter | Report subdivision and tempo-octave errors separately from tolerant event matches |
| Tonal features add noise to percussive music | Keep family scores separate and let the decoder downweight unsupported harmonic evidence |
| Visual inspection leaks held-out information | Generate/tune development overlays first; reserve held-out overlays until configuration lock |
| Hard case dominates aggregate metrics | Publish core and hard-case aggregates side by side; do not delete the track |

