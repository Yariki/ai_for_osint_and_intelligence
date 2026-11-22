"""JSON logging with secrets redacted at the logging boundary.

Every log line of the process goes through one handler, whether it was written
with structlog or with the standard library (uvicorn, SQLAlchemy, Alembic).
The handler renders the line as JSON and then replaces every configured secret
value in the rendered text. Redacting the finished line, and not the separate
fields, is what covers a secret wherever it appears: in the message, in a bound
field, in an exception or inside the ``repr`` of some object (NFR-SEC-3).
"""

import json
import logging
import sys
from collections.abc import Callable, Iterable
from typing import Any, TextIO

import structlog

REDACTED = "[REDACTED]"


def make_redactor(secrets: Iterable[str]) -> Callable[[Any, str, str], str]:
    """Return a processor that removes the secrets from a rendered log line."""
    needles: set[str] = set()
    for secret in secrets:
        if secret:
            needles.add(secret)
            # The form the secret takes inside a JSON string (quotes, backslashes,
            # non-ASCII characters are escaped there).
            needles.add(json.dumps(secret)[1:-1])
    # Longest first, so a URL is replaced before the password inside it.
    ordered = sorted(needles, key=len, reverse=True)

    def redact(_logger: Any, _method: str, line: str) -> str:
        for needle in ordered:
            line = line.replace(needle, REDACTED)
        return line

    return redact


def configure_logging(
    secrets: Iterable[str], level: str = "INFO", stream: TextIO | None = None
) -> None:
    """Send all logging of this process to ``stream`` (stdout) as redacted JSON lines."""
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
    )
    # The redactor runs last, on the rendered line.
    final: list[Any] = [
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        structlog.processors.JSONRenderer(),
        make_redactor(secrets),
    ]
    formatter = structlog.stdlib.ProcessorFormatter(
        # Applied to records that come from the standard library.
        foreign_pre_chain=shared,
        processors=final,
    )
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
