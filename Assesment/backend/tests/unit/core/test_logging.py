import io
import json
import logging
import os
from collections.abc import Iterator
from typing import Any

import pytest
import structlog

from ndm.core.logging import REDACTED, configure_logging
from ndm.core.settings import Settings

APP_URL = "postgresql+asyncpg://ndm_app:app-pw-91@postgres/ndm"
OWNER_URL = "postgresql+asyncpg://ndm:owner-pw-37@postgres/ndm"


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers, root.level
    yield
    root.handlers, root.level = handlers, level
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()


@pytest.fixture
def secrets(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The secret values of a configured installation."""
    for name in list(os.environ):
        if name.startswith("NDM_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("NDM_DATABASE_URL", APP_URL)
    monkeypatch.setenv("NDM_MIGRATION_DATABASE_URL", OWNER_URL)
    values = Settings().secret_values()
    assert set(values) == {APP_URL, "app-pw-91", OWNER_URL, "owner-pw-37"}
    return values


@pytest.fixture
def output(secrets: list[str]) -> io.StringIO:
    stream = io.StringIO()
    configure_logging(secrets, stream=stream)
    return stream


def lines(stream: io.StringIO) -> list[dict[str, Any]]:
    """Parse the output; fails if any line is not valid JSON."""
    return [json.loads(line) for line in stream.getvalue().splitlines()]


# --- NFR-SEC-3: no configured secret reaches the output ---


def test_secret_in_a_message_is_redacted(output: io.StringIO, secrets: list[str]) -> None:
    log = structlog.get_logger("test")
    for secret in secrets:
        log.info(f"connecting to {secret} now")

    for secret in secrets:
        assert secret not in output.getvalue()
    assert all(REDACTED in line["event"] for line in lines(output))


def test_secret_in_a_bound_field_is_redacted(output: io.StringIO, secrets: list[str]) -> None:
    for secret in secrets:
        structlog.get_logger("test").bind(dsn=secret).info("connecting", nested={"url": [secret]})
    structlog.contextvars.bind_contextvars(context_dsn=secrets[0])
    structlog.get_logger("test").info("with context")

    for secret in secrets:
        assert secret not in output.getvalue()
    assert lines(output)[-1]["context_dsn"] == REDACTED


def test_secret_in_an_exception_is_redacted(output: io.StringIO, secrets: list[str]) -> None:
    log = structlog.get_logger("test")
    for secret in secrets:
        try:
            raise RuntimeError(f"cannot connect to {secret}")
        except RuntimeError:
            log.exception("database error")

    for secret in secrets:
        assert secret not in output.getvalue()
    assert all(REDACTED in line["exception"] for line in lines(output))


def test_secret_inside_an_object_is_redacted(output: io.StringIO, secrets: list[str]) -> None:
    class Connection:
        def __repr__(self) -> str:
            return f"<Connection {secrets[0]}>"

    structlog.get_logger("test").info("opened", connection=Connection())

    assert secrets[0] not in output.getvalue()
    assert lines(output)[0]["connection"] == f"<Connection {REDACTED}>"


def test_standard_library_loggers_are_redacted_too(output: io.StringIO, secrets: list[str]) -> None:
    library_log = logging.getLogger("sqlalchemy.engine")
    for secret in secrets:
        library_log.warning("pool created for %s", secret)
        try:
            raise RuntimeError(f"cannot connect to {secret}")
        except RuntimeError:
            library_log.exception("engine error")

    for secret in secrets:
        assert secret not in output.getvalue()
    assert len(lines(output)) == 2 * len(secrets)


def test_secret_that_json_escapes_is_redacted() -> None:
    secret = 'pä"ss\\word'
    stream = io.StringIO()
    configure_logging([secret], stream=stream)

    structlog.get_logger("test").info(f"password is {secret}", field=secret)

    (line,) = lines(stream)
    assert line["event"] == f"password is {REDACTED}"
    assert line["field"] == REDACTED


# --- NFR-OPS-2: every line is valid JSON ---


def test_every_line_is_valid_json(output: io.StringIO) -> None:
    structlog.get_logger("test").info("plain")
    structlog.get_logger("test").warning('quotes " and \n newlines', count=3)
    logging.getLogger("uvicorn.error").info("from the standard library: %d", 7)
    try:
        raise ValueError("boom")
    except ValueError:
        structlog.get_logger("test").exception("with a traceback")

    parsed = lines(output)

    assert len(parsed) == 4
    assert [line["level"] for line in parsed] == ["info", "warning", "info", "error"]
    assert parsed[2]["event"] == "from the standard library: 7"
    for line in parsed:
        assert {"event", "level", "logger", "timestamp"} <= line.keys()


def test_level_filters_lines(secrets: list[str]) -> None:
    stream = io.StringIO()
    configure_logging(secrets, level="WARNING", stream=stream)

    structlog.get_logger("test").info("dropped")
    structlog.get_logger("test").warning("kept")

    assert [line["event"] for line in lines(stream)] == ["kept"]
