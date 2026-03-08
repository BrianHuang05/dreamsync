# Richer Section Features — Detailed Implementation Plan

## Background

The section labeler assigns verse/chorus labels via rank-based energy splitting, and
structural ID clustering (`_assign_structural_ids`) groups sections by cosine
similarity on 5 spectral features: `centroid`, `bass_ratio`, `spectral_flux`, `rms`,
`onset_strength`. In practice, nearly all sections cluster under ID "A" because these
energy/spectral features are too similar across verse and chorus — the real
differences live in harmony, melody, and timbre, which these features don't capture.

This plan adds **MFCC** and **chroma** features to the pipeline to give the
clustering algorithm enough signal to distinguish genuinely different sections.

### Current data flow

```
decode_mp3 → OfflineFeaturePipeline.extract() → list[FeatureRow]
                                                      ↓
                              SectionSegmenter._build_feature_matrix()
                                                      ↓
                              _self_similarity → novelty → boundaries
                                                      ↓
                              _section_vector → _assign_structural_ids → Section.section_id
```

### Key constraint

librosa is **not** a current dependency. All DSP is custom numpy + FFT. This plan
adds MFCC and chroma computation using numpy only (no new dependencies), keeping the
project's zero-heavy-dependency posture. If librosa is later added for other reasons,
these can be swapped to librosa calls.

---

## Deliverable 1 — Compute MFCC and Chroma per Frame

### Prerequisites
- Familiarity with `src/dreamsync/analyzer/features.py` (`FeatureRow`, `OfflineFeaturePipeline`)
- Familiarity with `src/dreamsync/live.py` (`_spectral_features`, `_prepare_bass_window`)
- Understanding of mel filterbanks and chroma bins (12 pitch classes)

### Goal
Add frame-level MFCC (13 coefficients) and chroma (12 pitch-class bins) computation
to `OfflineFeaturePipeline.extract()`, and store them in `FeatureRow`.

### Outline

1. **Add fields to `FeatureRow`** (`features.py:22-35`)
   - `mfcc: tuple[float, ...]` — 13 MFCC coefficients (tuple for frozen dataclass)
   - `chroma: tuple[float, ...]` — 12 chroma bin energies

2. **Implement `_mel_filterbank()`** (new helper in `features.py` or `live.py`)
   - Build a mel-scale triangular filterbank matrix: `(n_mels, n_fft//2+1)`
   - Parameters: `n_mels=40`, `fmin=20`, `fmax=sr/2`, `n_fft=2048`, `sr=44100`
   - Use the standard mel ↔ Hz conversions: `mel = 2595 * log10(1 + f/700)`
   - Cache the filterbank matrix — it only depends on FFT size and sample rate

3. **Implement `_compute_mfcc()`** (new helper)
   - Input: magnitude spectrum `|FFT|` (from `_spectral_features().mag`)
   - Apply mel filterbank → mel-scale power spectrum
   - Log-compress: `log(mel_power + 1e-10)`
   - DCT (type-II) on log mel spectrum → 13 MFCC coefficients
   - DCT can be implemented as matrix multiply with precomputed cosine basis
   - Return `tuple[float, ...]` of length 13

4. **Implement `_compute_chroma()`** (new helper)
   - Input: magnitude spectrum, frequency array (`freqs` from `_prepare_bass_window`)
   - Map each FFT bin to its nearest pitch class: `pitch_class = round(12 * log2(f / 440)) % 12`
   - Sum bin magnitudes into 12 chroma bins
   - Normalize: divide by max bin (or L2 norm) to get relative pitch profile
   - Return `tuple[float, ...]` of length 12

5. **Wire into `OfflineFeaturePipeline.extract()`** (`features.py:55-163`)
   - After `_spectral_features()` call (line 100), compute MFCC and chroma from `sf.mag`
   - Pre-compute mel filterbank and DCT matrix once before the frame loop (after line 65)
   - Pass results into `FeatureRow` constructor (lines 145-158)

### Testing

- **Unit tests for `_mel_filterbank()`:**
  - Shape: `(n_mels, n_fft//2+1)` = `(40, 1025)`
  - Rows sum to ≤ 1.0 (triangular filters)
  - Filters span from fmin to fmax in mel space

- **Unit tests for `_compute_mfcc()`:**
  - Returns tuple of length 13
  - Silent input (zeros) → all MFCCs near zero
  - White noise → non-trivial first coefficient, diminishing higher coefficients
  - Deterministic: same input → same output

- **Unit tests for `_compute_chroma()`:**
  - Returns tuple of length 12
  - Pure sine at 440 Hz → peak at pitch class A (index 0 or 9 depending on reference)
  - Silent input → all zeros or uniform
  - Normalized: max bin ≈ 1.0

- **Integration test for `OfflineFeaturePipeline`:**
  - Run on synthetic sine wave, verify `FeatureRow.mfcc` and `.chroma` are populated
  - Verify tuple lengths (13 and 12)
  - Verify existing fields are unchanged (regression)

### Completion Criteria
- [ ] `FeatureRow` has `mfcc` and `chroma` fields
- [ ] `_mel_filterbank()`, `_compute_mfcc()`, `_compute_chroma()` implemented with numpy only
- [ ] `OfflineFeaturePipeline.extract()` populates both fields per frame
- [ ] All new unit tests pass
- [ ] All existing `dev/tests/test_analyzer_features.py` tests pass (regression)
- [ ] All existing `tests/` tests pass

---

## Deliverable 2 — Update Section Clustering to Use New Features

### Prerequisites
- Deliverable 1 complete (FeatureRow has mfcc/chroma)

### Goal
Update `SectionSegmenter` to incorporate MFCC and chroma features into the
self-similarity matrix and structural ID clustering, so that sections with different
harmonic/timbral content get different IDs.

### Outline

1. **Update `_build_feature_matrix()`** (`sections.py:110-134`)
   - Expand the matrix columns to include MFCC (13 dims) and chroma (12 dims)
   - New matrix shape: `(n_frames, 5 + 13 + 12)` = `(n_frames, 30)`
   - Extract MFCC/chroma from FeatureRow using the tuple fields

2. **Update default `feature_weights`** (`sections.py:39-45`)
   - Add weight entries for each MFCC and chroma dimension
   - Suggested initial weights:
     ```python
     # Original spectral features (reduced weight, now 40% total)
     "centroid": 0.10,
     "bass_ratio": 0.10,
     "spectral_flux": 0.10,
     "rms": 0.05,
     "onset_strength": 0.05,
     # MFCC features (30% total, spread across 13 coefficients)
     # mfcc_0 through mfcc_12
     # Chroma features (30% total, spread across 12 bins)
     # chroma_0 through chroma_11
     ```
   - Alternative approach: use feature group weights instead of per-dimension weights:
     ```python
     feature_group_weights = {
         "spectral": 0.4,   # original 5 features
         "mfcc": 0.3,       # 13 MFCC coefficients
         "chroma": 0.3,     # 12 chroma bins
     }
     ```
     Then normalize within each group. This is cleaner and easier to tune.

3. **Update `_section_vector()`** (`sections.py:238-247`)
   - Must now average MFCC and chroma tuples across frames in a section
   - Compute mean MFCC vector (13-dim) and mean chroma vector (12-dim) per section
   - Concatenate with existing 5-dim spectral means → 30-dim section vector

4. **Retune `similarity_threshold`** (`sections.py:38`)
   - With 30 dimensions instead of 5, cosine similarity values will shift
   - Start with `similarity_threshold=0.85` (up from 0.7) — the richer feature
     space means genuinely similar sections will still score high, but dissimilar
     sections will now score lower
   - This may need empirical tuning in Deliverable 4

### Testing

- **Unit test: `_build_feature_matrix()` shape**
  - With MFCC/chroma features, matrix should have 30 columns
  - Normalization still produces values in [0, 1] per column

- **Unit test: `_section_vector()` length**
  - Returns 30-dim vector (was 5-dim)

- **Unit test: structural ID differentiation**
  - Create synthetic features where two section types have identical energy/spectral
    profiles but different MFCC/chroma values
  - Verify they get different structural IDs (this was impossible before)

- **Regression test: existing section tests still pass**
  - Update `_make_feature_row()` helper in `dev/tests/test_analyzer_sections.py`
    to include default `mfcc` and `chroma` tuple values
  - All existing tests in `TestSectionSegmenterBasic`, `TestSectionSegmenterBoundaries`,
    `TestSectionSegmenterLabelling`, `TestSectionSegmenterStructuralIDs`,
    `TestSectionSegmenterEdgeCases` must still pass

### Completion Criteria
- [ ] `_build_feature_matrix()` produces 30-column matrix
- [ ] `_section_vector()` returns 30-dim vectors
- [ ] `feature_weights` include MFCC and chroma dimensions
- [ ] `similarity_threshold` raised to 0.85
- [ ] New test proving MFCC/chroma enable section differentiation
- [ ] All existing `dev/tests/test_analyzer_sections.py` tests pass (with updated fixtures)
- [ ] All existing `tests/` tests pass

---

## Deliverable 3 — Update Test Fixtures and Serialization

### Prerequisites
- Deliverable 1 complete (FeatureRow schema change)

### Goal
Update all test helpers, fixtures, and any serialization paths that reference
`FeatureRow` fields so nothing breaks from the schema change.

### Outline

1. **Update `_make_feature_row()` helper** (`dev/tests/test_analyzer_sections.py:17-34`)
   - Add `mfcc` and `chroma` parameters with sensible defaults:
     ```python
     mfcc: tuple[float, ...] = (0.0,) * 13,
     chroma: tuple[float, ...] = (1/12,) * 12,  # uniform chroma
     ```

2. **Update `_make_structured_features()` helper** (`dev/tests/test_analyzer_sections.py:37-56`)
   - Generate MFCC/chroma values that vary by section profile
   - Higher-energy sections should have different timbral signatures
   - Example: verse sections get one MFCC pattern, chorus sections get another

3. **Search for all `FeatureRow(` constructor calls** across the codebase
   - Any direct construction must now include `mfcc=` and `chroma=` kwargs
   - Files to check:
     - `dev/tests/test_analyzer_features.py`
     - `dev/tests/test_features.py`
     - `dev/tests/test_analyzer_sections.py`
     - `dev/tests/test_analyzer_models.py`
     - Any other test or production code constructing FeatureRow

4. **Check serialization**
   - `FeatureRow` is **not** serialized to `.analysis.json` (only Section-level
     aggregates are stored) — so no JSON schema changes needed
   - Verify `SongStructure.to_dict()` / `from_dict()` are unaffected (they should be,
     since they only reference `Section`, not `FeatureRow`)

5. **Verify the live pipeline** (`src/dreamsync/live.py`)
   - `FeatureRow` is used in `OfflineFeaturePipeline` but the live path
     (`run_live_to_govee`) uses raw dicts, not `FeatureRow` — confirm this
   - If any live code path constructs `FeatureRow`, it needs updating too

### Testing

- Run full test suites:
  ```
  python -m pytest tests/ -v
  python -m pytest dev/tests/ -v
  ```
- Zero failures from missing `mfcc`/`chroma` arguments
- Zero failures from changed FeatureRow shape

### Completion Criteria
- [ ] All `FeatureRow()` constructor calls include `mfcc` and `chroma`
- [ ] No live code paths broken
- [ ] `.analysis.json` format unchanged (no migration needed)
- [ ] `python -m pytest tests/ -v` passes (569 tests)
- [ ] `python -m pytest dev/tests/ -v` passes (315+ tests)

---

## Deliverable 4 — Empirical Validation and Threshold Tuning

### Prerequisites
- Deliverables 1-3 complete
- At least 3-5 analyzed songs with `.analysis.json` output available

### Goal
Validate that the richer features actually improve section differentiation on real
music, and tune the clustering threshold for best results.

### Outline

1. **Re-analyze existing test songs**
   - Run `dreamsync analyze` on 3-5 songs with known verse/chorus structure
   - Songs should span genres (pop, EDM, rock) to test generalization
   - Save before/after `.analysis.json` for comparison

2. **Compare structural IDs before and after**
   - Before: expect most sections clustered under "A"
   - After: expect at least 2 distinct IDs (A and B) for songs with clear verse/chorus
   - Document which songs improved and which didn't change

3. **Tune `similarity_threshold`**
   - Test values: 0.80, 0.85, 0.90, 0.95
   - For each threshold, count:
     - Number of distinct structural IDs per song
     - Whether verse sections share an ID and chorus sections share an ID
   - Pick the threshold that best balances specificity (different sections get
     different IDs) vs. consistency (same section type gets same ID)
   - Update default in `SectionSegmenter.__init__`

4. **Tune feature group weights**
   - If chroma dominates too much (e.g., key changes within a section cause
     over-splitting), reduce chroma weight
   - If MFCC doesn't help much, reduce its weight and lean more on chroma
   - Document final weight ratios

5. **Spot-check label quality**
   - With better structural IDs, does `_label_sections()` step 5 ("structural ID
     consistency") now propagate labels more usefully?
   - Compare label sequences before/after for 2-3 songs

### Testing

- **Validation script** (ad-hoc, not committed): analyze N songs, print section
  summary table showing `(start_t, end_t, section_id, label, energy_mean)` for each
- **Sanity checks:**
  - No song should produce fewer sections than before
  - No song should produce more than 10 distinct structural IDs (over-splitting)
  - All labels should still be from the valid set

### Completion Criteria
- [ ] At least 3 songs re-analyzed with new features
- [ ] Before/after comparison documented in `dev/test*` notes
- [ ] Final `similarity_threshold` chosen and set
- [ ] Final `feature_group_weights` chosen and set
- [ ] No regressions in section count or label validity
- [ ] Results documented in `dev/plans/section-labeling-improvements.md` (update existing file)

---

## Summary of Files Touched

| File | Changes |
|------|---------|
| `src/dreamsync/analyzer/features.py` | Add `mfcc`/`chroma` to `FeatureRow`; add `_mel_filterbank()`, `_compute_mfcc()`, `_compute_chroma()`; update `extract()` |
| `src/dreamsync/analyzer/sections.py` | Update `feature_weights`, `_build_feature_matrix()`, `_section_vector()`, `similarity_threshold` |
| `dev/tests/test_analyzer_sections.py` | Update `_make_feature_row()`, `_make_structured_features()` helpers |
| `dev/tests/test_analyzer_features.py` | Add MFCC/chroma unit tests; update any FeatureRow constructors |
| `dev/tests/test_features.py` | Update FeatureRow constructors if present |
| `dev/plans/section-labeling-improvements.md` | Document final tuning results |

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Numpy-only MFCC/chroma are slower than librosa | Longer analysis time per song | Profile; mel filterbank and DCT matrix are precomputed and reused, per-frame cost is just two matrix multiplies |
| Chroma is noisy for non-tonal music (EDM, percussion-heavy) | Over-splitting or no improvement | Weight chroma lower; fallback to spectral-only if all chroma bins are near-uniform |
| 30-dim vectors change cosine similarity distribution | Threshold from D2 might not generalize | Deliverable 4 exists specifically for empirical tuning |
| FeatureRow schema change breaks unknown callers | Runtime errors | Deliverable 3 does exhaustive search; frozen dataclass will fail loudly on missing args |
