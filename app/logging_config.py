from __future__ import annotations

import sys
from loguru import logger
from .config import settings


def configure_logging() -> None:
    logger.remove()
    logger.add(
        sys.stdout,
        level=settings.log_level,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} | {message}",
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )


__all__ = ["configure_logging", "logger"]
