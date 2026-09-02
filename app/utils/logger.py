"""Application logging to console and dedicated log files."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import ROOT_DIR, load_config

_CONFIGURED = False


def setup_logging(logs_dir: Path | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    cfg = load_config()
    directory = logs_dir or (ROOT_DIR / cfg.logs_dir)
    directory.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger("app")
    root.setLevel(logging.INFO)
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(formatter)
    root.addHandler(console)

    app_file = RotatingFileHandler(directory / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    app_file.setLevel(logging.INFO)
    app_file.setFormatter(formatter)
    root.addHandler(app_file)

    download_logger = logging.getLogger("app.download")
    download_file = RotatingFileHandler(
        directory / "download.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    download_file.setLevel(logging.INFO)
    download_file.setFormatter(formatter)
    download_logger.addHandler(download_file)
    download_logger.propagate = True

    error_file = RotatingFileHandler(directory / "error.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    error_file.setLevel(logging.ERROR)
    error_file.setFormatter(formatter)
    root.addHandler(error_file)

    _CONFIGURED = True


def get_logger(name: str = "app") -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
