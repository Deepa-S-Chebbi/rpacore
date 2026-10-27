"""Shared SQLite retry helpers for short-lived lock handling."""

from __future__ import annotations

import logging
import sqlite3

from rpacore._clock import _Clock


def is_transient_sqlite_lock(error: sqlite3.OperationalError) -> bool:
    """Return True for SQLite lock/busy errors worth a short retry."""
    message = str(error).lower()
    return "locked" in message or "busy" in message


def sqlite_retry_delay(attempt: int, *, base_delay_seconds: float) -> float:
    """Return the exponential retry delay for a zero-based attempt index."""
    return base_delay_seconds * (2**attempt)


def _sleep_before_sqlite_retry(
    attempt: int,
    *,
    clock: _Clock,
    delay_seconds: float,
    log: logging.Logger,
    event: str,
    operation: str,
    queue_item_id: str,
    worker_id: str,
    error: sqlite3.OperationalError,
    max_attempts: int,
) -> None:
    delay = sqlite_retry_delay(attempt, base_delay_seconds=delay_seconds)
    log.warning(
        "Retrying transient SQLite operation after lock/busy error",
        extra={
            "event": event,
            "operation": operation,
            "queue_item_id": queue_item_id,
            "worker_id": worker_id,
            "attempt": attempt + 1,
            "max_attempts": max_attempts,
            "retry_delay_seconds": delay,
        },
        exc_info=(type(error), error, error.__traceback__),
    )
    clock.sleep(delay)
