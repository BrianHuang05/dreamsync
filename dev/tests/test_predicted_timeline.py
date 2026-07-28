from __future__ import annotations

import unittest

from dreamsync.show.models import ShowCue, ShowTimeline
from dreamsync.show.predicted_timeline import build_predicted_beat_timeline


def _reference_timeline() -> ShowTimeline:
    return ShowTimeline(
        song_path="song.mp3",
        duration=2.0,
        bpm=100.0,
        time_signature=4,
        beat_times=(0.0, 0.6, 1.2, 1.8),
        downbeat_times=(0.0,),
        cues=(
            ShowCue(
                t=0.0,
                render_mode="pulse",
                color_palette=("#ff0000", "#0000ff"),
                intensity=0.7,
                speed=1.0,
                params={},
                transition="cut",
                transition_beats=0,
            ),
        ),
        metadata={"track_name": "Reference"},
    )


class PredictedTimelineTests(unittest.TestCase):
    def test_replaces_truth_grid_and_synthesizes_bar_phase(self):
        timeline = build_predicted_beat_timeline(
            _reference_timeline(),
            (-0.1, 0.1, 0.6, 1.1, 1.6, 2.1),
            decoder_metadata={"decoder_track_id": "synthetic"},
        )

        self.assertEqual(timeline.beat_times, (0.1, 0.6, 1.1, 1.6))
        self.assertEqual(timeline.downbeat_times, (0.1,))
        self.assertAlmostEqual(timeline.bpm, 120.0)
        self.assertEqual(timeline.cues, _reference_timeline().cues)
        self.assertEqual(timeline.metadata["beat_grid_source"], "stable_grid_decoder_csv")
        self.assertIn("does not predict downbeats", timeline.metadata["downbeat_source"])
        self.assertIn("reference beat and downbeat timestamps were discarded", timeline.metadata["cue_source"])
        self.assertEqual(timeline.metadata["decoder_track_id"], "synthetic")

    def test_rejects_nonmonotonic_decoder_timestamps(self):
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            build_predicted_beat_timeline(_reference_timeline(), (0.1, 0.6, 0.5, 1.1))
