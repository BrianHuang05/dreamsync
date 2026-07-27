# Plan: Structure-First Reactive Similarity and Bar-Locked Lighting

## Status

**Proposed - implementation-ready, reactive live path only.**

This plan replaces the chord-centered core of:

- `dev/plans/live-harmonic-structure-and-true-downbeats.md`; and
- `dev/plans/live-predictive-music-structure-lighting-plan.md`

for the purpose of phrase and section detection in Reactive mode.

It does **not** remove chord or key analysis as a diagnostic capability. It
removes accurate chord, key, and Roman-numeral recognition as prerequisites
for structural prediction or lighting control.

The existing optional corpus/neural stage remains blocked until the
structure-first baseline in this document has been measured on deterministic
replays and real songs.

## Product Objective

The product objective is:

```text
live audio
  -> reliable beat and downbeat clock
  -> octave-compressed harmonic, rhythmic, timbral, and dynamic similarity
  -> song-local phrase and anonymous-section memory
  -> predicted structural bar markers
  -> small, medium, and large visual actions committed on downbeats
```

The objective is **not**:

- exact chord transcription;
- complete tonal analysis;
- definitive verse/chorus naming;
- firing a visual action for every chord change;
- allowing a prediction to change an effect at an arbitrary render frame.

The desired behavior is:

1. continuous effects remain visually alive between bar markers;
2. discrete effect, palette, and structural-envelope actions occur only on
   confident downbeats;
3. ordinary bar markers may receive small actions;
4. likely phrase boundaries may receive medium actions;
5. high-confidence section entrances, returns, or major transitions may
   receive large actions;
6. the system becomes more predictive after hearing repeated material in the
   current song;
7. the system abstains or degrades safely when the meter or structure is
   ambiguous.

## Why This Pivot Is Required

Reactive mode currently contains useful infrastructure but the structural
identity path remains centered on recognized chords:

- `LiveStructureTracker` begins macro-boundary consideration only from a
  recognized `harmonic_change`;
- its compatibility path considers fixed `bars_per_phrase` positions;
- `PhraseRecurrenceMemory` gives exact Roman-numeral equality most of its
  recurrence score;
- `SectionOrderMemory` also gives exact Roman-numeral equality most of its
  section-similarity score;
- the predictive engine's phrase and section memories are completed only
  after the older harmonic tracker emits `macro_change`;
- the displayed cycle position can be a modulo projection of a leading
  phrase-length prior rather than a confirmed recurring cycle;
- committed predictive cue effect names are exposed in runtime parameters but
  are not applied as renderer/effect changes;
- mood, drop, harmonic-accent, beat-pulse, and legacy macro paths can still
  cause visible behavior outside the desired bar-locked structural policy.

This creates a circular dependency:

```text
correct chord
  -> correct function
  -> correct recurrence
  -> correct macro boundary
  -> populate phrase/section memory
  -> predict the next boundary
```

One incorrect chord can therefore prevent or distort the higher-level result.
The replacement path uses audio similarity and recurrence directly:

```text
bar fingerprint
  -> similarity to earlier bars and phrases
  -> anonymous section identity and duration
  -> likely successor and boundary
```

Chord and key evidence may improve a confidence score later, but cannot gate
the structural path.

## Scope

### In scope

- Reactive live mode;
- capture-only audio input;
- existing bounded audio ingest and render schedules;
- existing beat and downbeat tracking;
- beat-synchronous aggregation into completed-bar fingerprints;
- octave-compressed chroma without mandatory chord classification;
- live timbre, rhythm, band-energy, bass, onset, and dynamics descriptors;
- bounded song-local self-similarity and recurrence memory;
- absolute and transposition-invariant harmonic similarity;
- competing phrase-length hypotheses;
- anonymous section identities such as A, B, and C;
- observed-boundary evidence and future-boundary predictions;
- phrase and section recurrence;
- section-duration and section-order prediction;
- bar-indexed cue scheduling;
- effect and palette actuation through one explicit structural-action seam;
- simulation, telemetry, replay, and GUI diagnostics;
- migration and compatibility controls for the current harmonic path.

### Out of scope

- offline/precompiled show analysis changes;
- compiled `Show` cue schemas;
- saved show timelines;
- audio playback, monitoring, or loopback;
- cloud inference;
- required GPU inference;
- automatic spatial-layout changes;
- exact semantic labeling of every section;
- guaranteed prediction of a song's first truly novel transition;
- an end-to-end audio-to-light model;
- Stage H corpus or neural training before the deterministic baseline passes.

## Non-Negotiable Runtime Rules

### 1. Remain causal

Every prediction must use only audio whose timestamp is at or before the
prediction timestamp.

No replay helper may expose a future bar, future boundary, complete-song
self-similarity matrix, or offline section label to the live predictor.

### 2. The downbeat clock owns discrete actuation

Every discrete structural visual action must carry:

- a target beat index;
- a target bar index;
- a target downbeat timestamp;
- timing confidence;
- structure confidence;
- an explicit action class.

The action may commit only when the matching confident downbeat is consumed by
the live render path.

Wall-clock proximity alone is not sufficient.

### 3. Continuous animation is not a structural cue

An already active effect may continue updating on every render frame.

In structure-controlled mode, the following are discrete actions and therefore
bar-locked:

- changing render/effect mode;
- changing the active effect preset;
- changing or advancing a palette;
- resetting a wave/ripple/scroll phase;
- starting a flash, bloom, blast, or transition envelope;
- changing effect bank;
- changing a structural intensity envelope.

Secondary-beat motion may modulate an already active animation only if the
profile explicitly enables it. It must not select, restart, or reconfigure the
effect. The structure-first default is no discrete secondary-beat action.

### 4. Structure first, labels later

The primary section representation is an anonymous song-local identity.

`A`, `B`, and `C` are valid results. `verse`, `chorus`, `bridge`, or `drop`
may appear only as optional semantic hypotheses with separate probabilities.

### 5. Tonal analysis is optional evidence

The structural system must work when all of these are unavailable:

- chord label;
- chord quality;
- key center;
- Roman numeral;
- cadence class.

Raw octave-compressed chroma remains useful and is not removed.

### 6. Song-local evidence dominates after recurrence

Before repetition is observed, duration regularity and trajectory evidence
provide conservative cold-start predictions.

After a phrase or section repeats, direct similarity to the current song must
outweigh generic four- or eight-bar priors.

### 7. Fail closed for large effects

Low confidence may allow:

- continuous rendering;
- a restrained ordinary bar marker;
- diagnostics;
- learning and memory updates.

Low confidence must not produce a large transition.

## Current Assets to Reuse

The implementation must reuse the existing live stack rather than creating a
parallel analyzer.

### Audio and timing

- bounded callback-to-worker ingest in `src/dreamsync/live.py`;
- `LiveBpmEstimator`;
- `LiveMeterTracker`;
- confident beat/downbeat publication;
- render and GUI state schedules;
- discontinuity and song-boundary resets.

### Harmonic evidence

- octave-compressed chroma extraction in
  `src/dreamsync/dsp/harmonic.py`;
- harmonic-partial attenuation already performed before chroma folding;
- harmonic novelty and tonal-confidence diagnostics.

Chord template matching may remain inside `LiveHarmonicAnalyzer` during the
first migration phase, but its output must not drive the new structure engine.
A later phase may split raw chroma extraction from chord classification.

### Timbre and spectral evidence

- `SpectralFeatures` in `src/dreamsync/live.py`;
- band energies, ratios, and fluxes;
- bass and kick energy;
- spectral flux;
- centroid;
- RMS/energy;
- onset strength;
- live EQ state;
- existing numpy-only mel filterbank, DCT, MFCC, and chroma helpers in
  `src/dreamsync/analyzer/features.py`.

Common DSP helpers should be extracted to a neutral `dreamsync.dsp` module.
Reactive code must not import the offline analyzer pipeline as a runtime
dependency.

### Predictive and visual infrastructure

- `LiveMusicalObservation`;
- calibration and arbitration contracts;
- `PredictedMusicalEvent`;
- `CueProposal`;
- `PredictiveCuePolicy` state transitions;
- effect-bank enablement;
- brightness caps and cooldowns;
- simulation/null output;
- runtime snapshots and GUI diagnostics.

These components require contract changes and rewiring, not wholesale removal.

## Target Runtime Architecture

```text
capture-only audio input
  -> bounded live ingest
  -> existing frame analysis
       -> beat/BPM evidence
       -> downbeat phase evidence
       -> chroma evidence
       -> timbre/band evidence
       -> rhythm/onset evidence
       -> dynamics evidence
  -> beat-synchronous feature accumulator
  -> completed LiveBarFingerprint on each confident downbeat
  -> bounded MultiFeatureSimilarityMemory
       -> absolute harmonic similarity
       -> transposition-invariant harmonic similarity
       -> timbre similarity
       -> rhythm similarity
       -> dynamics/trajectory similarity
       -> sequence/prefix similarity
  -> StructureEvidenceFusion
       -> repetition
       -> homogeneity
       -> novelty
       -> regularity
       -> entrance/exit trajectory
  -> OnlineStructureTracker
       -> phrase-length hypotheses
       -> observed boundary hypotheses
       -> anonymous section identities
       -> section duration/order memory
       -> future boundary probabilities
  -> calibration and arbitration
  -> BarLockedCuePolicy
       -> proposed
       -> armed
       -> scheduled for target bar/downbeat
       -> committed only on matching downbeat
       -> confirmed/cancelled/degraded
  -> StructuralVisualActuator
       -> apply enabled effect
       -> apply palette action
       -> reset/transition envelope
       -> preserve spatial configuration
  -> existing renderer and adapters
```

## Runtime Data Contracts

### Beat-level observation

The existing `LiveMusicalObservation` should evolve away from requiring a
symbolic harmonic answer.

Proposed contract:

```python
@dataclass(frozen=True)
class LiveBeatStructureObservation:
    t: float
    beat_index: int
    bar_index: int | None
    beat_in_bar: int | None
    meter: tuple[int, int] | None
    meter_confidence: float
    downbeat: bool
    beat_period: float | None

    chroma: tuple[float, ...]              # 12
    chroma_confidence: float
    harmonic_novelty: float

    mfcc: tuple[float, ...]                # 13, or configured compact form
    band_ratios: tuple[float, ...]
    band_fluxes: tuple[float, ...]
    bass_ratio: float
    energy: float
    onset_strength: float
    spectral_centroid: float

    chord_label: str | None = None         # diagnostics only
    chord_confidence: float = 0.0
    key_label: str | None = None           # diagnostics only
    key_confidence: float = 0.0
```

Rules:

- the predictor must behave identically if all optional label fields are
  cleared;
- all fixed-size vectors must be validated;
- non-finite inputs must be rejected or replaced before aggregation;
- low-confidence chroma must reduce only the harmonic group weight, not erase
  rhythm/timbre/dynamics evidence.

### Completed bar fingerprint

Create a structure-specific immutable summary:

```python
@dataclass(frozen=True)
class LiveBarFingerprint:
    bar_index: int
    start_t: float
    end_t: float
    beats: int
    meter: tuple[int, int] | None
    meter_confidence: float
    completeness: float

    beat_chroma: tuple[tuple[float, ...], ...]
    chroma_profile: tuple[float, ...]
    chroma_delta_shape: tuple[float, ...]
    chroma_confidence: float

    mfcc_mean: tuple[float, ...]
    mfcc_std: tuple[float, ...]
    band_profile: tuple[float, ...]
    band_flux_shape: tuple[float, ...]

    onset_shape: tuple[float, ...]
    energy_shape: tuple[float, ...]
    bass_shape: tuple[float, ...]
    centroid_shape: tuple[float, ...]

    tonal_label: str | None = None
```

`tonal_label` is informational and excluded from primary similarity.

Do not reduce a bar to median chroma alone. The beat-wise sequence must remain
available so two bars with the same pitch-class average but different harmonic
motion are distinguishable.

### Similarity result

```python
@dataclass(frozen=True)
class BarSimilarity:
    left_bar: int
    right_bar: int
    combined: float
    harmonic_absolute: float
    harmonic_transposed: float
    best_pitch_shift: int
    timbre: float
    rhythm: float
    dynamics: float
    reliability: float
```

### Structural boundary evidence

```python
@dataclass(frozen=True)
class BoundaryEvidence:
    target_bar: int
    observed_probability: float
    predicted_probability: float
    phrase_probability: float
    section_probability: float
    recurrence_probability: float
    novelty_probability: float
    homogeneity_probability: float
    regularity_probability: float
    trajectory_probability: float
    cold_start: bool
    evidence: tuple[PredictionEvidence, ...]
```

Observed and predicted probabilities must remain distinct:

- `observed_probability` describes a boundary supported by audio already
  heard;
- `predicted_probability` describes an upcoming boundary.

An observed boundary discovered after its marker may update memory and future
predictions, but may not be backdated into an optical action.

### Anonymous section state

```python
@dataclass(frozen=True)
class LiveSectionHypothesis:
    section_id: str
    start_bar: int
    expected_end_bar: int | None
    duration_distribution: tuple[tuple[int, float], ...]
    prefix_match: float
    recurrence_count: int
    likely_successors: tuple[tuple[str, float], ...]
    probability: float
    semantic_role: str | None = None
    semantic_probability: float = 0.0
```

### Bar-locked cue target

Extend prediction and cue contracts with musical identity:

```python
@dataclass(frozen=True)
class StructuralCueTarget:
    target_beat_index: int
    target_bar_index: int
    target_t: float
    requires_downbeat: bool
    timing_sigma: float
```

Do not deduplicate or commit structural cues by rounded seconds alone.

## Bar Fingerprint Construction

### Beat alignment

Accumulate one structure observation for each detected beat.

On a new confident downbeat:

1. close the previous bar;
2. build its fingerprint;
3. publish the fingerprint once;
4. start the new bar accumulator;
5. update similarity and structure memory outside the audio callback.

If meter confidence is temporarily lost:

- retain observations in a provisional accumulator;
- do not invent a completed bar;
- either reconcile after phase recovery or mark the bar incomplete;
- reduce fingerprint reliability;
- prohibit high-impact cues.

### Normalization

Normalize each feature family independently.

- Chroma: non-negative L1 normalization with confidence/entropy diagnostics.
- MFCC: song-local robust standardization using bounded median/MAD history.
- Band ratios: already scale-normalized, then robust clip.
- Flux/onset: log compression followed by bounded song-local normalization.
- Energy: log RMS and relative shape within the bar.
- Centroid: normalize by Nyquist or use relative delta.

Never concatenate unnormalized MFCC, chroma, energy, and centroid values and
apply a single cosine distance.

### Meter variability

Fingerprint comparisons must support at least configured 3-, 4-, and 6-beat
bars.

For bars of equal meter, compare beat positions directly.

For differing or uncertain meters:

- compare pooled group summaries;
- optionally resample per-beat shapes to a small normalized phase grid;
- reduce reliability;
- never silently claim the same meter.

Meter-length detection itself is a later enhancement. The first implementation
may retain configured `beats_per_bar`, but the GUI must call it configured or
assumed meter rather than detected time signature.

## Multi-Feature Similarity

### Bounded memory

Create `MultiFeatureSimilarityMemory` with:

- default maximum of 256 completed bars;
- stable bar IDs;
- fingerprints;
- only the required recent similarity rows;
- top recurrence matches per bar/sequence;
- no complete-song recomputation.

At 120 BPM in 4/4, 256 bars covers approximately 8.5 minutes.

### Harmonic similarity

Compute both absolute and transposition-invariant similarity.

#### Absolute

Compare:

- beat-wise chroma sequences;
- bar chroma profiles;
- chroma-delta shapes.

Use cosine similarity or another bounded distance after normalization.

#### Transposition invariant

For shifts `0..11`, circularly rotate the entire candidate bar or phrase by
one shared pitch-class shift and choose the best similarity.

The shift must be consistent across the compared sequence. Do not choose a
different transposition for every beat.

Retain:

- best score;
- best pitch shift;
- gap between best and runner-up shifts.

A weak or ambiguous shift reduces reliability.

#### Why both are required

- absolute similarity recognizes an exact harmonic return;
- transposition-invariant similarity recognizes the same pattern in another
  key;
- disagreement between them can identify modulation or a varied return;
- using only transposition-invariant similarity would hide some meaningful
  harmonic transitions.

### Timbre similarity

Use:

- MFCC mean and variance;
- band-energy profile;
- optionally spectral centroid shape.

Timbre similarity should be able to recognize instrumentation changes even
when the chords remain unchanged.

### Rhythm similarity

Use:

- onset shape;
- band-flux shape;
- bass/kick shape;
- harmonic-change-rate shape derived from chroma deltas, not chord commits.

### Dynamics similarity

Use:

- relative energy contour;
- bass contour;
- centroid/brightness contour;
- entrance and exit slopes.

Dynamics must have a deliberately lower identity weight than harmonic/timbre
similarity so a louder repeat can still match its earlier section.

### Initial group weights

Initial weights are hypotheses to be calibrated:

```text
harmonic absolute          0.20
harmonic transposed        0.20
timbre                     0.25
rhythm                     0.20
dynamics                   0.15
```

Reliability-normalize the active weights. If chroma is unreliable, redistribute
its weight among timbre, rhythm, and dynamics rather than forcing a low total
score.

All weights must be logged and configurable for replay experiments.

## Structure Evidence

The structure tracker must combine four primary principles:

### 1. Repetition

Does the current bar or multi-bar prefix match an earlier sequence?

Required horizons:

- 1 bar;
- 2 bars;
- 4 bars;
- 8 bars;
- 12 bars;
- 16 bars where history permits.

Use sequence alignment over `BarSimilarity` results. Allow:

- one weak/missing bar;
- small timing variation;
- dynamics variation;
- one consistent pitch transposition;
- confidence-weighted substitutions.

### 2. Homogeneity

Is the recent run of bars internally coherent, and would a boundary create
two more coherent groups than one?

Use rolling within-window and cross-window similarity. Do not require the
future side of an offline checkerboard kernel. In the causal implementation:

- post-boundary evidence may confirm a boundary after new material is heard;
- pre-boundary prediction comes from recurrence, duration, order, and exit
  trajectory.

### 3. Novelty

Novelty is a change relative to recent song-local context, not a chord-label
change.

Compute separate novelty channels:

- harmonic;
- timbral;
- rhythmic;
- dynamics.

A section boundary may be supported by any strong channel or several moderate
channels. A quiet chorus or same-chord instrumentation change must remain
detectable.

### 4. Regularity

Maintain soft duration distributions for:

- phrase lengths;
- anonymous section lengths;
- successor timing.

Priors may favor 2/4/8/12/16 bars but cannot create a boundary by modulo alone.

Passing an expected length weakens the hypothesis gradually rather than
resetting or forcing it.

### Supporting trajectory evidence

Retain:

- energy slope;
- onset-density slope;
- bass buildup/drop;
- brightness widening/narrowing;
- silence or near-silence;
- exit stability;
- fill-like rhythm increase.

Trajectory is supporting evidence. It may not independently identify a
chorus.

## Causal Boundary Strategy

### First occurrence

A genuinely novel first section entrance cannot always be known in advance.

The system may:

- predict conservatively from phrase duration and buildup evidence;
- arm a medium action when confidence is sufficient;
- abstain from a large action;
- detect the new section after hearing its prefix;
- store the boundary and section identity for future recurrence.

Do not falsify a successful prediction by backdating a post-boundary
detection.

### Repeated occurrence

After observing a section:

1. match the current section prefix to its earlier occurrence;
2. retrieve its learned duration;
3. retrieve likely successor sections;
4. estimate the target downbeat one or more bars ahead;
5. raise confidence as additional prefix bars match;
6. schedule an action before the target downbeat;
7. commit only on the matching target downbeat.

This is the primary path for large, well-timed effects.

### Boundary confirmation

When post-boundary evidence confirms a transition:

- close the preceding phrase/section;
- reset the phrase-start state;
- update duration distributions;
- assign or merge an anonymous section identity;
- update the section transition graph;
- record prediction outcome;
- do not fire a late large transition for the missed marker.

The current `boundary_observed` seam in `CompetingPhraseTracker` must be wired
to real observed boundary evidence.

## Phrase and Section Memory

### Phrase memory

Replace numeral-first `PhraseFingerprint` identity with a sequence of bar
fingerprint references or compact multi-feature sequence descriptors.

Suggested contract:

```python
@dataclass(frozen=True)
class StructurePhraseFingerprint:
    bars: int
    bar_descriptors: tuple[CompactBarDescriptor, ...]
    combined_signature: tuple[float, ...]
    entrance_signature: tuple[float, ...]
    exit_signature: tuple[float, ...]
    harmonic_shift_tolerance: bool
    energy_shape: tuple[int, ...]
    onset_shape: tuple[int, ...]
```

Roman numerals may be attached as diagnostics but must not participate in the
default match score.

### Section memory

Section fingerprints aggregate:

- phrase/bar sequence;
- duration distribution;
- entrance signature;
- exit signature;
- energy/onset trajectory;
- recurrence count;
- preceding and following anonymous section IDs;
- visual motif previously used.

Section matching should use robust multi-feature sequence similarity.

### Transition graph

The existing section-order graph must become an active prediction source.

If observed order is:

```text
A -> B -> A -> B
```

and the current prefix strongly matches A, the predictor should combine:

- A's learned duration;
- `A -> B` transition probability;
- current position within A;
- exit trajectory;
- target downbeat confidence.

`predict_successor()` must feed the unified predicted-event path rather than
remaining an unused method.

### Memory merge and split

Do not permanently freeze early A/B assignments.

- Merge identities after repeated high-similarity evidence.
- Split an identity when it contains two stable, mutually dissimilar clusters.
- Keep all operations bounded.
- Log merges and splits.
- Preserve transition counts when identities merge.

## Prediction Outputs

The new primary event types are:

```text
bar_marker
phrase_boundary
section_entrance
section_repeat
section_transition
build_release
```

Chord events may remain in diagnostics but are not enabled visual cue classes
by default.

Each prediction must include:

- target beat/bar identity;
- target time and uncertainty;
- probability;
- cold-start or song-local source;
- evidence-family contributions;
- matched earlier section/phrase, if any;
- expected section duration;
- successor alternatives;
- abstention reason when no event is emitted.

## Bar-Locked Visual Policy

### Action hierarchy

#### Ordinary bar marker

Examples:

- restrained intensity breathing point;
- subtle wave-phase alignment;
- small color interpolation step;
- no effect or palette replacement.

#### Phrase boundary

Examples:

- medium wave/ripple reset;
- controlled palette movement;
- moderate transition envelope;
- keep the same effect family unless the profile allows a phrase-level change.

#### Section entrance or return

Examples:

- apply a different enabled effect preset;
- change effect family;
- advance or recall an approved palette;
- trigger a larger but brightness-capped envelope;
- recall the visual motif previously associated with that anonymous section.

### Explicit actuator seam

Add an explicit API, for example:

```python
effect_cycler.apply_structural_action(
    cue_class=cue.cue_class,
    effect_name=selected_effect,
    color_action=cue.color_action,
    target_bar=cue.target.target_bar_index,
    now_t=stream_t,
)
```

The exact API may differ, but runtime metadata is not actuation.

The actuator must:

- verify the effect remains enabled;
- apply the chosen effect rather than merely serialize its name;
- implement every supported color action;
- preserve spatial settings;
- preserve brightness caps;
- return an applied/degraded/rejected outcome;
- publish the actual selected effect and palette;
- remain deterministic under replay when given a seed.

### Downbeat commit

Change cue commitment from:

```text
current time is within N milliseconds of target time
```

to:

```text
the consumed beat is a confident downbeat
and its beat/bar identity matches the cue target
and the cue remains scheduled
```

A small timestamp tolerance may validate the clock, but may not substitute for
the downbeat identity.

### Structural mode restrictions

When structure-controlled lighting is active:

- disable time-interval effect replacement;
- prevent mood-state changes from immediately replacing effects;
- prevent DROP entry/exit from immediately replacing effects;
- disable chord-change accents by default;
- disable chord-resolution visual actions by default;
- prevent secondary beats from restarting or selecting effects;
- allow mood, drop, chord, and momentum evidence to influence the next
  bar-locked action choice;
- preserve a master bypass that restores ordinary Reactive behavior.

This converts mood and drop from actuators into evidence and selection context.

## GUI and Diagnostics

### Operator-facing structure panel

Replace chord-cycle authority with a structure panel showing:

- configured/assumed meter;
- meter confidence;
- current bar index;
- leading phrase length alternatives;
- current anonymous section hypothesis;
- matched earlier section and match confidence;
- expected bars to next boundary;
- beats to target downbeat;
- boundary probability;
- cue state;
- target bar/downbeat;
- selected effect/palette action;
- applied/degraded/rejected result.

### Similarity diagnostics

Provide bounded, inspectable diagnostics:

- last N bars, initially 32;
- top three similar earlier bars or phrase prefixes;
- absolute harmonic similarity;
- transposed harmonic similarity and pitch shift;
- timbre similarity;
- rhythm similarity;
- dynamics similarity;
- combined score and reliability;
- novelty channels;
- phrase/section boundary probability.

A full interactive matrix is optional. Compact rows and a small heatmap are
sufficient for initial validation.

### Chord diagnostics

Keep the FFT/chord wheel behind its existing debug option.

Label it explicitly as optional tonal diagnostics. Its label must not imply
that section prediction depends on the recognized chord.

### Terminology corrections

Until meter length is inferred, display:

```text
Configured Meter: 4/4
```

not:

```text
Detected Time Signature: 4/4
```

Until recurrence is confirmed, display:

```text
Leading phrase hypothesis: 4 bars (0.46)
```

not:

```text
Cycle Length: 4 bars
```

## Configuration and Rollout Controls

Add or evolve reactive settings:

```python
structure_similarity_enabled: bool = False
structure_similarity_diagnostics: bool = False
structure_similarity_shadow_mode: bool = True
structure_bar_actions_enabled: bool = False
structure_phrase_actions_enabled: bool = False
structure_section_actions_enabled: bool = False
structure_allow_secondary_beat_modulation: bool = False
structure_use_tonal_sidecar: bool = False
structure_memory_bars: int = 256
structure_min_meter_confidence: float = 0.22
structure_phrase_threshold: float = ...
structure_section_threshold: float = ...
structure_large_action_threshold: float = ...
```

Compatibility controls:

- retain `harmonic_structure_enabled` during shadow comparison;
- do not allow both old macro actuation and new structure actuation
  simultaneously;
- log which engine owns the action;
- provide a single GUI choice:
  - ordinary reactive;
  - legacy harmonic structure;
  - structure similarity shadow;
  - structure similarity active.

After cutover, remove the legacy active option while retaining tonal
diagnostics.

## Telemetry and Explainability

Log one bounded record per completed bar:

```json
{
  "kind": "structure_bar",
  "bar_index": 24,
  "meter_confidence": 0.82,
  "fingerprint_reliability": 0.76,
  "top_matches": [
    {"bar": 8, "combined": 0.88, "pitch_shift": 0}
  ],
  "novelty": {
    "harmonic": 0.18,
    "timbre": 0.42,
    "rhythm": 0.21,
    "dynamics": 0.30
  },
  "phrase_boundary_probability": 0.61,
  "section_boundary_probability": 0.48
}
```

Log every cue lifecycle transition:

- proposal;
- arm;
- schedule;
- cancel;
- commit;
- applied;
- degraded;
- rejected;
- confirmation/outcome.

Each applied action must answer:

- Why this bar?
- Which earlier material matched?
- Was the prediction cold-start or song-local?
- Which evidence families contributed?
- Which effect was requested?
- Which effect was actually applied?
- Was the commit tied to the expected downbeat?

## Performance and Resource Budgets

### Audio callback

- no fingerprint aggregation;
- no similarity comparison;
- no prediction;
- no allocation beyond existing callback requirements;
- callback p99 must not regress.

### Beat update

- p95 below 1 ms;
- bounded feature accumulation;
- no history-wide scan.

### Completed-bar update

- p95 below 5 ms for 256-bar memory;
- p99 below 10 ms;
- no render starvation;
- no audio-ring lag increase beyond existing limits.

### Memory

- maximum 256 full bar fingerprints by default;
- maximum 64 phrase fingerprints;
- maximum 24 section observations;
- maximum 12 anonymous identities;
- bounded telemetry queues;
- no session-length list growth.

### Rendering

- output cadence unchanged;
- structural action applied atomically before rendering the matching downbeat
  frame;
- no extra network send solely for diagnostics.

## Implementation Phases

### Phase 0 - Freeze baseline and add audit tests

#### Goal

Capture the current behavior and expose the known gaps before changing it.

#### Work

1. Add an integration test proving that a predictive cue's selected effect is
   currently not applied.
2. Add a test showing legacy macro confirmation occurs after the downbeat.
3. Add a test showing section recurrence depends on numeral equality.
4. Add a test showing the cycle readout can exist without confirmed
   recurrence.
5. Add a test enumerating non-bar-locked effect changes in structure mode.
6. Record deterministic replay baselines for:
   - clear A/B pop form;
   - same chords with instrumentation change;
   - changed chords with same section identity;
   - quiet chorus;
   - transposed return;
   - drum-only passage.

#### Acceptance

- baseline tests accurately describe current behavior;
- no production behavior changes;
- replay fixtures contain only causal observations;
- expected failures are documented rather than hidden.

### Phase A - Separate raw structure evidence from tonal labels

#### Goal

Make chord/key output optional to the predictive observation.

#### Work

1. Add `LiveBeatStructureObservation`.
2. Populate chroma and spectral evidence without requiring a chord commit.
3. Move or extract reusable MFCC/mel helpers into `src/dreamsync/dsp`.
4. Compute live MFCC at a bounded cadence or derive it from the existing
   spectrum without duplicating FFT work.
5. Preserve chord/key fields as optional diagnostics.
6. Add a test that clears every symbolic tonal field and produces identical
   structure features.
7. Keep old prediction behavior behind compatibility wiring.

#### Suggested files

- add `src/dreamsync/prediction/structure_models.py`;
- add or modify `src/dreamsync/dsp/spectral_features.py`;
- modify `src/dreamsync/dsp/harmonic.py`;
- modify `src/dreamsync/prediction/runtime.py`;
- modify `src/dreamsync/live.py`;
- add `dev/tests/test_live_structure_observation.py`.

#### Acceptance

- raw structural observation is valid with no chord/key label;
- no second FFT is introduced unnecessarily;
- callback and frame-analysis budgets pass;
- capture-only guarantees remain intact.

### Phase B - Build reliable bar fingerprints

#### Goal

Produce one immutable, confidence-bearing fingerprint per completed bar.

#### Work

1. Implement `LiveBarFingerprintBuilder`.
2. Accumulate beat-aligned chroma, MFCC, band, onset, bass, energy, and
   centroid evidence.
3. Preserve beat-wise shapes.
4. Implement feature-family normalization.
5. Handle missing beats and meter-confidence loss.
6. Emit exactly one completed fingerprint at a downbeat.
7. Reset safely on discontinuity and detected song boundary.
8. Publish compact diagnostics.

#### Suggested files

- add `src/dreamsync/prediction/bar_features.py`;
- modify `src/dreamsync/prediction/models.py`;
- modify `src/dreamsync/prediction/runtime.py`;
- add `dev/tests/test_live_bar_fingerprints.py`.

#### Acceptance

- duplicate calls cannot close the same bar twice;
- incomplete bars are reliability-gated;
- same musical bar at different loudness remains similar;
- reversed harmonic motion does not collapse to the same fingerprint;
- no chord label is required.

### Phase C - Implement bounded multi-feature similarity

#### Goal

Recognize recurring bars and sequences without chord names.

#### Work

1. Implement `MultiFeatureSimilarityMemory`.
2. Implement absolute harmonic similarity.
3. Implement one-shift-per-sequence transposition-invariant similarity.
4. Implement timbre, rhythm, and dynamics similarity.
5. Implement reliability-normalized group weights.
6. Retain top matches at 1/2/4/8/12/16-bar horizons.
7. Add robust missing/substituted-bar tolerance.
8. Add diagnostics for per-group contributions.

#### Suggested files

- add `src/dreamsync/prediction/similarity.py`;
- add `dev/tests/test_live_structure_similarity.py`;
- extend prediction replay helpers.

#### Acceptance

- same pattern/same key scores high in both harmonic channels;
- transposed pattern scores high only in the transposed channel;
- instrumentation-only change affects timbre novelty;
- rhythm-only change affects rhythm novelty;
- a dynamics increase does not destroy section identity;
- one erroneous bar does not destroy an eight-bar match;
- memory and compute remain bounded.

### Phase D - Add causal phrase and boundary evidence

#### Goal

Turn similarity, novelty, homogeneity, and regularity into calibrated boundary
hypotheses.

#### Work

1. Implement causal novelty channels.
2. Implement rolling homogeneity evidence.
3. Adapt `CompetingPhraseTracker` to consume structure evidence.
4. Wire real `boundary_observed` events.
5. Reset phrase position from confirmed observed boundaries.
6. Remove cadence/chord requirements from the primary probability.
7. Keep 2/4/8/12/16 and irregular alternatives.
8. Distinguish post-boundary confirmation from future prediction.
9. Prevent modulo priors from being presented as learned cycles.

#### Suggested files

- add `src/dreamsync/prediction/boundary.py`;
- modify `src/dreamsync/prediction/phrase.py`;
- modify `src/dreamsync/prediction/engine.py`;
- modify GUI cycle/structure diagnostics;
- add `dev/tests/test_live_structure_boundaries.py`.

#### Acceptance

- same-chord timbre transition can produce a boundary;
- chord change within a stable section does not automatically produce one;
- quiet section transition remains detectable;
- every fourth bar is not automatically a boundary;
- a confirmed boundary resets phrase position;
- first-occurrence and recurrence predictions are labeled distinctly.

### Phase E - Replace numeral-first phrase and section memory

#### Goal

Learn anonymous song-local structure directly from bar sequences.

#### Work

1. Add `StructurePhraseFingerprint`.
2. Replace primary recurrence matching with multi-feature sequence matching.
3. Adapt `SectionOrderMemory` to structure fingerprints.
4. Preserve A/B/C identities before semantic labels.
5. Use `predict_successor()` in the engine.
6. Learn phrase and section duration distributions.
7. Implement prefix-match confidence growth.
8. Implement bounded merge/split logic.
9. Retain tonal fields only as sidecar diagnostics.
10. Stop requiring legacy `macro_change` to call `complete_phrase()` or
    `complete_section()`.

#### Suggested files

- modify `src/dreamsync/prediction/recurrence.py`;
- modify `src/dreamsync/prediction/section.py`;
- modify `src/dreamsync/prediction/memory.py`;
- modify `src/dreamsync/prediction/engine.py`;
- add `dev/tests/test_live_similarity_recurrence.py`;
- add `dev/tests/test_live_similarity_sections.py`.

#### Acceptance

- A/B/A/B structure is learned without chord labels;
- section successor prediction contributes evidence;
- transposed A can match earlier A;
- an energy-raised A remains A when other evidence matches;
- a same-chord but timbrally distinct B can remain B;
- one bad bar does not poison section identity.

### Phase F - Produce bar-indexed predictions

#### Goal

Predict future structural markers early enough to schedule a downbeat action.

#### Work

1. Extend event contracts with target beat/bar identity.
2. Generate `bar_marker`, `phrase_boundary`, `section_entrance`,
   `section_repeat`, `section_transition`, and `build_release`.
3. Combine prefix recurrence, section duration, section order, regularity, and
   trajectory evidence.
4. Calibrate cold-start and post-recurrence predictions separately.
5. Preserve alternatives and abstention.
6. Re-arbitrate predictions targeting the same downbeat.
7. Record lead time and target error.

#### Suggested files

- modify `src/dreamsync/prediction/models.py`;
- modify `src/dreamsync/prediction/engine.py`;
- modify `src/dreamsync/prediction/calibration.py`;
- modify `src/dreamsync/prediction/arbitration.py`;
- add `dev/tests/test_live_structural_predictions.py`.

#### Acceptance

- post-recurrence boundary prediction improves over duration prior alone;
- target bar/downbeat is stable before commitment;
- contradictory large actions cannot target the same downbeat;
- low-confidence cold-start cases abstain;
- no prediction uses a future bar.

### Phase G - Implement true bar-locked actuation

#### Goal

Make scheduled structural cues perform their intended visual action exactly on
the matching downbeat.

#### Work

1. Add `StructuralVisualActuator`.
2. Add the explicit effect-cycler structural-action API.
3. Apply selected enabled effects.
4. Implement phrase reset, section recall, palette advance/recall, and large
   transition actions.
5. Bind cue commitment to downbeat identity.
6. Return applied/degraded/rejected outcomes.
7. Disable arbitrary mood/drop/time/chord actuation while structure-controlled
   mode owns the output.
8. Preserve ordinary Reactive behavior outside structure-controlled mode.
9. Add deterministic effect selection under seeded replay.

#### Suggested files

- add `src/dreamsync/prediction/visual_actuator.py`;
- modify `src/dreamsync/prediction/cue_policy.py`;
- modify `src/dreamsync/effects.py`;
- modify `src/dreamsync/director.py`;
- modify `src/dreamsync/live.py`;
- extend null/simulation adapter diagnostics;
- add `dev/tests/test_live_bar_locked_actuation.py`.

#### Acceptance

- requested effect equals applied effect;
- disabled effect cannot be applied;
- phrase/section actions have distinct implemented behavior;
- no structural action commits on a secondary beat;
- no structural action commits from wall-clock proximity alone;
- mood/drop evidence cannot bypass the bar lock;
- no spatial settings change;
- brightness and output cadence remain bounded.

### Phase H - Shadow validation and simulator tooling

#### Goal

Measure the new analyzer and actuator without risking disruptive hardware
behavior.

#### Work

1. Add structure-similarity shadow mode.
2. Display top matches and evidence groups.
3. Display scheduled target downbeats.
4. Add simulation overlay markers for:
   - ordinary bar;
   - predicted phrase boundary;
   - predicted section transition;
   - committed action;
   - missed/degraded prediction.
5. Add deterministic audio replay into simulation output.
6. Compare legacy and new boundary streams.
7. Freeze initial thresholds after replay evaluation.

#### Acceptance

- shadow mode cannot change renderer, effect, palette, or spatial state;
- predicted and observed boundaries are visually distinguishable;
- simulator shows the exact applied effect/palette result;
- replay is deterministic;
- evidence and outcome logs are sufficient to diagnose every false cue.

### Phase I - Controlled cutover

#### Goal

Make structure similarity the only active structural engine after it passes
release gates.

#### Work

1. Enable ordinary bar actions first.
2. Enable phrase actions after phrase-boundary precision passes.
3. Enable section actions after post-recurrence precision passes.
4. Keep large cold-start actions disabled initially.
5. Disable legacy harmonic macro actuation.
6. Remove chord accents from default allowed cue classes.
7. Retain chord/key diagnostics behind the debug panel.
8. Rename GUI settings and labels to structure-first terminology.
9. Remove obsolete compatibility wiring after one stable release.

#### Acceptance

- one and only one engine owns structural actuation;
- ordinary, phrase, and section action tiers are independently reversible;
- disabling structure actions leaves continuous Reactive rendering functional;
- precompiled and pipeline behavior remain unchanged;
- no audio output path is introduced.

### Phase J - Optional tonal sidecar

#### Goal

Use reliable tonal evidence as a bonus without restoring tonal dependency.

#### Work

1. Add optional cadence/resolution evidence.
2. Add key-center diagnostics.
3. Allow a strong cadence to raise an existing boundary probability by a
   capped amount.
4. Prohibit tonal evidence from creating a section boundary by itself.
5. Run all structure tests with tonal fields removed.

#### Acceptance

- structure results remain valid with the sidecar disabled;
- enabling the sidecar does not lower non-tonal cohort performance;
- one incorrect chord cannot change section identity;
- no chord visual cue is re-enabled implicitly.

## Testing Matrix

### Unit tests

- feature-family normalization;
- bar closure and completeness;
- beat-wise chroma preservation;
- absolute harmonic similarity;
- transposition-invariant similarity;
- consistent sequence pitch shift;
- timbre similarity;
- rhythm similarity;
- dynamics invariance;
- reliability-weight redistribution;
- bounded memory;
- top-match ordering;
- novelty channels;
- homogeneity evidence;
- phrase hazard;
- observed-boundary reset;
- section prefix matching;
- duration distribution;
- successor prediction;
- cue arbitration;
- downbeat identity matching;
- actuator result states.

### Synthetic structural sequences

1. `A A A A | B B B B`
2. `A B A B`
3. four-bar A followed by eight-bar B
4. twelve-bar blues-like recurrence
5. sixteen-bar section
6. same chords, new instrumentation
7. new chords, same instrumentation and section identity
8. same section transposed
9. same section louder
10. quiet chorus/section entrance
11. drum-only section
12. silent or near-silent break
13. one missing beat
14. one substituted bar
15. one corrupted chroma vector
16. irregular 5- or 7-bar phrase
17. 3/4 configured meter
18. 6-beat configured meter
19. meter-confidence loss and recovery
20. song boundary/discontinuity reset.

### Deterministic replay tests

Every replay result must report:

- boundary precision/recall/F1;
- median and p95 target-bar error;
- prediction lead time;
- cold-start versus post-recurrence results;
- false large cues per minute;
- missed section returns;
- anonymous-section clustering consistency;
- action timing error from target downbeat;
- requested versus applied effect;
- capture and render performance.

Replay cohorts:

- pop;
- rock;
- EDM;
- hip-hop;
- acoustic;
- sparse/ambient;
- harmonically ambiguous;
- percussion-heavy;
- odd or ambiguous meter;
- live/crowd/noise contamination.

### Integration tests

- callback remains capture-only;
- no output audio API is constructed;
- beat observations reach bar builder;
- downbeat closes exactly one bar;
- bar reaches similarity memory;
- boundary evidence reaches engine;
- engine output reaches cue policy;
- scheduled cue survives until target;
- matching downbeat commits it;
- actuator applies the requested enabled effect;
- palette action is applied;
- simulation receives the actual result;
- hardware and simulation paths share semantics;
- discontinuity cancels scheduled cues;
- song boundary resets song-local memory;
- feature flags isolate legacy and new engines;
- compiled and pipeline shows remain unchanged.

### End-to-end tests currently missing and required

The implementation is not complete until automated tests prove:

1. a real similarity-derived section prediction causes a renderer/effect
   change;
2. that change occurs only on the target downbeat;
3. no chord label was required;
4. an effect-bank-disabled effect was not applied;
5. a same-chord timbre transition can be structurally meaningful;
6. a chord change within one stable section does not cause a large action.

## Evaluation Metrics and Release Gates

### Meter and timing

- confident downbeat precision on the supported meter cohort;
- structural action timing within one render interval of target downbeat;
- zero structural commits on secondary beats;
- zero wall-clock-only structural commits.

### Boundary quality

Report with exact-bar and +/-1-bar tolerances:

- phrase-boundary precision, recall, and F1;
- section-boundary precision, recall, and F1;
- first-occurrence versus repeated-occurrence performance;
- quiet-transition cohort;
- same-chord transition cohort;
- transposed-return cohort.

### Recurrence and identity

- phrase retrieval precision at K;
- section-prefix identification accuracy;
- anonymous-section clustering consistency;
- section-successor accuracy;
- post-recurrence improvement over duration priors.

### Lighting

- requested/applied effect agreement: 100%;
- disabled-effect violation: 0%;
- spatial mutation: 0%;
- false large actions per minute below the frozen product threshold;
- large-action precision above the frozen product threshold;
- degraded/cancelled cue does not alter output;
- all structural actions explainable from telemetry.

### Performance

- callback p99 does not regress;
- beat update p95 below 1 ms;
- completed-bar update p95 below 5 ms;
- completed-bar update p99 below 10 ms;
- no sustained audio-ring lag increase;
- no output cadence regression;
- bounded memory over a 60-minute replay.

### Initial rollout gate

Large section actions remain disabled until:

1. exact-downbeat action timing passes;
2. requested/applied effect agreement is 100%;
3. post-recurrence section-boundary precision meets the selected threshold;
4. false large cue cost has been reviewed manually;
5. quiet and same-chord transition cohorts pass;
6. no-chord-label replay produces viable results;
7. real simulation sessions pass before hardware testing.

## Manual Verification

### Simulation-first

1. Select simulation output.
2. Enable structure-similarity analysis and diagnostics.
3. Leave all structure actions disabled.
4. Play a song with an obvious verse/chorus return.
5. Confirm bar fingerprints appear only on downbeats.
6. Confirm similarity matches strengthen when the verse or chorus returns.
7. Confirm anonymous A/B identities are stable enough to be useful.
8. Confirm the predicted target bar is visible before the transition.
9. Enable ordinary bar actions only.
10. Confirm no action occurs on secondary beats.
11. Enable phrase actions.
12. Confirm medium actions occur on target downbeats.
13. Enable section actions.
14. Confirm the selected effect is actually applied in simulation.
15. Confirm requested and applied effect/palette diagnostics agree.

### Negative cases

- play static one-chord material with changing instrumentation;
- play harmonically busy material with stable instrumentation;
- play drum-only material;
- play silence and crowd/noise;
- interrupt capture;
- cause meter confidence to fall;
- disable a predicted effect in the effect bank;
- disable the cue master immediately before the target downbeat.

Expected behavior:

- structure evidence remains multi-feature;
- high-impact actions abstain when timing is uncertain;
- disabled effects are never applied;
- scheduled cues cancel on reset;
- no late action fires after a missed boundary;
- continuous rendering remains stable.

### Hardware gate

Hardware testing begins only after simulation acceptance.

Verify:

- no extra audio output/monitoring stream;
- target downbeat and visible action align;
- UDP/output cadence remains stable;
- device brightness limits are honored;
- stop/master-disable is immediate;
- no spatial settings change;
- no duplicate action is sent for one boundary.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| First novel section cannot be predicted | Conservative cold-start actions; learn it for recurrence; never backdate |
| Wrong downbeat causes every action to be wrong | Confidence gate; abstention; target beat/bar identity; meter diagnostics |
| Chroma remains noisy | Reliability weighting; retain timbre/rhythm/dynamics; no chord requirement |
| Transposition matching hides modulation | Preserve both absolute and transposed channels |
| Timbre dominates section identity | Calibrated group weights and cohort evaluation |
| Loudness change creates false B section | Low dynamics identity weight; compare relative contours |
| Same instrumentation hides quiet section | Use harmonic/rhythm recurrence and duration/order evidence |
| Fixed meter is presented as detection | Correct GUI terminology; later meter-length inference |
| Similarity work starves live rendering | Bar-rate updates, bounded memory, no callback work |
| Early A/B identity is wrong | Merge/split support and confidence-bearing hypotheses |
| Phrase prior becomes modulo rule | Require supporting evidence; separate prior from confirmed recurrence |
| Cue selected effect is not applied | Explicit actuator API and requested/applied integration tests |
| Mood/drop bypasses bar lock | Convert to evidence/selection context in structure-controlled mode |
| Wall-clock commit lands between beats | Commit only on matching downbeat identity |
| Legacy and new engines both actuate | Mutually exclusive ownership mode and telemetry |
| New behavior leaks into compiled shows | Reactive-only imports, settings, and regression tests |
| Neural work begins before target is correct | Keep Stage H blocked until structure-first release gates pass |

## Files Expected to Change

### New

- `src/dreamsync/prediction/structure_models.py`
- `src/dreamsync/prediction/bar_features.py`
- `src/dreamsync/prediction/similarity.py`
- `src/dreamsync/prediction/boundary.py`
- `src/dreamsync/prediction/visual_actuator.py`
- `dev/tests/test_live_structure_observation.py`
- `dev/tests/test_live_bar_fingerprints.py`
- `dev/tests/test_live_structure_similarity.py`
- `dev/tests/test_live_structure_boundaries.py`
- `dev/tests/test_live_similarity_recurrence.py`
- `dev/tests/test_live_similarity_sections.py`
- `dev/tests/test_live_structural_predictions.py`
- `dev/tests/test_live_bar_locked_actuation.py`

### Modified

- `src/dreamsync/live.py`
- `src/dreamsync/dsp/harmonic.py`
- `src/dreamsync/dsp/structure.py`
- `src/dreamsync/prediction/models.py`
- `src/dreamsync/prediction/runtime.py`
- `src/dreamsync/prediction/phrase.py`
- `src/dreamsync/prediction/recurrence.py`
- `src/dreamsync/prediction/section.py`
- `src/dreamsync/prediction/memory.py`
- `src/dreamsync/prediction/engine.py`
- `src/dreamsync/prediction/calibration.py`
- `src/dreamsync/prediction/arbitration.py`
- `src/dreamsync/prediction/cue_policy.py`
- `src/dreamsync/effects.py`
- `src/dreamsync/director.py`
- `src/dreamsync/gui/models/reactive_settings.py`
- `src/dreamsync/gui/services/session_service.py`
- `src/dreamsync/gui/services/runtime_supervisor.py`
- `src/dreamsync/gui/widgets/queue_panel.py`
- `src/dreamsync/gui/main_window.py`
- prediction replay and GUI test files.

### Possible common-DSP extraction

- add `src/dreamsync/dsp/spectral_features.py`;
- move reusable mel filterbank, DCT, and MFCC helpers from
  `src/dreamsync/analyzer/features.py`;
- keep offline behavior and tests unchanged.

## Migration and Deletion Criteria

Do not delete the legacy path at the start.

The sequence is:

1. freeze baseline;
2. add new observation and fingerprints;
3. run new similarity analysis in shadow mode;
4. compare legacy and new boundaries;
5. activate new ordinary bar actions;
6. activate new phrase actions;
7. activate new section actions;
8. disable legacy macro actuation;
9. retain chord/key diagnostics;
10. remove obsolete compatibility code only after stable replay and manual
    verification.

Legacy `LiveStructureTracker` may be removed or reduced to an adapter when:

- the new tracker independently closes phrase and section state;
- no production path calls `complete_phrase()` from legacy `macro_change`;
- no effect transition depends on chord labels;
- all structure-first release gates pass;
- rollback is available through a tagged release rather than dual live
  actuation.

## Definition of Done

This plan is complete when Reactive mode:

1. constructs bounded, beat-aligned, multi-feature bar fingerprints;
2. compares harmonic content without requiring chord labels;
3. retains octave compression;
4. computes both absolute and transposition-invariant harmonic similarity;
5. uses timbre, rhythm, and dynamics alongside harmonic evidence;
6. recognizes song-local phrase and anonymous-section recurrence;
7. uses real observed boundaries to reset phrase/section state;
8. actively uses section-duration and successor memory;
9. distinguishes cold-start from post-recurrence predictions;
10. predicts a target beat/bar/downbeat rather than only a timestamp;
11. commits structural visual actions only on the matching confident downbeat;
12. applies the selected enabled effect rather than only publishing its name;
13. implements phrase reset, section recall, and section transition actions;
14. prevents mood, drop, timer, chord, and secondary-beat paths from bypassing
    the bar lock while structure control is active;
15. abstains safely on ambiguous meter or structure;
16. exposes evidence, alternatives, target, cue state, and actual action;
17. passes deterministic replay, simulation, and hardware verification;
18. stays within callback, analysis, memory, and render budgets;
19. remains capture-only and never creates an audio output path;
20. leaves precompiled shows, pipeline compilation, and saved timelines
    unchanged;
21. retains tonal analysis only as an optional sidecar;
22. keeps corpus/neural work blocked until the measured deterministic baseline
    justifies it.

## Recommended First Implementation Slice

Implement Phases 0 through C first:

```text
baseline audit tests
  -> chord-optional beat observation
  -> completed multi-feature bar fingerprints
  -> bounded absolute/transposed/timbre/rhythm/dynamics similarity
  -> diagnostics only
```

This slice is deliberately non-actuating. It will answer the central technical
question before more cue tuning:

```text
Can Reactive mode recognize that the current bars resemble an earlier part of
the same song without knowing the chords?
```

Only after that answer is measured should Phases D through F generate boundary
predictions. Only after those predictions pass replay should Phase G change
effects.
