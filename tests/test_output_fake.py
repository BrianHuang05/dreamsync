import unittest

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.output.fake import FakeOutputAdapter


class FakeOutputTests(unittest.TestCase):
    def test_emit_records_intent(self) -> None:
        out = FakeOutputAdapter()
        out.emit(
            1.25,
            LightingIntent(mode=EffectMode.PULSE, intensity=0.4, speed=0.6, bpm=120.0),
        )
        self.assertEqual(len(out.records), 1)
        rec = out.records[0]
        self.assertEqual(rec.mode, "pulse")
        self.assertEqual(rec.t, 1.25)
