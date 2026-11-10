"""
Structured logging configuration using structlog.

Produces JSON logs in production and pretty dev logs in development.
Call configure_logging() once at startup.
"""
import logging
import sys
from typing import Any

import structlog

from app.config import get_settings


def configure_logging() -> None:
    """Configure structlog and stdlib logging for the whole application."""
    settings = get_settings()
    log_level = getattr(logging, settings.log_level, logging.INFO)
    is_dev = settings.environment == "development"

    # Shared pre-chain processors (run before the final renderer).
    # NOTE: add_logger_name uses logger.name which only exists on stdlib loggers;
    #       we use WriteLoggerFactory below so this is safe.
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if is_dev:
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]
    else:
        processors = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]

    # Use stdlib WriteLoggerFactory so that the underlying logger always has
    # a .name attribute, making add_logger_name safe to use if needed later.
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.WriteLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Route stdlib loggers (uvicorn, sqlalchemy…) through the same level.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy.engine"):
        logging.getLogger(name).setLevel(log_level)


def get_logger(name: str = __name__) -> Any:
    """Return a bound structlog logger for the given module name."""
    return structlog.get_logger(name)
