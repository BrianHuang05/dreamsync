# Plan: Predictive Live Music Structure and Lighting Cues

## Status

**Proposed — implementation-ready, reactive live path only.**

This document defines the next stage of DreamSync's live harmonic work:
predicting musically meaningful events early enough to prepare and schedule
lighting cues.

The product objective is not frequency analysis or chord transcription for its
own sake. The objective is:

```text
musical knowledge
  + causal live observations
  + memory of the current song
      -> predicted musical events
      -> confidence-gated lighting cues
```

The predictor should eventually reason in forms such as:

```text
Knowledge:
  In 4/4, four-bar phrases are common. V near the end of a phrase often
  resolves to I at the next phrase boundary.

Observation:
  Key-center hypotheses favor C major. The current phrase has produced
  vi -> IV -> V over three bars, and the next confident downbeat is 1.8
  seconds away.

Prediction:
  I is likely on beat 1 of the next bar, with a likely phrase resolution.

Action:
  Arm a long flash and color-profile transition, then execute them on the
  predicted downbeat only if cue confidence remains above policy thresholds.
```

This plan extends the existing live-only meter, chord-history, progression,
structure, diagnostics, effect-bank, and color-profile work. It does not create
a parallel audio-analysis stack.

## Product Decision

Build a **hierarchical, hybrid predictor**, not a single end-to-end model that
maps audio directly to lighting.

The system will keep four responsibilities separate:

1. **Observation:** derive causal, confidence-bearing musical evidence from
   live audio.
2. **Musical inference:** estimate key centers, Roman-numeral harmony, phrase
   position, recurrence, section state, and momentum.
3. **Prediction:** produce distributions over future musical events and their
   likely times.
4. **Visual policy:** decide whether and how a musical prediction may cue the
   currently enabled effects and color profiles.

The model predicts music. The lighting policy interprets those predictions.
This boundary is required for explainability, safe cueing, effect-bank control,
and future model replacement.

## Scope Boundaries

### In scope

- reactive live mode only;
- causal analysis of the incoming capture stream;
- probabilistic key-center tracking;
- Roman-numeral chord representation;
- competing bar- and phrase-length hypotheses;
- common-progression and cadence priors;
- song-local learning of progressions, phrases, and section order;
- next-chord and next-change-time prediction;
- likely phrase boundary and harmonic-resolution prediction;
- chorus-entrance, verse-repeat, and section-transition prediction;
- mood/energy/tension trajectory prediction;
- confidence-calibrated cue proposals;
- arming, scheduling, confirming, cancelling, and degrading visual cues;
- local event logging, deterministic replay, and model evaluation;
- GUI diagnostics for evidence, alternatives, predictions, and cue state.

### Explicitly out of scope

- no audio output, monitoring, loopback playback, or PCM forwarding;
- no changes to precompiled shows, pipeline shows, saved show timelines, or
  offline playback behavior;
- no direct audio-to-light neural model;
- no requirement for cloud inference, network access, a GPU, or a model
  download during live operation;
- no automatic modification of spatial settings;
- no activation of an effect that the user has disabled in the reactive
  effect bank;
- no assumption that all songs are in 4/4 or use four-bar phrases;
- no promise of exact verse/chorus names when the evidence supports only a
  generic section boundary or recurrence;
- no use of a prediction as if it were an observed fact;
- no heavyweight runtime ML dependency until an evaluated baseline proves it
  necessary.

Reactive live mode remains a capture-and-control path. It must open only an
audio input stream and must never instantiate or call an audio output path.

## Existing Baseline and Known Gaps

The current implementation already provides useful foundations:

- `LiveHarmonicAnalyzer` produces filtered pitch-class evidence and chord
  candidates.
- `LiveMeterTracker` provides beat, downbeat, bar phase, and confidence.
- `LiveChordHistory` commits chord changes at logical beat locations.
- `LiveChordProgressionPredictor` recognizes short repeated chord sequences
  and prior-section prefixes.
- `LiveStructureTracker` combines harmonic and momentum evidence at expected
  phrase boundaries.
- `live.py` keeps the result on the reactive path and exposes compact GUI and
  renderer state.
- the reactive GUI shows chord history, chord-wheel diagnostics, predictions,
  and mismatches.

The current predictor is intentionally limited:

- repeated-pattern search is capped at eight chord events;
- active phrase reasoning assumes a fixed `bars_per_phrase=4`;
- chord events are stored primarily as absolute labels rather than functions
  relative to competing key centers;
- the predictor identifies literal repetition but has no general cadence or
  common-progression prior;
- it has no calibrated probability distribution over alternatives;
- it does not explicitly model chord duration, bar position, harmonic rhythm,
  section identity, or momentum trajectory;
- current prediction state is informational and does not own a controlled cue
  lifecycle;
- section prediction and mood-change prediction are not first-class outputs.

These limitations explain why regular four-chord loops work better than
longer eight- or twelve-bar progressions. The immediate problem is not evidence
that a large neural network is required. It is evidence that the state
representation, context horizon, musical normalization, and prediction
contracts need to be expanded.

## Architectural Principles

### 1. Remain causal

Every runtime feature and prediction must use only samples and events whose
audio timestamps are at or before the current capture time. Replay tests must
fail if future events leak into a prediction.

### 2. Preserve uncertainty

Do not collapse uncertain tonality immediately to one key or one Roman
numeral. Retain a small beam of key/function hypotheses with probabilities.
For example:

```text
C major: 0.62  -> current chord V
A minor: 0.29  -> current chord VII
other:   0.09
```

Predictions should weaken when the input is ambiguous rather than repeatedly
flipping labels.

### 3. Use several musical timescales

The system must reason concurrently about:

- sub-beat lead time for cue scheduling;
- beat-level chord changes;
- bar-level harmonic rhythm;
- 2-, 4-, 8-, 12-, and 16-bar phrase hypotheses;
- section-scale recurrence;
- whole-song section order.

No one fixed history window is sufficient for all outputs.

### 4. Prefer song-local evidence when available

Generic musical knowledge is most useful before the song has revealed its own
rules. Once a verse, chorus, or progression has occurred, a strong match to
that song's prior material should outweigh a weaker corpus-level convention.

### 5. Separate prediction from actuation

A model result is a `PredictedMusicalEvent`. It becomes a `CueProposal` only
after visual policy evaluates:

- event type;
- event confidence and calibration;
- timing uncertainty;
- meter/downbeat confidence;
- user-enabled effects;
- active color profile;
- cooldown and brightness safety;
- conflicts with other proposals.

### 6. Fail gracefully

When confidence is low:

- continue observed beat and chord reactions;
- show diagnostic hypotheses;
- do not issue an anticipatory cue;
- recover without resetting unrelated tempo or effect state.

### 7. Keep runtime work bounded

- fixed-size history and song-memory structures;
- no unbounded token or phrase growth;
- no training in the audio callback;
- no inference in the audio callback;
- no per-frame filesystem writes;
- prediction updates primarily on committed chord, beat, and downbeat events;
- GUI publication remains rate-limited.

## Target Runtime Architecture

```text
capture-only audio input
  -> existing bounded live audio ingest
  -> existing beat / meter / harmonic / momentum analysis
  -> committed beat-synchronous musical observations
       -> probabilistic key-center tracker
       -> Roman-numeral/function encoder
       -> bar and phrase hypothesis tracker
       -> song-local progression and section memory
       -> musical-prior scorer
       -> unified predictive-structure engine
            -> next chord + timing distribution
            -> phrase boundary / resolution
            -> section entrance / repeat
            -> mood-energy-tension trajectory
  -> prediction calibration and arbitration
  -> visual cue policy
       -> observe
       -> arm
       -> schedule
       -> commit
       -> confirm / cancel / degrade
  -> existing live director, effect cycler, renderer, and adapters
```

There is no audio-output branch.

## Musical Representation

### Beat-synchronous observation

Add an immutable observation produced only after the live meter and chord
history have committed their relevant state:

```python
@dataclass(frozen=True)
class LiveMusicalObservation:
    t: float
    beat_index: int
    bar_index: int | None
    beat_in_bar: int | None
    meter: tuple[int, int] | None
    meter_confidence: float
    downbeat: bool
    absolute_chord: str | None
    chord_confidence: float
    chord_change: bool
    chord_duration_beats: float | None
    chroma: tuple[float, ...]
    tonal_confidence: float
    energy: float
    energy_delta: float
    onset_density: float
    onset_density_delta: float
    spectral_centroid: float
    centroid_delta: float
    harmonic_rhythm: float
```

The live path should publish one consolidated observation contract rather than
allowing the prediction package to inspect mutable fields spread throughout
`live.py`.

### Key-center hypothesis

```python
@dataclass(frozen=True)
class KeyHypothesis:
    tonic_pc: int
    mode: Literal["major", "minor"]
    probability: float
    stability: float
    age_beats: int
```

Retain at most a configured top-K beam, initially three hypotheses. Key changes
must require sustained evidence. Short tonicizations should be representable
without immediately declaring a modulation.

### Functional chord hypothesis

```python
@dataclass(frozen=True)
class FunctionalChordHypothesis:
    key: KeyHypothesis
    degree: int
    numeral: str
    quality: str
    inversion: int | None
    borrowed: bool
    applied_target: int | None
    probability: float
```

Initial runtime support may restrict quality to major/minor and mark other
qualities as `unknown`. The schema must not prevent later diminished,
augmented, suspended, seventh, borrowed, or secondary-dominant functions.

Use conventional case:

- `I`, `IV`, and `V` for major triads;
- `ii`, `iii`, and `vi` for minor triads;
- explicit annotations for ambiguity rather than forcing a false numeral.

### Bar summary

```python
@dataclass(frozen=True)
class PredictiveBarSummary:
    start_t: float
    end_t: float
    bar_index: int
    meter_confidence: float
    functional_chords: tuple[str, ...]
    chord_durations_beats: tuple[float, ...]
    key_hypotheses: tuple[KeyHypothesis, ...]
    harmonic_rhythm: float
    cadence_features: tuple[float, ...]
    energy: float
    energy_slope: float
    onset_density: float
    onset_slope: float
    texture: tuple[float, ...]
```

### Phrase and section fingerprints

Phrase fingerprints should be transposition-invariant where possible:

```python
@dataclass(frozen=True)
class PhraseFingerprint:
    bars: int
    numerals: tuple[str, ...]
    chord_duration_pattern: tuple[int, ...]
    cadence_class: str
    harmonic_rhythm_pattern: tuple[int, ...]
    energy_shape: tuple[int, ...]
    onset_shape: tuple[int, ...]
    key_path: tuple[str, ...]
```

Section fingerprints aggregate phrase fingerprints plus:

- likely section role;
- duration distribution;
- entrance and exit signatures;
- energy and texture envelope;
- recurrence count;
- preceding and following section hypotheses;
- visual motif used previously, if any.

Fingerprints are evidence for recurrence, not permanent labels. A later
observation may merge or split stored motifs.

## Knowledge Layer

The knowledge layer supplies priors, never absolute rules.

### Initial hand-authored priors

Implement small, inspectable tables for the first baseline:

- meter-conditioned phrase-length distributions;
- common bar lengths, initially including 2, 4, 8, 12, and 16;
- common functional progressions and suffixes;
- cadence likelihoods such as `V -> I`, `IV -> I`, `ii -> V -> I`;
- deceptive alternatives such as `V -> vi`;
- tonic likelihood near phrase starts;
- dominant or predominant likelihood near phrase endings;
- common section-duration distributions;
- common section-order transitions;
- harmonic-rhythm changes near section entrances;
- momentum patterns such as rising energy/onset density before a chorus.

Every prior must:

- return a distribution, not a Boolean;
- have a documented source/version;
- be editable without changing runtime algorithms;
- support an `unknown` outcome;
- be weaker than strong song-local recurrence evidence;
- be testable independently.

Suggested storage:

```text
src/dreamsync/prediction/priors/
  phrase_lengths.yaml
  progressions.yaml
  cadences.yaml
  section_transitions.yaml
```

The YAML support should remain optional only if the predictor can package and
load defaults reliably. An immutable Python fallback is acceptable during the
first implementation.

### Corpus-trained priors

After the event schema and evaluation harness are stable, replace or augment
hand-authored probabilities with counts learned from licensed, properly
attributed symbolic chord/section corpora.

Training normalization should include:

- chord symbols to canonical qualities;
- key-relative Roman numerals;
- beat and bar alignment;
- transposition augmentation;
- section-label normalization;
- explicit unknown/no-chord tokens;
- song-level and artist-level split protection;
- metadata recording for dataset version and license.

Do not put corpus parsing, dataset dependencies, or training code on the live
runtime import path.

## Observation Layer

### Key-center tracking

Implement a causal probabilistic key tracker using:

- beat- and bar-smoothed chroma;
- chord-root evidence;
- duration-weighted pitch-class evidence;
- compatibility with major/minor key profiles;
- transition penalties for modulation;
- hysteresis and minimum residence time;
- a top-K beam rather than a single label.

The first version should be NumPy-based and deterministic. It should expose
the contribution of chroma, chord, and transition evidence for diagnostics.

### Roman-numeral conversion

For every committed chord:

1. generate a functional interpretation under each retained key hypothesis;
2. score diatonic compatibility, chord quality, root support, and key
   probability;
3. preserve the best few interpretations;
4. emit `unknown` when neither chord nor key evidence is adequate;
5. update stored progressions with probability-weighted observations rather
   than silently committing an uncertain numeral.

### Phrase-position tracking

Replace the one fixed four-bar counter with competing phrase hypotheses.

Each hypothesis tracks:

- phrase start bar;
- expected length;
- current bar position;
- probability;
- cadence support;
- recurrence support;
- momentum-boundary support;
- meter continuity;
- survival probability if the expected boundary passes.

Initial supported lengths should be configurable and include:

```text
2, 4, 8, 12, 16 bars, plus irregular/unknown
```

Use a duration or hazard model so a four-bar phrase can remain likely without
making every fourth bar a mandatory boundary.

### Momentum observations

The initial momentum vector should reuse causal features already available in
the live path:

- energy and energy slope;
- onset density and slope;
- spectral centroid and slope;
- beat strength;
- harmonic rhythm;
- recent silence or breakdown probability;
- tempo stability;
- texture contrast;
- current macro-change evidence.

Later observation families may be added only if they improve held-out replay
metrics without compromising latency.

Mood should initially mean a trajectory over interpretable axes:

- energy;
- brightness;
- tension;
- rhythmic density;
- harmonic stability.

Do not infer a categorical emotional label solely from major/minor quality.

## Song-Local Learning

### Variable-order progression model

Replace the fixed eight-event search with a bounded variable-order model,
similar to an n-gram model with backoff or prediction-by-partial-matching.

The model should:

- consume Roman-numeral/function hypotheses;
- retain chord duration and bar-position tokens;
- support contexts spanning at least 16 bars;
- back off from long to short context when evidence is sparse;
- decay stale low-value branches;
- bound memory by node count and song duration;
- update online after committed observations;
- return a full next-token distribution;
- expose which context length produced the result.

Example tokens:

```text
vi@bar1:4beats
IV@bar2:4beats
V@bar3:4beats
```

The distribution should include both next harmonic function and likely time.

### Phrase recurrence memory

Store completed phrase fingerprints and match the current partial phrase
against them incrementally.

The matcher should support:

- exact Roman-numeral recurrence;
- duration-aware similarity;
- partial-prefix matching;
- small chord substitutions;
- transposition via functional representation;
- key changes within a phrase;
- momentum-envelope similarity;
- confidence that increases as more bars match.

Once the opening bars of a previous verse or chorus match, its continuation
becomes a strong prediction candidate.

### Section-order memory

Maintain a bounded transition graph for the current song:

```text
intro -> verse -> chorus -> verse -> chorus -> bridge -> chorus -> outro
```

The runtime does not need to name every node correctly at first. Stable
anonymous section identities such as `A`, `B`, and `C` are enough to learn:

- recurrence;
- usual duration;
- likely successor;
- typical entrance chord;
- momentum envelope;
- prior visual motif.

Semantic labels such as `verse` or `chorus` should be emitted only when their
posterior confidence clears a separate threshold.

## Prediction Layer

### Unified predicted-event contract

```python
@dataclass(frozen=True)
class PredictedMusicalEvent:
    prediction_id: str
    created_t: float
    target_t: float
    timing_sigma: float
    event_type: Literal[
        "chord_change",
        "harmonic_resolution",
        "phrase_boundary",
        "section_entrance",
        "section_repeat",
        "chorus_entrance",
        "verse_repeat",
        "mood_change",
    ]
    target_function: str | None
    target_section: str | None
    direction: str | None
    probability: float
    alternatives: tuple["PredictionAlternative", ...]
    evidence: tuple["PredictionEvidence", ...]
    model_version: str
```

`timing_sigma` describes uncertainty around `target_t`. A cue should not be
scheduled as precisely as a downbeat when timing uncertainty is broad.

### Required prediction heads

#### Next chord and chord-change time

Predict:

- probability of a chord change on each upcoming beat/downbeat;
- Roman-numeral distribution for the new chord;
- absolute chord distribution marginalized over key hypotheses;
- expected duration;
- timing uncertainty.

#### Harmonic resolution

Predict:

- whether current tension is likely to resolve;
- expected target function;
- expected phrase position;
- strength of the resolution;
- deceptive-resolution alternatives.

#### Phrase boundary

Predict:

- boundary probability at upcoming downbeats;
- likely completed phrase length;
- whether the next phrase repeats, varies, or introduces new material.

#### Section entrance

Predict:

- generic section-boundary probability;
- recurrence identity;
- semantic role probability, including chorus and verse;
- expected entrance time and likely opening harmony;
- momentum direction.

Chorus inference should combine multiple cues:

- match to a previously observed chorus fingerprint;
- section-order prior;
- phrase completion;
- harmonic cadence;
- entrance chord;
- rising energy or onset density;
- texture expansion;
- longer-term recurrence.

No single chord progression should be sufficient to declare a chorus.

#### Verse repeat

Predict a verse repeat primarily from song-local phrase/section recurrence and
section order. Generic priors may help before enough local history exists but
must not dominate.

#### Mood/trajectory change

Predict a future change in energy, brightness, tension, density, or harmonic
stability. Emit a direction and horizon, for example:

```text
energy rising over 2 bars
tension likely to resolve at next downbeat
texture likely to widen at section entrance
```

### Probabilistic fusion

The initial unified score may be implemented as calibrated log-probability
fusion:

```text
score(candidate) =
    w_prior       * log P(candidate | musical priors)
  + w_local       * log P(candidate | song-local sequence)
  + w_recurrence  * phrase/section match score
  + w_phrase      * phrase-position score
  + w_cadence     * cadence score
  + w_momentum    * momentum score
  + w_meter       * meter/downbeat confidence
  + w_observation * chord/key confidence
```

Weights begin as explicit configuration and are calibrated from replay data.
The fusion layer must return:

- normalized alternatives;
- evidence contributions;
- confidence before and after calibration;
- an abstention when no candidate is reliable.

The song-local weight should rise as repeated material accumulates.

## Visual Cue Policy

### Cue contract

```python
@dataclass(frozen=True)
class CueProposal:
    cue_id: str
    prediction_id: str
    created_t: float
    execute_t: float
    expires_t: float
    cue_class: str
    effect_candidates: tuple[str, ...]
    color_action: str | None
    intensity: float
    confidence: float
    state: Literal["proposed", "armed", "scheduled"]
    explanation: str
```

The predictor must not call the renderer or adapters. It submits predicted
musical events to the cue policy.

### Cue lifecycle

```text
observed context
  -> prediction
  -> proposal
  -> armed
  -> scheduled
  -> committed
  -> confirmed
             \-> cancelled
             \-> degraded
```

Rules:

1. **Propose:** create a diagnostic proposal when a musically relevant
   prediction exists.
2. **Arm:** reserve an eligible effect/color action when confidence and lead
   time are sufficient.
3. **Schedule:** bind execution to the predicted beat/downbeat deadline.
4. **Re-evaluate:** update or cancel if the prediction, key, meter, or target
   time changes.
5. **Commit:** send the visual action only through the normal live render path.
6. **Confirm:** record whether the observed event matched the prediction.
7. **Degrade:** use a smaller observed-event accent when a large anticipatory
   cue is no longer justified.

### Confidence tiers

Initial policy tiers should be configurable and calibrated:

- **diagnostic:** prediction is displayed and logged, with no optical action;
- **prepare:** effect/color action may be armed but remains invisible;
- **schedule:** reversible or bounded cue may be scheduled;
- **high-impact:** long flash, palette transition, or effect-bank transition
  requires the highest threshold and strong timing confidence.

High-impact cues also require:

- a confident upcoming downbeat;
- sufficient lead time;
- no active cooldown conflict;
- user safety/brightness limits;
- an enabled compatible effect;
- a current prediction that has not materially changed.

### Musical event to visual-intent mapping

The mapping is policy data, not model code:

| Predicted event | Candidate visual intent |
|---|---|
| ordinary chord change | small color movement, ripple, or harmonic accent |
| strong `V -> I` resolution | long flash and color-profile transition |
| phrase boundary | wave/ripple reset or medium transition |
| chorus entrance | larger effect-bank and palette transition |
| verse repeat | recall or vary the prior verse visual motif |
| bridge/new section | controlled contrast or effect-family change |
| energy rise | gradually increase intensity/motion |
| tension release | brightness bloom or color convergence |
| uncertain event | diagnostics only |

The active reactive effect bank is authoritative. The policy may select only
from enabled effects. It may use the active or user-approved color profiles,
but it must not change spatial mapping or hidden device settings.

### Worked acceptance scenario

Input:

```text
meter = 4/4, confident
phrase hypotheses = 4 bars: 0.74, 8 bars: 0.18, unknown: 0.08
key = C major: 0.91
observed bars = vi | IV | V
current position = bar 3 of likely 4
next downbeat = 1.8 seconds
song-local match = moderate
```

Expected prediction:

```text
event_type = harmonic_resolution
target_function = I
target_t = next downbeat
probability >= configured schedule threshold
alternatives include vi and V
evidence includes dominant-resolution, phrase position, key confidence,
and song-local context
```

Expected action:

```text
arm long flash + color change
schedule at next downbeat
cancel/degrade if V changes early, meter confidence collapses, or the
posterior drops below threshold
confirm if I is observed within the timing tolerance
```

Expected learning:

- an observed `I` strengthens the song-local `vi -> IV -> V -> I` pattern;
- a deceptive `vi` strengthens that alternative for later repetitions;
- the system records timing and cue outcomes for calibration.

## Libraries and Build-vs-Dependency Decision

### Runtime baseline

Use the project's existing runtime dependencies:

- **NumPy:** vectorized probabilities, fixed-size matrices, similarity, and
  bounded numerical state;
- **SciPy:** only where an existing stable numerical primitive materially
  simplifies implementation.

Build the following in DreamSync because they are small, streaming-specific,
and tightly coupled to the runtime contracts:

- key-hypothesis beam;
- Roman-numeral encoder;
- phrase-length hazard model;
- variable-order song-local sequence model;
- phrase/section recurrence memory;
- probability-fusion and calibration interfaces;
- cue-policy state machine;
- replay event schema and metrics.

Do not add scikit-learn to the live runtime merely for online counts,
calibration tables, or nearest-neighbor phrase matching. Those operations are
small enough to implement deterministically with NumPy and typed Python
structures.

### Offline research/training extras

If corpus-trained priors or a compact neural model are justified later, use
optional development dependencies kept outside the runtime baseline:

- a symbolic-music parser for dataset preparation;
- a training framework such as PyTorch for experiments;
- standard evaluation/dataframe tooling in scripts only;
- an exchange/runtime format only after deployment measurements prove it is
  acceptable.

Do not select or add an inference runtime during the initial phases. First
measure whether the probabilistic song-local baseline meets the product
metrics.

### Neural-model decision gate

A neural model is authorized for implementation only after:

1. the causal replay schema is stable;
2. a licensed training corpus is identified;
3. the non-neural baseline is measured;
4. held-out results show a material remaining gap;
5. an inference budget is defined;
6. the candidate model improves calibration or early-event accuracy without
   increasing unsafe false cues;
7. live mode retains a no-model fallback.

A likely later experiment is a compact event-token model operating on
Roman-numeral, duration, bar-position, key, section, and momentum tokens. It
must not consume raw PCM in its first version.

## Runtime Performance Budgets

Initial budgets should be verified on the supported desktop hardware:

- no prediction work in the PortAudio callback;
- event update p95 below 2 ms on committed beats/chords;
- downbeat/bar update p95 below 5 ms;
- bounded song memory, initially under 10 MB;
- GUI serialization contains summaries, not full model state;
- prediction publication at semantic-event rate or at most the existing GUI
  snapshot cadence;
- no increase in audio-ring latency ceiling;
- no additional output frames beyond the configured renderer rate;
- no blocking filesystem write on the analysis/render loop.

If a later model cannot meet its inference deadline, prediction is skipped for
that cycle and observed reactive behavior continues.

## Persistence and Privacy

Configuration persistence should include:

- predictive structure enabled;
- predictive cueing enabled;
- shadow/diagnostic mode;
- confidence tier thresholds;
- allowed cue classes;
- maximum anticipatory flash intensity;
- cue cooldowns;
- model/prior version;
- optional local learning-log consent.

By default:

- do not persist raw audio;
- keep song-local memory in RAM and reset it at a confirmed song boundary;
- persist only bounded, structured observation/prediction/outcome logs when
  diagnostic logging is enabled;
- never upload observations or model data.

Replay fixtures intended for the repository must be derived structured events
or explicitly approved synthetic/licensed audio.

## Telemetry and Explainability

Every prediction log should contain:

- prediction ID and model version;
- creation and target audio timestamps;
- target event and alternatives;
- raw and calibrated probabilities;
- key hypotheses;
- phrase-length hypotheses;
- matched song-local pattern/section, if any;
- generic priors used;
- momentum evidence;
- meter, chord, and key confidence;
- cue decision and reason;
- final observed outcome;
- timing and tone mismatch;
- cancellation or degradation reason.

The GUI should be able to answer:

- What does the system think the key is?
- How is the current chord interpreted functionally?
- Where does it think we are in a phrase?
- What event is predicted next, and when?
- Which evidence supports that prediction?
- What alternatives remain plausible?
- Is a visual cue merely proposed, armed, or scheduled?
- Why was a cue cancelled or suppressed?

Do not render every internal probability at full frame rate. Publish compact
top-K summaries.

## Evaluation Strategy

### Evaluation units

Measure at several levels:

1. chord-function classification;
2. next-chord prediction;
3. change-time prediction;
4. phrase-length and phrase-boundary prediction;
5. section recurrence;
6. chorus/verse entrance prediction;
7. mood/trajectory direction;
8. final lighting-cue quality.

### Core metrics

#### Harmonic prediction

- next-function top-1 and top-3 accuracy;
- negative log likelihood;
- Brier score;
- calibration error;
- chord-change timing MAE;
- percentage within beat and downbeat tolerances;
- abstention coverage and accuracy.

#### Structure prediction

- phrase-boundary precision, recall, and F1;
- predicted phrase-length accuracy;
- section-boundary precision, recall, and F1;
- recurrence-link accuracy;
- chorus/verse entrance precision and recall;
- median usable lead time before the boundary;
- early/late timing distribution.

#### Mood trajectory

- direction accuracy for energy/brightness/tension/density;
- false change rate;
- lead time;
- correlation with annotated or derived trajectories.

#### Lighting policy

- anticipatory cues per minute;
- false high-impact cues per minute;
- cancelled cues per minute;
- cues committed too early or late;
- prediction-to-cue latency;
- observed fallback success;
- high-impact cue precision;
- manual perceptual ratings for musical timing and visual appropriateness.

False high-impact cues should carry a materially larger evaluation cost than a
missed anticipatory cue.

### Data splits

For any corpus-trained component:

- split by song, not by phrase;
- protect against alternate versions of the same song crossing splits;
- where possible, split by artist to measure generalization;
- reserve a final held-out set before tuning;
- report genre, meter, phrase-length, and modulation cohorts separately;
- keep long 8-, 12-, and 16-bar progressions as explicit cohorts.

For song-local replay:

- evaluate early-song cold start separately;
- measure improvement after first recurrence;
- measure misleading-repetition and deceptive-cadence cases;
- retain failures rather than filtering them from aggregates.

## Implementation Phases

Each phase must preserve live observed-reaction behavior unless its acceptance
criteria explicitly authorize predictive cueing.

### Phase A — Contracts, logging, replay, and frozen baseline

#### Goal

Create a reproducible causal evaluation harness before changing the predictor.

#### Work

1. Add typed observation, key/function, prediction, evidence, and outcome
   contracts.
2. Consolidate existing meter, harmonic, structure, and momentum fields into
   `LiveMusicalObservation`.
3. Add a bounded structured event logger with a schema version.
4. Record prediction lifecycle and observed outcomes without raw audio.
5. Add a deterministic replay engine that feeds observations with their
   original audio timestamps.
6. Adapt the current `LiveChordProgressionPredictor` behind the new prediction
   interface as the baseline.
7. Freeze baseline metrics for:

   - four-chord loops;
   - 8-bar progressions;
   - 12-bar progressions;
   - irregular changes;
   - deceptive cadences;
   - modulations;
   - low-confidence/noisy harmony.

8. Add causality tests that truncate replay at every prediction point and
   prove future observations do not affect current output.

#### Suggested files

- add `src/dreamsync/prediction/__init__.py`
- add `src/dreamsync/prediction/models.py`
- add `src/dreamsync/prediction/replay.py`
- add `src/dreamsync/prediction/metrics.py`
- add `scripts/evaluate_live_predictions.py`
- modify `src/dreamsync/live.py`
- add `dev/tests/test_live_prediction_contracts.py`
- add `dev/tests/test_live_prediction_replay.py`
- extend `dev/tests/test_live_harmonic.py`

#### Acceptance

- replay is deterministic across repeated runs;
- future observations cannot change prior predictions;
- logs are bounded and schema-versioned;
- no raw PCM is persisted;
- current predictor results can be evaluated through the new interface;
- the 4-, 8-, and 12-bar baseline report is committed before tuning.

### Phase B — Probabilistic key centers and Roman numerals

#### Goal

Normalize observed harmony into transferable musical functions while
preserving key ambiguity.

#### Work

1. Implement major/minor key profiles and a causal top-K key beam.
2. Weight chord and chroma evidence by confidence and duration.
3. Add transition penalties, stability, tonicization, and modulation
   hysteresis.
4. Implement functional chord hypotheses under each key.
5. Add `unknown` and no-chord behavior.
6. Surface absolute chord, likely Roman numeral, key alternatives, and
   probabilities in diagnostics.
7. Reset song-local tonality only at a confirmed song boundary; preserve
   short-term uncertainty through ambiguous frames.

#### Suggested files

- add `src/dreamsync/dsp/tonality.py`
- add `src/dreamsync/prediction/function.py`
- modify `src/dreamsync/prediction/models.py`
- modify `src/dreamsync/live.py`
- modify reactive harmonic diagnostics
- add `dev/tests/test_live_tonality.py`
- add `dev/tests/test_live_functional_harmony.py`

#### Acceptance

- transposed versions of one progression produce equivalent functional
  sequences;
- relative major/minor ambiguity remains visible and stable;
- isolated borrowed or out-of-key chords do not force immediate modulation;
- sustained key changes eventually switch the leading key hypothesis;
- low-confidence harmony yields `unknown` rather than a confident false
  numeral;
- runtime budgets remain satisfied.

### Phase C — Bar-aware phrase hypotheses and musical priors

#### Goal

Support long and variable phrase lengths and general musical expectations.

#### Work

1. Implement bar summaries and competing phrase-length hypotheses.
2. Remove the eight-event prediction ceiling from the active path.
3. Add duration/hazard priors for 2-, 4-, 8-, 12-, and 16-bar phrases plus
   irregular/unknown.
4. Add progression suffix and cadence priors.
5. Predict boundary probability at future confident downbeats.
6. Expose the leading phrase positions and their alternatives.
7. Keep the old fixed-four-bar structure result in shadow comparison until the
   new model is validated.

#### Suggested files

- add `src/dreamsync/prediction/priors.py`
- add `src/dreamsync/prediction/phrase.py`
- add packaged prior data
- modify `src/dreamsync/dsp/structure.py`
- add `dev/tests/test_live_phrase_tracker.py`
- add `dev/tests/test_live_musical_priors.py`
- extend `dev/tests/test_live_structure.py`

#### Acceptance

- synthetic 4-, 8-, 12-, and 16-bar progressions retain the correct leading
  phrase-length hypothesis;
- a cadence can raise boundary probability but cannot force a boundary alone;
- irregular phrases remain representable;
- meter-confidence loss suppresses precise boundary scheduling;
- the original four-bar acceptance scenarios do not regress.

### Phase D — Online song-local progression and phrase learning

#### Goal

Learn the rules of the current song and outperform generic priors after
repetition occurs.

#### Work

1. Implement the bounded variable-order functional progression model.
2. Include chord duration and bar position in its context.
3. Implement incremental phrase fingerprinting and partial matching.
4. Add substitution-tolerant recurrence scoring.
5. Fuse generic and song-local distributions with an increasing local weight.
6. Store prediction source and context length for diagnostics.
7. Reset memory at confirmed song boundaries.
8. Add explicit protection against one erroneous chord poisoning a long
   context.

#### Suggested files

- add `src/dreamsync/prediction/online_sequence.py`
- add `src/dreamsync/prediction/recurrence.py`
- add `src/dreamsync/prediction/memory.py`
- replace or wrap `LiveChordProgressionPredictor`
- add `dev/tests/test_live_online_sequence.py`
- add `dev/tests/test_live_phrase_recurrence.py`

#### Acceptance

- repeated 8- and 12-bar progressions become predictable after observation;
- transposed functional progressions share the same learned structure;
- long context backs off cleanly after a mismatch;
- one misdetected chord does not erase an otherwise strong recurrence;
- song-local evidence overtakes a conflicting weak generic prior;
- memory remains within its configured bounds.

### Phase E — Section, repeat, and mood-trajectory prediction

#### Goal

Predict musically meaningful macro events beyond the next chord.

#### Work

1. Build anonymous section fingerprints from phrases and momentum envelopes.
2. Maintain a bounded section-transition graph.
3. Predict section recurrence and likely successor.
4. Add conservative semantic probabilities for verse and chorus.
5. Combine recurrence, section order, cadence, phrase boundary, entrance
   harmony, and momentum for chorus prediction.
6. Predict energy, brightness, tension, density, and harmonic-stability
   trajectories over beat/bar horizons.
7. Distinguish a predicted trajectory from an observed mood change.

#### Suggested files

- add `src/dreamsync/prediction/section.py`
- add `src/dreamsync/prediction/momentum.py`
- add `src/dreamsync/prediction/engine.py`
- extend reactive diagnostics
- add `dev/tests/test_live_section_prediction.py`
- add `dev/tests/test_live_momentum_prediction.py`

#### Acceptance

- a returning known section is identified before or at its entrance when its
  prefix provides sufficient evidence;
- anonymous recurrence works even when semantic verse/chorus confidence is
  low;
- chorus predictions require multiple evidence families;
- no categorical mood claim is derived solely from chord major/minor quality;
- uncertainty rises correctly on new or irregular material.

### Phase F — Calibration, arbitration, and shadow-mode live validation

#### Goal

Produce trustworthy probabilities without yet allowing anticipatory optical
actions.

#### Work

1. Fuse prediction heads and alternatives into the unified event contract.
2. Calibrate each event type separately using development replay data.
3. Add abstention and minimum-evidence rules.
4. Resolve conflicting predictions targeting the same time.
5. Run all predictions in shadow mode during live operation.
6. Display predicted target, time, alternatives, evidence, and eventual
   outcome.
7. Compare predicted events to the existing observed live reactions.
8. Freeze thresholds before the first predictive-cue trial.

#### Suggested files

- add `src/dreamsync/prediction/calibration.py`
- add `src/dreamsync/prediction/arbitration.py`
- modify `src/dreamsync/prediction/engine.py`
- modify session snapshots and reactive GUI
- add `dev/tests/test_live_prediction_calibration.py`
- add `dev/tests/test_live_prediction_arbitration.py`
- extend GUI diagnostic tests

#### Acceptance

- probability calibration is reported by event type;
- alternatives sum to a valid distribution;
- low-confidence cases abstain;
- shadow mode cannot alter renderer/effect parameters;
- false high-impact prediction rates meet the threshold selected for cue
  rollout;
- live p95 latency and capture-drop behavior do not regress.

### Phase G — Cue policy and limited predictive lighting rollout

#### Goal

Use trustworthy predictions to prepare and schedule bounded visual actions.

#### Work

1. Implement the cue-policy state machine.
2. Add policy tables mapping musical events to visual intents.
3. Respect effect-bank enablement, color-profile selection, brightness limits,
   and cooldowns.
4. Begin with one low-risk cue class, such as a small predicted chord-change
   accent.
5. Add harmonic-resolution flash/color change only after its precision gate
   passes.
6. Add phrase/section actions incrementally.
7. Confirm, cancel, or degrade cues based on continuing evidence.
8. Keep observed-event fallback active.
9. Persist user predictive-cue settings.
10. Provide an immediate master toggle that disables predictive cues without
    disabling ordinary live reaction.

#### Suggested files

- add `src/dreamsync/prediction/cue_policy.py`
- add packaged cue-policy defaults
- modify `src/dreamsync/live.py`
- modify `src/dreamsync/director.py`
- modify `src/dreamsync/effects.py` only at an explicit visual-intent seam
- modify reactive settings and GUI
- add `dev/tests/test_live_predictive_cues.py`
- extend `dev/tests/test_live_responsiveness.py`
- extend GUI settings and effect-bank tests

#### Acceptance

- only enabled effects can be selected;
- no spatial setting is changed;
- scheduled cues execute against the live beat/downbeat clock;
- falling confidence cancels or degrades a cue before execution;
- duplicate/conflicting cues are arbitrated;
- renderer rate limits remain unchanged;
- predictive cueing can be disabled instantly;
- capture-only/no-audio-output tests pass;
- precompiled and pipeline show tests prove no behavior change.

### Phase H — Optional corpus-trained or neural predictor

#### Goal

Improve cold-start and unfamiliar-song prediction only if measured evidence
justifies added model complexity.

#### Entry gate

Do not begin this phase until the neural-model decision gate in this document
is satisfied.

#### Work

1. Freeze the event vocabulary and dataset licensing.
2. Train a corpus prior over functional chord, duration, phrase, section, and
   momentum tokens.
3. Compare:

   - count-based progression priors;
   - variable-order Markov baseline;
   - compact recurrent/event-token model;
   - compact transformer-style event-token model.

4. Evaluate cold start and post-recurrence separately.
5. Measure calibration, inference latency, memory, and false-cue cost.
6. Export/version the selected artifact only if it materially improves the
   held-out product metrics.
7. Keep the Phase D song-local model and observed-event behavior as fallback.

#### Acceptance

- material held-out improvement over the Phase F baseline;
- better cold-start prediction without worse post-recurrence performance;
- calibrated uncertainty;
- runtime performance within budget;
- no network or GPU requirement;
- corrupt/missing model artifact falls back safely;
- model version appears in every prediction log.

## Testing Matrix

### Unit tests

- key posterior normalization and stability;
- Roman-numeral mapping in all 24 major/minor keys;
- transposition invariance;
- borrowed/unknown chord handling;
- phrase hazard and competing lengths;
- cadence distributions including deceptive alternatives;
- variable-order backoff;
- recurrence matching with substitutions;
- section-transition updates;
- probability fusion and calibration;
- cue lifecycle transitions;
- cooldown, cancellation, and effect-bank restrictions.

### Synthetic sequences

- `vi | IV | V | I` in several keys;
- `ii | V | I`;
- `I | V | vi | IV`;
- deceptive `V | vi`;
- sustained dominant before delayed resolution;
- 8-, 12-, and 16-bar progressions;
- one chord per bar and multiple chords per bar;
- silent/no-chord bars;
- modulation;
- relative-major/minor ambiguity;
- odd phrase lengths;
- meter confidence loss and recovery;
- erroneous intermediate chord;
- chorus-like momentum rise without a section change;
- section change without an energy rise.

### Deterministic replay

- cold-start prediction;
- first recurrence;
- later recurrence;
- changed final chord;
- changed phrase length;
- changed section order;
- prediction cancellation;
- timing drift;
- input discontinuity;
- song-boundary reset.

### Integration tests

- prediction receives committed chord events, not raw unstable FFT labels;
- target time uses capture/audio time;
- cues use the normal live render path;
- GUI snapshots remain bounded;
- effect toggles and color-profile changes are respected immediately;
- ESC/popout/fullscreen diagnostics continue to behave correctly;
- predictive settings persist;
- no audio output object is created;
- pipeline/precompiled execution does not import or invoke live prediction.

### Manual live tests

1. Four-bar common progression with tonic resolution.
2. Eight-bar progression repeated three times.
3. Twelve-bar progression with turnaround.
4. Verse/chorus song with a returning verse.
5. Chorus entrance with clear energy rise.
6. Quiet chorus or section entrance without an energy rise.
7. Deceptive cadence.
8. Mid-song modulation.
9. Performance with missed or substituted chords.
10. Low-tonality/percussive material where the predictor should abstain.

For every manual test, record:

- leading hypotheses;
- prediction lead time;
- selected cue tier;
- whether the cue was armed, committed, cancelled, or degraded;
- observed musical outcome;
- perceptual judgment.

## Rollout Controls

Use independent flags:

```text
predictive_analysis_enabled
predictive_diagnostics_enabled
predictive_shadow_mode
predictive_cues_enabled
predictive_high_impact_cues_enabled
```

Recommended rollout:

1. replay only;
2. live diagnostics/shadow mode;
3. low-impact predicted chord cues;
4. harmonic-resolution cues;
5. phrase-boundary cues;
6. section-repeat and chorus cues;
7. mood-trajectory cues;
8. optional corpus/neural prior.

Each level must be independently reversible. Disabling predictive cueing must
leave observed reactive mode functioning.

## Failure Modes and Mitigations

| Failure | Mitigation |
|---|---|
| Incorrect chord contaminates long context | Probability-weighted tokens, bounded alternatives, robust recurrence matching, backoff |
| Relative major/minor flips | Top-K key beam, duration weighting, modulation hysteresis |
| V does not resolve to I | Preserve deceptive and hold alternatives; confidence-gated cueing |
| Every fourth bar is treated as a boundary | Competing duration hypotheses and boundary hazard, not a fixed modulo rule |
| Long progression is mistaken for several short loops | Bar-aware duration/context tokens and recurrence comparison across longer horizons |
| New material resembles a known verse prefix | Require increasing partial-match confidence and retain alternatives |
| Energy rise is mistaken for a chorus | Require structure, recurrence/order, harmony, and momentum evidence |
| Quiet chorus is missed | Do not require energy rise; use recurrence and section-order evidence |
| Modulation breaks functional memory | Retain key path and compare both functional and contour representations |
| Cue fires after prediction changes | Re-evaluate armed/scheduled cues until commit deadline |
| False high-impact cue is visually disruptive | Higher precision threshold, cooldown, intensity cap, degradation path |
| Inference misses deadline | Skip prediction cycle and retain observed reactive behavior |
| Model artifact unavailable | Deterministic baseline fallback |
| Live changes leak into compiled shows | Separate imports, feature flags, and explicit regression tests |
| Runtime accidentally emits audio | Capture-only architecture and construction-level no-output tests |

## Definition of Done

The predictive system is ready for general live use when:

1. key-relative chord sequences are stable enough for transposition-invariant
   recurrence;
2. 4-, 8-, 12-, and 16-bar progression cohorts are evaluated separately;
3. song-local prediction materially improves after a phrase/section repeats;
4. chord, resolution, phrase, and section probabilities are calibrated;
5. the model can abstain on ambiguous music;
6. high-impact cue precision meets a deliberately selected product threshold;
7. cue timing is locked to confident live beats/downbeats;
8. predictions never directly bypass the visual policy;
9. disabled effects and spatial settings remain untouched;
10. runtime latency, memory, and renderer cadence remain bounded;
11. predictive cueing can be disabled without disabling observed live reaction;
12. no audio output path exists;
13. precompiled and pipeline show behavior remains unchanged;
14. replay, integration, and manual live tests pass;
15. model/prior/cue-policy versions and explanations are visible in telemetry.

## Recommended First Implementation Slice

Proceed through Phases A–D before attempting anticipatory lighting.

That slice will deliver:

- causal replay and measurement;
- probabilistic key centers;
- Roman-numeral observations;
- variable phrase lengths;
- common progression/cadence priors;
- long-context song-local learning;
- reliable evaluation of 4-, 8-, and 12-bar behavior.

Then complete Phases E–F in shadow mode. Only after prediction accuracy,
calibration, latency, and false-cue cost are known should Phase G allow the
model to cue effects.

The first predictive lighting acceptance target is:

```text
In confident 4/4 meter, after observing vi -> IV -> V over a likely
four-bar phrase in a stable key, predict I at the next downbeat, arm a long
flash and color change, execute only while confidence remains above the
high-impact threshold, and record whether the actual chord and timing
confirmed the prediction.
```

Longer-progression acceptance targets must be evaluated alongside it so the
system does not optimize itself back into a four-bar-only predictor.
