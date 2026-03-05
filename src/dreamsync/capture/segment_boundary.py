"""Compute frame-level segment boundaries from song timing data."""

from __future__ import annotations


def compute_boundaries(
    song_durations: list[float],
    current_playback_time: float,
    sample_rate: int = 48000,
) -> list[int]:
    """Convert song durations and playback position to cumulative frame boundaries.

    Parameters
    ----------
    song_durations:
        List of song durations in seconds.  ``song_durations[0]`` is the
        currently playing song; the rest are upcoming songs.
    current_playback_time:
        Seconds elapsed into the first (currently playing) song.
    sample_rate:
        Audio sample rate in Hz.

    Returns
    -------
    List of cumulative frame positions where segment splits should occur.

    Example
    -------
    >>> compute_boundaries([30, 210, 180], 15.0, 48000)
    [720000, 10800000, 19440000]
    """
    if not song_durations:
        return []

    remaining = max(song_durations[0] - current_playback_time, 0.0)
    segment_durations = [remaining] + list(song_durations[1:])
    segment_frames = [round(d * sample_rate) for d in segment_durations]

    boundaries: list[int] = []
    cumulative = 0
    for frames in segment_frames:
        cumulative += frames
        boundaries.append(cumulative)
    return boundaries
