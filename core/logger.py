"""
Structured logger — JSON in production, human-readable in dev.

Usage:
    from core.logger import get_logger
    log = get_logger(__name__)
    log.info("detect.start", user_id="u1", subject="biology", files=2)
    log.error("detect.failed", error="timeout", elapsed=32.1)
"""

import json
import logging
import sys
import time

from core.config import LOG_LEVEL as _LOG_LEVEL_STR, LOG_FORMAT as _LOG_FORMAT

_LOG_LEVEL = _LOG_LEVEL_STR.upper()
_LOG_FORMAT = _LOG_FORMAT

# Standard LogRecord attributes to exclude from extra field output
_STDLIB_FIELDS = frozenset(logging.LogRecord("", 0, "", 0, "", [], None).__dict__.keys()) | {
    "message", "asctime", "taskName"
}


class _JsonFormatter(logging.Formatter):
    """One JSON object per line — structured, machine-parseable."""

    def format(self, record: logging.LogRecord) -> str:
        extras: dict = {}
        # Attach any extra keys passed via log.info(..., extra={...})
        for k, v in record.__dict__.items():
            if k not in _STDLIB_FIELDS and not k.startswith("_"):
                try:
                    json.dumps(v)  # only include JSON-serialisable extras
                    extras[k] = v
                except (TypeError, ValueError):
                    extras[k] = str(v)

        # [user_id=...][test_id=...] prefix on every event, "-" when not
        # available at this call site (e.g. before the SQS body has even been
        # parsed) — labeled (not just positional) so it's unambiguous which
        # value is which at a glance, and a trace can always be pulled with
        # `grep "test_id=job-abc-123"` regardless of which module emitted it.
        uid = extras.get("user_id", "-")
        tid = extras.get("test_id", "-")

        obj: dict = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "event": f"[user_id={uid}][test_id={tid}] {record.getMessage()}",
        }
        obj.update(extras)
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False)


class _PrettyFormatter(logging.Formatter):
    """Human-readable format for dev terminals."""

    LEVEL_COLORS = {
        "DEBUG":    "\033[36m",   # cyan
        "INFO":     "\033[32m",   # green
        "WARNING":  "\033[33m",   # yellow
        "ERROR":    "\033[31m",   # red
        "CRITICAL": "\033[35m",   # magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelname, "")
        ts = self.formatTime(record, "%H:%M:%S")
        event = record.getMessage()
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in _STDLIB_FIELDS and not k.startswith("_")
        }
        # [user_id=...][test_id=...] prefix — "-" when not available at this call site.
        uid = extras.pop("user_id", "-")
        tid = extras.pop("test_id", "-")
        prefix = f"[user_id={uid}][test_id={tid}]"
        base = f"{color}{ts}{self.RESET} {record.levelname:<8} {record.name} | {prefix} {event}"
        if extras:
            kv = "  ".join(f"{k}={v}" for k, v in extras.items())
            base = f"{base}  [{kv}]"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


class AppLogger(logging.LoggerAdapter):
    """
    Wraps a standard Logger, allows keyword extras:
        log.info("event.name", key=val, ...)
    """

    def info(self, event: str, **kwargs):          # type: ignore[override]
        super().info(event, extra=kwargs)

    def debug(self, event: str, **kwargs):         # type: ignore[override]
        super().debug(event, extra=kwargs)

    def warning(self, event: str, **kwargs):       # type: ignore[override]
        super().warning(event, extra=kwargs)

    def error(self, event: str, **kwargs):         # type: ignore[override]
        super().error(event, extra=kwargs)

    def critical(self, event: str, **kwargs):      # type: ignore[override]
        super().critical(event, extra=kwargs)

    def process(self, msg, kwargs):
        # extra is already merged onto the record by LoggerAdapter
        return msg, kwargs


_configured_loggers: set = set()


def get_logger(name: str) -> AppLogger:
    """Return a configured AppLogger for the given module name."""
    base = logging.getLogger(name)
    if name not in _configured_loggers:
        base.setLevel(getattr(logging, _LOG_LEVEL, logging.INFO))
        if not base.handlers:
            handler = logging.StreamHandler(sys.stdout)
            if _LOG_FORMAT == "json":
                handler.setFormatter(_JsonFormatter())
            else:
                handler.setFormatter(_PrettyFormatter())
            base.addHandler(handler)
        base.propagate = False
        _configured_loggers.add(name)
    return AppLogger(base, {})
