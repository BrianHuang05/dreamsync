"""Orchestrator — analyze_song() wires decode → features → bpm → sections → SongStructure."""

from __future__ import annotations

from pathlib import Path

from dreamsync.analyzer.bpm import GlobalBpmEstimator
from dreamsync.analyzer.decode import decode_mp3
from dreamsync.analyzer.features import OfflineFeaturePipeline
from dreamsync.analyzer.models import SongStructure
from dreamsync.analyzer.sections import SectionSegmenter


def analyze_song(
    mp3_path: Path,
    *,
    sample_rate: int = 44100,
    frame_size: int = 2048,
    hop_size: int = 512,
    metadata: dict | None = None,
) -> SongStructure:
    """Full analysis pipeline: mp3 → SongStructure.

    Steps:
    1. Decode mp3 → PCM
    2. Run offline feature pipeline
    3. Estimate global BPM + beat grid
    4. Segment into sections
    5. Return SongStructure
    """
    mp3_path = Path(mp3_path)

    # 1. Decode
    audio = decode_mp3(mp3_path, target_sr=sample_rate)

    # 2. Extract features
    pipeline = OfflineFeaturePipeline(
        sample_rate=sample_rate, frame_size=frame_size, hop_size=hop_size,
    )
    features = pipeline.extract(audio.signal)

    # 3. Global BPM
    bpm_estimator = GlobalBpmEstimator()
    bpm, tempo_regions, beat_grid = bpm_estimator.estimate(
        features, sample_rate=sample_rate, hop_size=hop_size,
    )

    # 4. Sections
    segmenter = SectionSegmenter()
    sections = segmenter.segment(features, beat_grid, tempo_regions)

    # 5. Assemble
    return SongStructure(
        path=str(mp3_path),
        duration=audio.duration,
        bpm=bpm,
        time_signature=beat_grid.time_signature,
        beat_grid=beat_grid,
        tempo_regions=tuple(tempo_regions),
        sections=tuple(sections),
        metadata=metadata or {},
    )
