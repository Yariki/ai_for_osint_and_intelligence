"""A chunk the scanner keeps failing on is dead-lettered after the delivery
limit, so that the orchestrator fails its scan (NFR-REL-4).

RabbitMQ 4 does not count a nack with requeue towards the limit; without the
scanner's own count the chunk would be delivered again for ever.
"""

import asyncio
from pathlib import Path

import pytest
from aio_pika.abc import AbstractChannel

from ndm.core import broker, messages
from ndm.core.messages import Chunk, Hello
from ndm.core.settings import Settings
from ndm.scanner import main as scanner_main
from ndm.scanner.main import Worker
from tests.integration.scanner.conftest import chunk, next_message


async def test_a_chunk_that_fails_every_time_goes_to_the_dead_letter_queue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    broker_url: str,
    topology: AbstractChannel,
) -> None:
    monkeypatch.setenv("NDM_BROKER_URL", broker_url)
    monkeypatch.setenv("NDM_VANTAGE_POINT", "test")
    monkeypatch.setenv("NDM_ALLOW_NON_PUBLIC_TARGETS", "true")
    monkeypatch.setattr(scanner_main, "HEARTBEAT_FILE", tmp_path / "heartbeat")
    attempts = 0

    async def fail(self: Worker, received: Chunk) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("the chunk cannot be handled")

    monkeypatch.setattr(Worker, "handle_chunk", fail)
    scanner = asyncio.create_task(scanner_main.run(Settings()))
    try:
        results = await topology.get_queue(broker.QUEUE_RESULTS)
        hello = await next_message(results)
        assert isinstance(messages.RESULTS.validate_json(hello.body), Hello)
        await hello.ack()

        await broker.publish(
            topology, broker.EXCHANGE_SCANNER, broker.chunk_queue("test"), chunk(scan_id=11)
        )
        dead = await next_message(await topology.get_queue(broker.QUEUE_DEAD))
    finally:
        scanner.cancel()
        await asyncio.gather(scanner, return_exceptions=True)

    assert attempts == broker.DELIVERY_LIMIT
    dead_chunk = messages.WORK.validate_json(dead.body)
    assert isinstance(dead_chunk, Chunk) and dead_chunk.scan_id == 11
    await dead.ack()
    chunks = await topology.get_queue(broker.chunk_queue("test"))
    assert await chunks.get(fail=False) is None
