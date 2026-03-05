"""Per-segment FFmpeg encoder process (PCM stdin -> MP3 file)."""

from __future__ import annotations

import logging
import subprocess
import threading

logger = logging.getLogger(__name__)


class EncoderProcess:
    """Spawn an FFmpeg process that encodes raw PCM from stdin to an MP3 file.

    Lifecycle: ``start()`` -> ``write()`` (many) -> ``close()`` -> ``wait()``.
    """

    def __init__(
        self,
        output_path: str,
        sample_rate: int = 48000,
        channels: int = 2,
        bitrate: str = "192k",
    ) -> None:
        self._output_path = output_path
        self._sample_rate = sample_rate
        self._channels = channels
        self._bitrate = bitrate
        self._process: subprocess.Popen | None = None
        self._stderr_lines: list[str] = []
        self._stderr_thread: threading.Thread | None = None

    @property
    def output_path(self) -> str:
        return self._output_path

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the FFmpeg encoder subprocess."""
        if self._process is not None:
            raise RuntimeError("Encoder already started")

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-f", "s16le",
            "-ac", str(self._channels),
            "-ar", str(self._sample_rate),
            "-i", "pipe:0",
            "-c:a", "libmp3lame",
            "-b:a", self._bitrate,
            "-y",
            self._output_path,
        ]
        logger.debug("Starting encoder: %s", " ".join(cmd))

        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self._stderr_lines.clear()
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def write(self, pcm_data: bytes) -> None:
        """Write raw PCM bytes to the encoder's stdin."""
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("Encoder not started or stdin closed")
        self._process.stdin.write(pcm_data)

    def close(self) -> None:
        """Close stdin to signal end-of-input, triggering MP3 finalization."""
        if self._process is not None and self._process.stdin is not None:
            self._process.stdin.close()

    def wait(self, timeout: float = 30.0) -> int:
        """Wait for FFmpeg to exit and return the exit code."""
        if self._process is None:
            raise RuntimeError("Encoder not started")
        self._process.wait(timeout=timeout)
        rc = self._process.returncode
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=2)
        return rc

    def is_alive(self) -> bool:
        """Return True if the encoder process is still running."""
        return self._process is not None and self._process.poll() is None

    @property
    def stderr_output(self) -> list[str]:
        return list(self._stderr_lines)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _drain_stderr(self) -> None:
        assert self._process is not None
        try:
            for raw in self._process.stderr:  # type: ignore[union-attr]
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    self._stderr_lines.append(line)
                    logger.debug("encoder stderr: %s", line)
        except (ValueError, OSError):
            pass
