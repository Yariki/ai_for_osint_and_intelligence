"""Check 5.8 (NFR-OPS-2): the scanner logs each chunk and each traceroute it
completes, and the line carries the scan's or the run's identifier, bound from
the message."""

import asyncio
import io
import json
import logging
from pathlib import Path
from typing import Any

import pytest
import structlog
from aio_pika.abc import AbstractChannel

from ndm.core import broker, messages
from ndm.core.logging import configure_logging
from ndm.core.messages import Chunk, Hello, TracerouteJob
from ndm.core.settings import Settings
from ndm.scanner import main as scanner_main
from ndm.scanner.main import Worker
from tests.integration.scanner.conftest import chunk, next_message
from tests.integration.scanner.test_traceroute_scope import job


async def test_completed_work_is_logged_with_its_identifier(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    broker_url: str,
    topology: AbstractChannel,
) -> None:
    monkeypatch.setenv("NDM_BROKER_URL", broker_url)
    monkeypatch.setenv("NDM_VANTAGE_POINT", "test")
    monkeypatch.setenv("NDM_ALLOW_NON_PUBLIC_TARGETS", "true")
    monkeypatch.setattr(scanner_main, "HEARTBEAT_FILE", tmp_path / "heartbeat")
    handled: list[Any] = []

    async def handle_chunk(self: Worker, received: Chunk) -> None:
        handled.append(received)

    async def handle_traceroute(self: Worker, received: TracerouteJob) -> None:
        handled.append(received)

    monkeypatch.setattr(Worker, "handle_chunk", handle_chunk)
    monkeypatch.setattr(Worker, "handle_traceroute", handle_traceroute)
    # Logging as main() sets it up in production, into a buffer.
    root = logging.getLogger()
    handlers, level = root.handlers, root.level
    log = io.StringIO()
    configure_logging(secrets=[], stream=log)
    scanner = asyncio.create_task(scanner_main.run(Settings()))
    try:
        results = await topology.get_queue(broker.QUEUE_RESULTS)
        hello = await next_message(results)
        assert isinstance(messages.RESULTS.validate_json(hello.body), Hello)
        await hello.ack()

        await broker.publish(
            topology, broker.EXCHANGE_SCANNER, broker.chunk_queue("test"), chunk(scan_id=31)
        )
        await broker.publish(
            topology, broker.EXCHANGE_SCANNER, broker.traceroute_queue("test"), job(run_id=32)
        )
        for _ in range(200):
            if len(handled) == 2:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.2)  # the line follows the acknowledgement
    finally:
        scanner.cancel()
        await asyncio.gather(scanner, return_exceptions=True)
        root.handlers, root.level = handlers, level
        structlog.reset_defaults()

    lines = [json.loads(line) for line in log.getvalue().splitlines()]
    (chunk_line,) = [line for line in lines if line["event"] == "chunk_done"]
    (traceroute_line,) = [line for line in lines if line["event"] == "traceroute_done"]
    assert (chunk_line["scan_id"], chunk_line["chunk_seq"]) == (31, 1)
    assert traceroute_line["run_id"] == 32
    assert "run_id" not in chunk_line and "scan_id" not in traceroute_line
