# Section Labeling Improvements

## Current State (2026-03-08)

The section labeler uses rank-based energy splitting to assign verse/chorus labels.
This works well enough for light show variety, but has a known limitation: when
sections have similar energy levels (e.g., 0.92 vs 0.96 vs 1.00), the assignment
is essentially arbitrary because the analyzer lacks signal to distinguish them.

The structural ID clustering (`_assign_structural_ids`) also groups nearly all
sections under the same ID ("A") because cosine similarity on the 5 spectral
features (centroid, bass_ratio, spectral_flux, rms, onset_strength) is
consistently high between sections. In real music, verse and chorus differ more
in melody, harmony, and lyrics than in raw spectral energy.

## Option 2 — Improve Clustering

**Goal:** Make structural IDs actually differentiate sections so labels propagate
more meaningfully (e.g., all "A" sections = verse, all "B" = chorus).

**Changes:**
- Raise `similarity_threshold` from 0.7 to 0.85+ so that sections need to be
  more similar to share an ID
- Switch from cosine similarity to Euclidean distance, which is more sensitive
  to magnitude differences (cosine only measures direction)
- Normalize section vectors before comparison so scale differences in features
  don't dominate

**Limitations:** Won't solve the fundamental problem when sections genuinely
sound similar in these features. Only helps when there are real timbral
differences that are currently being collapsed by the permissive threshold.

**Files:** `src/dreamsync/analyzer/sections.py` — `_assign_structural_ids()`,
`_cosine_sim()`, `_section_vector()`

## Option 3 — Add Richer Features

**Goal:** Capture harmonic and tonal differences between sections that
energy-based spectral features miss.

**Changes:**
- Add MFCC (Mel-frequency cepstral coefficients) to `FeatureRow` — these encode
  timbral "texture" and are the standard for distinguishing speech/music segments
- Add chroma features — these capture pitch class distribution (which notes are
  present), directly encoding harmonic content. Verse and chorus often use
  different chord progressions that chroma features would reveal
- Update `_build_feature_matrix()` and `feature_weights` to include the new
  dimensions
- Retune clustering threshold after adding new features

**Dependencies:** librosa already computes MFCCs and chroma. The main work is
threading them through `FeatureRow`, the feature extractor, and serialization.

**Scope:** Medium — touches the analyzer pipeline (`features.py`, `sections.py`,
`models.py`) and requires re-analyzing all songs to regenerate `.analysis.json`.
Existing tests would need updated fixtures.
