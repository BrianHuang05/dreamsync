"""Build playable show timelines from externally predicted beat timestamps.

The stable grid decoder is intentionally label-free and presently predicts
beats, not downbeats or lighting treatments.  This module lets an offline CSV
replay use its predicted beat times in the normal show playback runtime while
retaining a separately supplied set of visual cues for an apples-to-apples
lighting comparison.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import statistics
from typing import Any

from dreamsync.show.models import ShowTimeline


def build_predicted_beat_timeline(
    reference_timeline: ShowTimeline,
    beat_times: Sequence[float],
    *,
    decoder_metadata: Mapping[str, Any] | None = None,
) -> ShowTimeline:
    """Return a playable timeline whose beat grid comes solely from *beat_times*.

    ``ShowTimeline`` has no time-varying BPM field, so its global BPM is the
    median predicted inter-beat BPM.  Actual beat-reactive behaviour is driven
    by the supplied timestamps, which preserves the decoder's rolling clock
    behaviour at playback time.  The decoder does not currently predict bar
    phase, so a clearly labelled synthetic downbeat is created every 3 or 4
    predicted beats, beginning with the first in-range prediction.
    """

    predicted_beats = _validated_in_range_beats(beat_times, reference_timeline.duration)
    if len(predicted_beats) < 2:
        raise ValueError("At least two in-range predicted beats are required to build a show timeline")

    intervals = [later - earlier for earlier, later in zip(predicted_beats, predicted_beats[1:])]
    bpm = 60.0 / statistics.median(intervals)
    downbeats = predicted_beats[:: reference_timeline.time_signature]

    metadata = dict(reference_timeline.metadata)
    metadata.update(
        {
            "beat_grid_source": "stable_grid_decoder_csv",
            "beat_grid_playback": "timestamp_replay_on_show_clock",
            "beat_count": len(predicted_beats),
            "downbeat_source": (
                "synthetic_bar_phase: every "
                f"{reference_timeline.time_signature} predicted beats from first in-range prediction; "
                "the stable grid decoder does not predict downbeats"
            ),
            "cue_source": (
                "reference timeline cues retained for beat-grid visual comparison; "
                "reference beat and downbeat timestamps were discarded"
            ),
        }
    )
    if decoder_metadata:
        metadata.update(dict(decoder_metadata))

    return ShowTimeline(
        song_path=reference_timeline.song_path,
        duration=reference_timeline.duration,
        bpm=bpm,
        time_signature=reference_timeline.time_signature,
        beat_times=predicted_beats,
        downbeat_times=downbeats,
        cues=reference_timeline.cues,
        metadata=metadata,
    )


def _validated_in_range_beats(beat_times: Sequence[float], duration: float) -> tuple[float, ...]:
    """Filter analysis-edge timestamps and reject malformed decoder output."""

    values: list[float] = []
    previous = -math.inf
    for raw_time in beat_times:
        t = float(raw_time)
        if not math.isfinite(t):
            raise ValueError(f"Predicted beat time must be finite, got {raw_time!r}")
        if t < 0.0 or t > duration:
            continue
        if t <= previous:
            raise ValueError("Predicted beat timestamps must be strictly increasing")
        values.append(t)
        previous = t
    return tuple(values)
