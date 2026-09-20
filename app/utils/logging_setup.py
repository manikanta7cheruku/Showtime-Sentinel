"""Logging configuration."""
from __future__ import annotations

import logging
import sys
from pathlib import Path


class SafeStreamHandler(logging.StreamHandler):
    """Stream handler that safely ignores writes to closed streams during tests."""
    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except (ValueError, OSError):
            pass


def setup_logging(level: str = "INFO", log_file: Path | str | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = SafeStreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    if log_file:
        file_path = Path(log_file)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)