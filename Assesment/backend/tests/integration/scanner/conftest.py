"""Helpers for scanner tests: a worker in connect mode, and loopback targets.

Without raw sockets the worker scans with ordinary connections, so it can be
run against listeners on the loopback address. Loopback is non-public, which
is what the test switch is for.
"""

import asyncio
import os
import socket
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from ipaddress import IPv4Address
from typing import Any

import pytest
from aio_pika.abc import AbstractIncomingMessage, AbstractQueue

from ndm.core.messages import Chunk, ChunkPhase, Discovery, Message, Technique
from ndm.core.settings import Settings
from ndm.scanner.main import Worker

LOCAL = IPv4Address("127.0.0.1")
LOOPBACK_SCOPE = [(IPv4Address("127.0.0.0"), IPv4Address("127.0.0.255"))]


@dataclass
class Published:
    """Stands in for the broker: keeps what the worker publishes, with the time."""

    messages: list[Message] = field(default_factory=list)
    times: list[float] = field(default_factory=list)

    async def __call__(self, message: Message) -> None:
        self.messages.append(message)
        self.times.append(asyncio.get_running_loop().time())

    def kinds(self) -> list[str]:
        return [message.type for message in self.messages]  # type: ignore[attr-defined]


@pytest.fixture
def published() -> Published:
    return Published()


@pytest.fixture
def make_worker(monkeypatch: pytest.MonkeyPatch, published: Published) -> Callable[..., Worker]:
    """A worker without raw sockets. Keyword arguments become NDM_ variables."""

    def create(**environment: str) -> Worker:
        for name in list(os.environ):
            if name.startswith("NDM_"):
                monkeypatch.delenv(name)
        monkeypatch.setenv("NDM_VANTAGE_POINT", "test")
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        return Worker(Settings(), published)

    return create


@pytest.fixture
def worker(make_worker: Callable[..., Worker]) -> Worker:
    """A worker with the test switch on, so that it may scan the loopback address."""
    return make_worker(NDM_ALLOW_NON_PUBLIC_TARGETS="true")


@dataclass
class Listeners:
    ports: list[int]
    connections: list[bytes]
    """What each client sent before it disconnected; one item per connection."""


@pytest.fixture
async def listeners() -> AsyncIterator[Listeners]:
    """Ten open ports on the loopback address."""
    connections: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connections.append(await reader.read())
        writer.close()

    servers = [await asyncio.start_server(handle, "127.0.0.1", 0) for _ in range(10)]
    yield Listeners([server.sockets[0].getsockname()[1] for server in servers], connections)
    for server in servers:
        server.close()
        await server.wait_closed()


def closed_ports(count: int) -> list[int]:
    """Loopback ports on which nothing listens."""
    sockets = [socket.socket() for _ in range(count)]
    for sock in sockets:
        sock.bind(("127.0.0.1", 0))
    ports = [sock.getsockname()[1] for sock in sockets]
    for sock in sockets:
        sock.close()
    return ports


def chunk(**changes: Any) -> Chunk:
    """A connect-mode port-scan chunk for the loopback address; fields can be overridden."""
    fields: dict[str, Any] = {
        "scan_id": 7,
        "chunk_seq": 1,
        "phase": ChunkPhase.PORT_SCAN,
        "technique": Technique.CONNECT,
        "targets": [LOCAL],
        "ports": [],
        "pps": 5000,
        "timeout_ms": 500,
        "retries": 0,
        "seed": 1,
        "attestation_id": 1,
        "scope": LOOPBACK_SCOPE,
    }
    fields.update(changes)
    if fields["phase"] is ChunkPhase.DISCOVERY and "discovery" not in fields:
        fields["discovery"] = Discovery(icmp_echo=True, tcp_ports=fields.pop("ports"))
        fields["ports"] = []
    return Chunk(**fields)


async def next_message(queue: AbstractQueue, timeout: float = 10) -> AbstractIncomingMessage:
    """The next message on a queue, waiting for it to arrive.

    ``queue.get`` does not wait: basic.get answers at once when the queue is
    empty, and its timeout bounds only that answer.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while (delivery := await queue.get(fail=False)) is None:
        assert asyncio.get_running_loop().time() < deadline, f"no message on {queue.name}"
        await asyncio.sleep(0.05)
    return delivery
