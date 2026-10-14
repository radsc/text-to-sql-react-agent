"""Logging setup: one-time root config plus a named-logger helper."""

import logging

from sql_agent.config import get_settings

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def setup_logging(level: str | None = None) -> None:
    """Configure root logging once; LOG_LEVEL from settings is the default."""
    logging.basicConfig(level=(level or get_settings().log_level).upper(), format=_FORMAT)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger; pass ``__name__`` so log lines show the source module."""
    return logging.getLogger(name)
