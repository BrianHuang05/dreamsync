import unittest

import numpy as np
from scipy.io import wavfile

from dreamsync.output.ledfx import LedFxConfig, LedFxOutputAdapter
from dreamsync.replay import replay_wav_to_fake_output
from dreamsync.replay import replay_wav_to_ledfx_output


def _mixed_signal(sr: int) -> np.ndarray:
    t = np.linspace(0, 6.0, int(sr * 6.0), endpoint=False)
    low = 0.04 * np.sin(2 * np.pi * 220.0 * t[: int(sr * 2.0)])
    mid = 0.10 * np.sin(2 * np.pi * 110.0 * t[: int(sr * 2.0)])
    high = 0.35 * np.sin(2 * np.pi * 55.0 * t[: int(sr * 2.0)])
    return np.concatenate([low, mid, high]).astype(np.float32)


class ReplayTests(unittest.TestCase):
    def test_replay_produces_logs_with_mode_changes(self) -> None:
        sr = 44100
        path = None
        signal = _mixed_signal(sr)
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "mix.wav"
            wavfile.write(path, sr, signal)
            logs = replay_wav_to_fake_output(path)

        self.assertTrue(logs)
        modes = {str(row["mode"]) for row in logs}
        self.assertIn("motion", modes)
        self.assertGreaterEqual(len(modes), 2)

    def test_ledfx_replay_sends_and_logs_multiple_modes(self) -> None:
        sr = 44100
        signal = _mixed_signal(sr)
        sent: list[tuple[str, dict, float]] = []
        now = {"t": 0.0}

        def _transport(url: str, payload: dict, timeout: float) -> None:
            sent.append((url, payload, timeout))

        def _monotonic() -> float:
            now["t"] += 0.05
            return now["t"]

        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "mix.wav"
            wavfile.write(path, sr, signal)
            adapter = LedFxOutputAdapter(
                LedFxConfig(
                    base_url="http://127.0.0.1:8888",
                    virtual_id="abc",
                    min_update_interval_seconds=0.0,
                ),
                transport=_transport,
                monotonic_fn=_monotonic,
            )
            logs = replay_wav_to_ledfx_output(path, adapter)

        self.assertTrue(logs)
        self.assertTrue(any(bool(row["sent"]) for row in logs))
        modes = {str(row["mode"]) for row in logs}
        self.assertIn("motion", modes)
        self.assertGreaterEqual(len(modes), 2)
        self.assertGreater(len(sent), 0)
