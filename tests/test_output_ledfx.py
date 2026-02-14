import unittest

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.output.ledfx import LedFxConfig, LedFxOutputAdapter, MultiLedFxOutputAdapter
from dreamsync.output.roles import DeviceRole


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


class MultiLedFxOutputTests(unittest.TestCase):
    def _make_adapter(self, virtual_id: str, sent: list) -> LedFxOutputAdapter:
        def _transport(url: str, payload: dict, timeout: float) -> None:
            sent.append((virtual_id, url, payload))

        return LedFxOutputAdapter(
            LedFxConfig(base_url="http://127.0.0.1:8888", virtual_id=virtual_id),
            transport=_transport,
            monotonic_fn=lambda: 10.0,
        )

    def test_emit_fans_out_to_all_devices(self) -> None:
        sent: list[tuple] = []
        primary = self._make_adapter("strip1", sent)
        accent = self._make_adapter("strip2", sent)
        multi = MultiLedFxOutputAdapter([(primary, DeviceRole.PRIMARY), (accent, DeviceRole.ACCENT)])

        intent = LightingIntent(mode=EffectMode.MOTION, intensity=0.8, speed=0.7, bpm=130.0)
        result = multi.emit(0.0, intent)

        self.assertTrue(result)
        self.assertEqual(len(sent), 2)
        # Primary device gets original effect type (scroll for MOTION)
        self.assertEqual(sent[0][2]["type"], "scroll")
        # Accent device gets ambient effect type (magnitude for AMBIENT)
        self.assertEqual(sent[1][2]["type"], "magnitude")

    def test_emit_accent_has_reduced_intensity(self) -> None:
        sent: list[tuple] = []
        primary = self._make_adapter("strip1", sent)
        accent = self._make_adapter("strip2", sent)
        multi = MultiLedFxOutputAdapter([(primary, DeviceRole.PRIMARY), (accent, DeviceRole.ACCENT)])

        intent = LightingIntent(mode=EffectMode.PULSE, intensity=1.0, speed=0.5, bpm=120.0)
        multi.emit(0.0, intent)

        primary_brightness = sent[0][2]["config"]["brightness"]
        accent_brightness = sent[1][2]["config"]["brightness"]
        self.assertAlmostEqual(primary_brightness, 1.0, places=3)
        self.assertAlmostEqual(accent_brightness, 0.6, places=3)

    def test_clear_effect_clears_all_devices(self) -> None:
        cleared: list[str] = []

        def _make_clearing_adapter(virtual_id: str) -> LedFxOutputAdapter:
            def _delete(url: str, timeout: float) -> None:
                cleared.append(virtual_id)
            return LedFxOutputAdapter(
                LedFxConfig(base_url="http://127.0.0.1:8888", virtual_id=virtual_id),
                transport=lambda u, p, t: None,
                delete_transport=_delete,
            )

        a1 = _make_clearing_adapter("strip1")
        a2 = _make_clearing_adapter("strip2")
        multi = MultiLedFxOutputAdapter([(a1, DeviceRole.PRIMARY), (a2, DeviceRole.ACCENT)])
        multi.clear_effect()

        self.assertEqual(cleared, ["strip1", "strip2"])
