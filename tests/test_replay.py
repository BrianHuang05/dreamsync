import unittest

import numpy as np
from scipy.io import wavfile

from dreamsync.replay import replay_wav_to_fake_output


def _mixed_signal(sr: int, duration: float = 6.0) -> np.ndarray:
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    low = 0.04 * np.sin(2 * np.pi * 220.0 * t[: int(sr * (duration / 3))])
    mid = 0.10 * np.sin(2 * np.pi * 110.0 * t[: int(sr * (duration / 3))])
    high = 0.35 * np.sin(2 * np.pi * 55.0 * t[: int(sr * (duration / 3))])
    return np.concatenate([low, mid, high]).astype(np.float32)


class ReplayTests(unittest.TestCase):
    def test_replay_produces_logs_with_mode_changes(self) -> None:
        sr = 44100
        # Signal must be longer than Director warmup_seconds (8.0) to
        # exercise mode transitions beyond AMBIENT.
        signal = _mixed_signal(sr, duration=12.0)
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
