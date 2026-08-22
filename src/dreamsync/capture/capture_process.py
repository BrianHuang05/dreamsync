"""FFmpeg audio capture process lifecycle manager."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from dataclasses import dataclass, field

from dreamsync.capture.ffmpeg_device import (
    CaptureBackend, CaptureDevice, CaptureDiscoveryError, default_capture_pattern,
    discover_audio_device, discover_capture_device,
)
from dreamsync.ffmpeg import resolve_ffmpeg

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaptureConfig:
    """Parameters for the FFmpeg capture subprocess."""

    sample_rate: int = 44100
    channels: int = 2
    thread_queue_size: int = 1024
    device_pattern: str = field(default_factory=default_capture_pattern)
    capture_source: str | None = None


class CaptureProcessManager:
    """Spawn, monitor, and terminate the FFmpeg capture process.

    The process reads from a platform capture source and writes raw PCM (s16le)
    to its stdout pipe.
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
            Full backend-specific source name. If ``None``, runs discovery using
            ``capture_source`` (when supplied) or the legacy ``device_pattern``.
        """
        if self._process is not None and self._process.poll() is None:
            raise RuntimeError("Capture process is already running")

        if device_name is None:
            pattern = self._config.capture_source or self._config.device_pattern
            if sys.platform == "win32":
                # Preserve the established DirectShow seam (and its callers).
                name = discover_audio_device(pattern)
                if name is None:
                    raise RuntimeError(f"No audio device matching {pattern!r} found (DirectShow backend)")
                device = CaptureDevice(CaptureBackend.DIRECTSHOW, name, match_rule="substring")
            else:
                try:
                    device = discover_capture_device(pattern)
                except CaptureDiscoveryError as exc:
                    raise RuntimeError(str(exc)) from exc
        else:
            device = CaptureDevice(CaptureBackend.DIRECTSHOW if sys.platform == "win32" else CaptureBackend.PULSE, device_name)

        cmd = self._build_command(device)
        logger.info("Starting capture: %s", " ".join(cmd))

        # On Windows, CREATE_NEW_PROCESS_GROUP prevents Ctrl+C from
        # propagating to the child — our code terminates it cleanly.
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **kwargs,
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

    def _build_command(self, device: CaptureDevice | str) -> list[str]:
        cfg = self._config
        ffmpeg = resolve_ffmpeg()
        if ffmpeg is None:
            raise RuntimeError("ffmpeg not found on PATH")
        if isinstance(device, str):
            device = CaptureDevice(
                CaptureBackend.DIRECTSHOW if sys.platform == "win32" else CaptureBackend.PULSE,
                device,
            )
        input_args = (
            ["-f", "dshow", "-i", f"audio={device.name}"]
            if device.backend is CaptureBackend.DIRECTSHOW
            else ["-f", "pulse", "-i", device.name]
        )
        return [
            ffmpeg,
            "-hide_banner",
            "-loglevel", "warning",
            "-thread_queue_size", str(cfg.thread_queue_size),
            *input_args,
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
