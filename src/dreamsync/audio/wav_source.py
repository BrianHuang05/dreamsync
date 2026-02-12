from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.io import wavfile


def read_wav_mono(path: Path) -> tuple[int, np.ndarray]:
    """Load a WAV file and return (sample_rate, mono_float32_signal)."""
    sr, data = wavfile.read(str(path))

    if data.ndim == 2:
        data = data.mean(axis=1)

    if np.issubdtype(data.dtype, np.integer):
        max_val = np.iinfo(data.dtype).max
        data = data.astype(np.float32) / max_val
    else:
        data = data.astype(np.float32)

    return sr, data
