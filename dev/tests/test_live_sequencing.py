import unittest

from dreamsync.director import Director, DirectorConfig, EffectMode, LightingIntent
from dreamsync.live import LiveBeatSequencer
from dreamsync.render import RenderMode, SegmentRenderer


class LiveBeatSequencerTests(unittest.TestCase):
    def test_four_four_marks_one_bar_accent_and_three_soft_beats(self) -> None:
        sequencer = LiveBeatSequencer()
        accents = [sequencer.update(True) for _ in range(8)]

        self.assertEqual([accent.beat_in_bar for accent in accents], [1, 2, 3, 4, 1, 2, 3, 4])
        self.assertEqual([accent.downbeat for accent in accents], [True, False, False, False] * 2)
        self.assertEqual([accent.strength for accent in accents], [1.0, 0.35, 0.35, 0.35] * 2)

    def test_reset_starts_a_new_bar(self) -> None:
        sequencer = LiveBeatSequencer()
        sequencer.update(True)
        sequencer.update(True)
        sequencer.reset()

        accent = sequencer.update(True)

        self.assertTrue(accent.downbeat)
        self.assertEqual(accent.beat_in_bar, 1)


class LiveSequencingIntegrationTests(unittest.TestCase):
    def test_director_changes_color_on_downbeats_only(self) -> None:
        colors = ("#ff0000", "#00ff00", "#0000ff")
        director = Director(DirectorConfig(colors=colors))
        sequencer = LiveBeatSequencer()
        observed = []

        for i in range(5):
            accent = sequencer.update(True)
            intent = director.update({
                "t": i * 0.5,
                "rms": 0.1,
                "zcr": 0.05,
                "bpm": 120.0,
                "beat": True,
                "downbeat": accent.downbeat,
            })
            observed.append(intent.color)

        self.assertEqual(observed, ["#00ff00", "#00ff00", "#00ff00", "#00ff00", "#0000ff"])

    def test_secondary_pulse_beat_is_a_soft_accent(self) -> None:
        renderer = SegmentRenderer(segments=1, mode=RenderMode.PULSE)
        intent = LightingIntent(
            mode=EffectMode.PULSE,
            intensity=1.0,
            speed=0.5,
            bpm=120.0,
            color="#ff0000",
        )

        renderer.render(1.0, intent, beat=True, params={"beat_accent": 1.0})
        soft_frame = renderer.render(1.5, intent, beat=True, params={"beat_accent": 0.35})
        downbeat_frame = renderer.render(2.0, intent, beat=True, params={"beat_accent": 1.0})

        self.assertGreater(soft_frame[0][0], 80)
        self.assertLess(soft_frame[0][0], 100)
        self.assertEqual(downbeat_frame[0], (255, 0, 0))


if __name__ == "__main__":
    unittest.main()
