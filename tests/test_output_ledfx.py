import unittest

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.output.ledfx import LedFxConfig, LedFxOutputAdapter, MultiLedFxOutputAdapter, _BAND_COLOR_EFFECTS, _darken_hex
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


class LedFxPutVsPostTests(unittest.TestCase):
    def _make_adapter(self, posted: list, put: list, clock: dict | None = None):
        if clock is None:
            clock = {"t": 0.0}

        def _post(url: str, payload: dict, timeout: float) -> None:
            posted.append((url, payload))

        def _put(url: str, payload: dict, timeout: float) -> None:
            put.append((url, payload))

        def _monotonic() -> float:
            clock["t"] += 1.0
            return clock["t"]

        return LedFxOutputAdapter(
            LedFxConfig(base_url="http://127.0.0.1:8888", virtual_id="v1"),
            transport=_post,
            put_transport=_put,
            delete_transport=lambda u, t: None,
            monotonic_fn=_monotonic,
        )

    def test_first_emit_uses_post(self) -> None:
        posted: list = []
        put: list = []
        adapter = self._make_adapter(posted, put)
        intent = LightingIntent(mode=EffectMode.PULSE, intensity=0.5, speed=0.6, bpm=120.0)
        adapter.emit(0.0, intent)

        self.assertEqual(len(posted), 1)
        self.assertEqual(len(put), 0)
        self.assertEqual(posted[0][1]["type"], "energy")

    def test_same_effect_type_uses_put(self) -> None:
        posted: list = []
        put: list = []
        adapter = self._make_adapter(posted, put)
        intent_a = LightingIntent(mode=EffectMode.PULSE, intensity=0.5, speed=0.6, bpm=120.0)
        intent_b = LightingIntent(mode=EffectMode.PULSE, intensity=0.9, speed=0.3, bpm=120.0)
        adapter.emit(0.0, intent_a)
        adapter.emit(1.0, intent_b)

        self.assertEqual(len(posted), 1)
        self.assertEqual(len(put), 1)
        # PUT sends only config, no type key
        self.assertIn("config", put[0][1])
        self.assertNotIn("type", put[0][1])

    def test_different_effect_type_uses_post(self) -> None:
        posted: list = []
        put: list = []
        adapter = self._make_adapter(posted, put)
        intent_a = LightingIntent(mode=EffectMode.PULSE, intensity=0.5, speed=0.6, bpm=120.0)
        intent_b = LightingIntent(mode=EffectMode.AMBIENT, intensity=0.3, speed=0.2, bpm=90.0)
        adapter.emit(0.0, intent_a)
        adapter.emit(1.0, intent_b)

        self.assertEqual(len(posted), 2)
        self.assertEqual(len(put), 0)

    def test_clear_effect_resets_active_type(self) -> None:
        posted: list = []
        put: list = []
        adapter = self._make_adapter(posted, put)
        intent = LightingIntent(mode=EffectMode.PULSE, intensity=0.5, speed=0.6, bpm=120.0)
        adapter.emit(0.0, intent)
        adapter.clear_effect()
        # Same effect type after clear should POST again
        adapter.emit(2.0, intent)

        self.assertEqual(len(posted), 2)
        self.assertEqual(len(put), 0)

    def test_payload_has_no_bpm_hint(self) -> None:
        posted: list = []
        put: list = []
        adapter = self._make_adapter(posted, put)
        intent = LightingIntent(mode=EffectMode.PULSE, intensity=0.5, speed=0.6, bpm=120.0)
        adapter.emit(0.0, intent)

        config = posted[0][1]["config"]
        self.assertNotIn("bpm_hint", config)

    def test_ripple_mirror_defaults_true(self) -> None:
        posted: list = []
        put: list = []
        adapter = self._make_adapter(posted, put)
        intent = LightingIntent(mode=EffectMode.RIPPLE, intensity=0.5, speed=0.6, bpm=120.0)
        adapter.emit(0.0, intent)

        self.assertTrue(posted[0][1]["config"]["mirror"])

    def test_ripple_mirror_configurable(self) -> None:
        posted: list = []
        clock = {"t": 0.0}

        def _post(url: str, payload: dict, timeout: float) -> None:
            posted.append(payload)

        def _monotonic() -> float:
            clock["t"] += 1.0
            return clock["t"]

        adapter = LedFxOutputAdapter(
            LedFxConfig(base_url="http://127.0.0.1:8888", virtual_id="v1", mirror=False),
            transport=_post,
            monotonic_fn=_monotonic,
        )
        intent = LightingIntent(mode=EffectMode.RIPPLE, intensity=0.5, speed=0.6, bpm=120.0)
        adapter.emit(0.0, intent)

        self.assertFalse(posted[0]["config"]["mirror"])


class DarkenHexTests(unittest.TestCase):
    def test_darkens_white(self) -> None:
        self.assertEqual(_darken_hex("#ffffff", 0.5), "#7f7f7f")

    def test_darkens_red(self) -> None:
        self.assertEqual(_darken_hex("#ff0000", 0.3), "#4c0000")

    def test_black_stays_black(self) -> None:
        self.assertEqual(_darken_hex("#000000", 0.3), "#000000")

    def test_default_factor(self) -> None:
        result = _darken_hex("#ffffff")
        # default factor=0.3 → int(255*0.3)=76 → 0x4c
        self.assertEqual(result, "#4c4c4c")


class ScrollPayloadTests(unittest.TestCase):
    def _make_adapter(self, posted: list, put: list, mirror: bool = True):
        clock = {"t": 0.0}

        def _post(url: str, payload: dict, timeout: float) -> None:
            posted.append(payload)

        def _put(url: str, payload: dict, timeout: float) -> None:
            put.append(payload)

        def _monotonic() -> float:
            clock["t"] += 1.0
            return clock["t"]

        return LedFxOutputAdapter(
            LedFxConfig(
                base_url="http://127.0.0.1:8888",
                virtual_id="v1",
                effect_type_override="scroll",
                mirror=mirror,
            ),
            transport=_post,
            put_transport=_put,
            delete_transport=lambda u, t: None,
            monotonic_fn=_monotonic,
        )

    def test_scroll_ripple_sets_color_bands(self) -> None:
        posted: list = []
        put: list = []
        adapter = self._make_adapter(posted, put)
        intent = LightingIntent(
            mode=EffectMode.RIPPLE, intensity=0.5, speed=0.6, bpm=120.0,
            color="#ff8800",
        )
        adapter.emit(0.0, intent)

        config = posted[0]["config"]
        self.assertEqual(config["color_lows"], "#ff8800")
        self.assertEqual(config["color_mids"], "#ff8800")
        self.assertEqual(config["color_high"], "#ff8800")

    def test_scroll_ripple_background_is_darkened(self) -> None:
        posted: list = []
        put: list = []
        adapter = self._make_adapter(posted, put)
        intent = LightingIntent(
            mode=EffectMode.RIPPLE, intensity=0.5, speed=0.6, bpm=120.0,
            color="#ff8800",
        )
        adapter.emit(0.0, intent)

        config = posted[0]["config"]
        self.assertEqual(config["background_color"], _darken_hex("#ff8800"))

    def test_scroll_ripple_passes_brightness_and_omits_pinned_keys(self) -> None:
        posted: list = []
        put: list = []
        adapter = self._make_adapter(posted, put)
        intent = LightingIntent(
            mode=EffectMode.RIPPLE, intensity=0.9, speed=0.3, bpm=120.0,
            color="#aabbcc",
        )
        adapter.emit(0.0, intent)

        config = posted[0]["config"]
        self.assertAlmostEqual(config["brightness"], 0.9, places=3)
        # speed, decay, background_brightness left to LedFx defaults
        self.assertNotIn("speed", config)
        self.assertNotIn("decay", config)
        self.assertNotIn("background_brightness", config)

    def test_scroll_ripple_mirror(self) -> None:
        posted: list = []
        put: list = []
        adapter = self._make_adapter(posted, put, mirror=True)
        intent = LightingIntent(
            mode=EffectMode.RIPPLE, intensity=0.5, speed=0.6, bpm=120.0,
            color="#112233",
        )
        adapter.emit(0.0, intent)

        config = posted[0]["config"]
        self.assertTrue(config["mirror"])

    def test_scroll_ripple_mirror_false(self) -> None:
        posted: list = []
        put: list = []
        adapter = self._make_adapter(posted, put, mirror=False)
        intent = LightingIntent(
            mode=EffectMode.RIPPLE, intensity=0.5, speed=0.6, bpm=120.0,
            color="#112233",
        )
        adapter.emit(0.0, intent)

        config = posted[0]["config"]
        self.assertFalse(config["mirror"])

    def test_scroll_ripple_no_color_omits_bands(self) -> None:
        posted: list = []
        put: list = []
        adapter = self._make_adapter(posted, put)
        intent = LightingIntent(
            mode=EffectMode.RIPPLE, intensity=0.5, speed=0.6, bpm=120.0,
        )
        adapter.emit(0.0, intent)

        config = posted[0]["config"]
        self.assertNotIn("color_lows", config)
        self.assertNotIn("color_mids", config)
        self.assertNotIn("color_high", config)
        self.assertNotIn("background_color", config)

    def test_non_ripple_scroll_uses_generic_config(self) -> None:
        """MOTION mode resolves to scroll but should use the generic payload."""
        clock = {"t": 0.0}
        posted: list = []

        def _post(url: str, payload: dict, timeout: float) -> None:
            posted.append(payload)

        def _monotonic() -> float:
            clock["t"] += 1.0
            return clock["t"]

        adapter = LedFxOutputAdapter(
            LedFxConfig(base_url="http://127.0.0.1:8888", virtual_id="v1"),
            transport=_post,
            monotonic_fn=_monotonic,
        )
        intent = LightingIntent(
            mode=EffectMode.MOTION, intensity=0.8, speed=0.7, bpm=130.0,
            color="#ff0000",
        )
        adapter.emit(0.0, intent)

        config = posted[0]["config"]
        # Generic payload uses "color", not band-specific keys
        self.assertEqual(config["color"], "#ff0000")
        self.assertNotIn("color_lows", config)


class BandColorEffectsTests(unittest.TestCase):
    """All effects in _BAND_COLOR_EFFECTS should use band-specific color keys."""

    def test_wavelength_ripple_uses_band_colors(self) -> None:
        clock = {"t": 0.0}
        posted: list = []

        def _post(url: str, payload: dict, timeout: float) -> None:
            posted.append(payload)

        def _monotonic() -> float:
            clock["t"] += 1.0
            return clock["t"]

        adapter = LedFxOutputAdapter(
            LedFxConfig(
                base_url="http://127.0.0.1:8888",
                virtual_id="v1",
                effect_type_override="wavelength",
            ),
            transport=_post,
            monotonic_fn=_monotonic,
        )
        intent = LightingIntent(
            mode=EffectMode.RIPPLE, intensity=0.9, speed=0.6, bpm=120.0,
            color="#ff8800",
        )
        adapter.emit(0.0, intent)

        config = posted[0]["config"]
        self.assertEqual(posted[0]["type"], "wavelength")
        self.assertEqual(config["color_lows"], "#ff8800")
        self.assertEqual(config["color_mids"], "#ff8800")
        self.assertEqual(config["color_high"], "#ff8800")
        self.assertEqual(config["background_color"], _darken_hex("#ff8800"))
        self.assertNotIn("color", config)

    def test_band_color_set_contains_expected_types(self) -> None:
        self.assertIn("scroll", _BAND_COLOR_EFFECTS)
        self.assertIn("wavelength", _BAND_COLOR_EFFECTS)


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
