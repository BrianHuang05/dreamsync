"""FFmpeg audio capture process lifecycle manager."""

from __future__ import annotations

import logging
import subprocess
import threading
from dataclasses import dataclass

from dreamsync.capture.ffmpeg_device import discover_audio_device

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaptureConfig:
    """Parameters for the FFmpeg capture subprocess."""

    sample_rate: int = 48000
    channels: int = 2
    thread_queue_size: int = 1024
    device_pattern: str = "CABLE Output"


class CaptureProcessManager:
    """Spawn, monitor, and terminate the FFmpeg capture process.

    The process reads from a DirectShow audio device and writes raw PCM
    (s16le) to its stdout pipe.
    """

    def __init__(self, config: CaptureConfig | None = None) -> None:
        self._config = config or CaptureConfig()
        self._process: subprocess.Popen | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_output: list[str] = []

    @property
    def config(self) -> CaptureConfig:
        return self._config

    @property
    def stdout(self):
        """Raw stdout pipe of the FFmpeg process (binary IO)."""
        if self._process is None:
            raise RuntimeError("Capture process not started")
        return self._process.stdout

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, device_name: str | None = None) -> None:
        """Spawn the FFmpeg capture process.

        Parameters
        ----------
        device_name:
            Full DirectShow device name.  If ``None``, runs device
            discovery automatically using the configured pattern.
        """
        if self._process is not None and self._process.poll() is None:
            raise RuntimeError("Capture process is already running")

        if device_name is None:
            device_name = discover_audio_device(self._config.device_pattern)
            if device_name is None:
                raise RuntimeError(
                    f"No audio device matching {self._config.device_pattern!r} found"
                )

        cmd = self._build_command(device_name)
        logger.info("Starting capture: %s", " ".join(cmd))

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Monitor stderr in a background thread so it doesn't block.
        self._stderr_output.clear()
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True,
        )
        self._stderr_thread.start()

    def stop(self) -> int | None:
        """Terminate the capture process and return its exit code."""
        if self._process is None:
            return None

        if self._process.poll() is None:
            logger.info("Stopping capture process (pid=%d)", self._process.pid)
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("Force-killing capture process")
                self._process.kill()
                self._process.wait(timeout=5)

        rc = self._process.returncode
        self._process = None
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=2)
            self._stderr_thread = None
        return rc

    def restart(self, device_name: str | None = None) -> None:
        """Stop then start the capture process."""
        self.stop()
        self.start(device_name=device_name)

    def is_alive(self) -> bool:
        """Return ``True`` if the capture process is running."""
        return self._process is not None and self._process.poll() is None

    @property
    def stderr_output(self) -> list[str]:
        """Lines collected from FFmpeg stderr (for diagnostics)."""
        return list(self._stderr_output)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_command(self, device_name: str) -> list[str]:
        cfg = self._config
        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-thread_queue_size", str(cfg.thread_queue_size),
            "-f", "dshow",
            "-i", f"audio={device_name}",
            "-ac", str(cfg.channels),
            "-ar", str(cfg.sample_rate),
            "-f", "s16le",
            "-acodec", "pcm_s16le",
            "pipe:1",
        ]

    def _drain_stderr(self) -> None:
        """Read stderr lines in a background thread."""
        assert self._process is not None
        try:
            for raw_line in self._process.stderr:  # type: ignore[union-attr]
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                if line:
                    self._stderr_output.append(line)
                    logger.debug("ffmpeg stderr: %s", line)
        except (ValueError, OSError):
            pass  # pipe closed
