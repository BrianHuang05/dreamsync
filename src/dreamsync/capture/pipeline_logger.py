"""Structured JSON logging for the capture pipeline."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "category": getattr(record, "category", "pipeline"),
            "event": getattr(record, "event", record.getMessage()),
            "data": getattr(record, "data", None),
            "frame_position": getattr(record, "frame_position", 0),
        }
        return json.dumps(entry, default=str)


class _ConsoleFormatter(logging.Formatter):
    """Human-readable one-line format for console output."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        cat = getattr(record, "category", "pipeline")
        event = getattr(record, "event", record.getMessage())
        fp = getattr(record, "frame_position", 0)
        return f"{ts} [{record.levelname:<7}] {cat}:{event}  frame={fp}"


class PipelineLogger:
    """Structured logger with console (human) + file (JSON) outputs.

    Parameters
    ----------
    log_dir:
        Directory for JSON log files.  Created if missing.
    console_level:
        Minimum level for console output.
    file_level:
        Minimum level for file output.
    """

    def __init__(
        self,
        log_dir: str = "./logs",
        console_level: str = "INFO",
        file_level: str = "DEBUG",
    ) -> None:
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)

        self._logger = logging.getLogger("dreamsync.capture.pipeline_events")
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False

        # Remove prior handlers (prevents duplicate on re-init).
        self._logger.handlers.clear()

        # Console handler
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(getattr(logging, console_level.upper(), logging.INFO))
        ch.setFormatter(_ConsoleFormatter())
        self._logger.addHandler(ch)

        # File handler
        log_file = self._log_dir / "pipeline.jsonl"
        fh = logging.FileHandler(str(log_file), encoding="utf-8")
        fh.setLevel(getattr(logging, file_level.upper(), logging.DEBUG))
        fh.setFormatter(_JsonFormatter())
        self._logger.addHandler(fh)

    def log(
        self,
        level: str,
        category: str,
        event: str,
        data: dict | None = None,
        frame_position: int = 0,
    ) -> None:
        """Emit a structured log entry."""
        lvl = getattr(logging, level.upper(), logging.INFO)
        extra = {
            "category": category,
            "event": event,
            "data": data,
            "frame_position": frame_position,
        }
        self._logger.log(lvl, event, extra=extra)

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def capture_event(self, event: str, frame_position: int = 0, **data) -> None:
        self.log("INFO", "capture", event, data or None, frame_position)

    def encoder_event(self, event: str, frame_position: int = 0, **data) -> None:
        self.log("INFO", "encoder", event, data or None, frame_position)

    def split_event(self, event: str, frame_position: int = 0, **data) -> None:
        self.log("INFO", "split", event, data or None, frame_position)

    def boundary_event(self, event: str, frame_position: int = 0, **data) -> None:
        self.log("INFO", "boundary", event, data or None, frame_position)

    def timing_event(self, event: str, frame_position: int = 0, **data) -> None:
        self.log("INFO", "timing", event, data or None, frame_position)

    def recovery_event(self, event: str, frame_position: int = 0, **data) -> None:
        self.log("WARNING", "recovery", event, data or None, frame_position)
