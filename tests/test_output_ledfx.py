import unittest

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.output.ledfx import LedFxConfig, LedFxOutputAdapter


class LedFxOutputTests(unittest.TestCase):
    def test_emit_sends_payload_to_expected_endpoint(self) -> None:
        sent: list[tuple[str, dict, float]] = []

        def _transport(url: str, payload: dict, timeout: float) -> None:
            sent.append((url, payload, timeout))

        adapter = LedFxOutputAdapter(
            LedFxConfig(base_url="http://127.0.0.1:8888", virtual_id="abc"),
            transport=_transport,
            monotonic_fn=lambda: 10.0,
        )
        did_send = adapter.emit(
            0.0,
            LightingIntent(mode=EffectMode.PULSE, intensity=0.5, speed=0.6, bpm=120.0),
        )

        self.assertTrue(did_send)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "http://127.0.0.1:8888/api/virtuals/abc/effects")
        self.assertEqual(sent[0][1]["type"], "energy")

    def test_emit_rate_limits_and_dedupes(self) -> None:
        sent: list[tuple[str, dict, float]] = []
        now = {"t": 0.0}

        def _transport(url: str, payload: dict, timeout: float) -> None:
            sent.append((url, payload, timeout))

        def _monotonic() -> float:
            return now["t"]

        adapter = LedFxOutputAdapter(
            LedFxConfig(
                base_url="http://127.0.0.1:8888",
                virtual_id="abc",
                min_update_interval_seconds=0.5,
            ),
            transport=_transport,
            monotonic_fn=_monotonic,
        )
        intent_a = LightingIntent(mode=EffectMode.AMBIENT, intensity=0.2, speed=0.2, bpm=90.0)
        intent_b = LightingIntent(mode=EffectMode.MOTION, intensity=0.8, speed=0.9, bpm=130.0)

        self.assertTrue(adapter.emit(0.0, intent_a))
        now["t"] = 0.1
        self.assertFalse(adapter.emit(0.1, intent_b))  # rate-limited
        now["t"] = 0.7
        self.assertFalse(adapter.emit(0.7, intent_a))  # deduped payload
        self.assertTrue(adapter.emit(0.7, intent_b))   # new payload, interval satisfied
        self.assertEqual(len(sent), 2)
