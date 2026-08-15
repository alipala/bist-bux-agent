"""Ortak logging + rich konsol."""
from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

console = Console()


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> logging.Logger:
    handlers: list[logging.Handler] = [
        RichHandler(console=console, rich_tracebacks=True, show_path=False)
    ]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s"))
        handlers.append(fh)

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return logging.getLogger("finagent")
