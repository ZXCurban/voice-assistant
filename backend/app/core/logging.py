"""Logging configuration placeholder."""

import logging


def configure_logging(level: str = "INFO") -> None:
    """Configure root logger with a sane default format."""
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
