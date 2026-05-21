"""Orchestrator — analyze_song() wires decode → features → bpm → sections → SongStructure."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from dreamsync.analyzer.bpm import GlobalBpmEstimator
from dreamsync.analyzer.decode import decode_mp3
from dreamsync.analyzer.features import OfflineFeaturePipeline
from dreamsync.analyzer.instruments import InstrumentHeuristicAnalyzer
from dreamsync.analyzer.models import SongStructure
from dreamsync.analyzer.phrases import InstrumentEventDetector, PhraseSegmenter
from dreamsync.analyzer.separation import StemSeparationBackend
from dreamsync.analyzer.sections import SectionSegmenter


def analyze_song(
    mp3_path: Path,
    *,
    sample_rate: int = 44100,
    frame_size: int = 2048,
    hop_size: int = 512,
    metadata: dict | None = None,
    stem_backend: StemSeparationBackend | None = None,
) -> SongStructure:
    """Full analysis pipeline: mp3 → SongStructure.

    Steps:
    1. Decode mp3 → PCM
    2. Run offline feature pipeline
    3. Estimate global BPM + beat grid
    4. Segment into sections
    5. Detect phrases and instrument events
    6. Return SongStructure
    """
    mp3_path = Path(mp3_path)

    # 1. Decode
    audio = decode_mp3(mp3_path, target_sr=sample_rate)
    stem_artifacts = stem_backend.separate(mp3_path, audio) if stem_backend is not None else {}

    # 2. Extract features
    pipeline = OfflineFeaturePipeline(
        sample_rate=sample_rate, frame_size=frame_size, hop_size=hop_size,
    )
    analysis_signal = getattr(audio, "stereo_signal", None)
    if not isinstance(analysis_signal, np.ndarray):
        analysis_signal = audio.signal
    features = pipeline.extract(analysis_signal)

    # 3. Global BPM
    bpm_estimator = GlobalBpmEstimator()
    bpm, tempo_regions, beat_grid = bpm_estimator.estimate(
        features, sample_rate=sample_rate, hop_size=hop_size,
    )

    # 4. Sections
    segmenter = SectionSegmenter()
    sections = segmenter.segment(features, beat_grid, tempo_regions)

    # 5. Phrases and instrument events
    phrase_segmenter = PhraseSegmenter()
    phrases = phrase_segmenter.segment(sections, features, beat_grid)

    event_detector = InstrumentEventDetector()
    instrument_events = event_detector.detect(phrases, features)
    instrument_proxies = InstrumentHeuristicAnalyzer().analyze(phrases, features)

    # 6. Assemble
    result_metadata = dict(metadata or {})
    if stem_backend is not None:
        result_metadata["stem_backend"] = stem_backend.backend_name
    if stem_artifacts:
        result_metadata["available_stems"] = sorted(stem_artifacts)

    return SongStructure(
        path=str(mp3_path),
        duration=audio.duration,
        bpm=bpm,
        time_signature=beat_grid.time_signature,
        beat_grid=beat_grid,
        tempo_regions=tuple(tempo_regions),
        sections=tuple(sections),
        metadata=result_metadata,
        phrases=tuple(phrases),
        instrument_events=tuple(instrument_events),
        instrument_proxies=tuple(instrument_proxies),
    )
