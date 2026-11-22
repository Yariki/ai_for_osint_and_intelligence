"""The server-sent event stream.

The stream never ends, which the in-process test client cannot read, so these
tests run the application on a real local port.
"""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from ipaddress import IPv4Address
from typing import Any

import httpx
import pytest
import uvicorn
from aio_pika.abc import AbstractChannel
from fastapi import FastAPI
from httpx import AsyncClient

from ndm.api.app import create_app
from ndm.api.events import QUEUE_SIZE, EventHub
from ndm.api.routers import events as events_router
from ndm.core import broker
from ndm.core.messages import HostRow, HostsChanged, ScanProgress
from ndm.core.settings import Settings

PROGRESS = ScanProgress(
    scan_id=7,
    project_id=3,
    state="running",
    phase="port_scan",
    chunks_done=1,
    chunks_total=4,
    probes_sent=9984,
)
HOSTS = HostsChanged(
    scan_id=7,
    project_id=3,
    hosts=[HostRow(address=IPv4Address("8.8.8.1"), reachable=True, open_port_count=2)],
)


@contextlib.asynccontextmanager
async def served(app: FastAPI) -> AsyncIterator[str]:
    """The application on a local port. Its lifespan is run by the caller."""
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_config=None, lifespan="off", timeout_graceful_shutdown=1
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


class Stream:
    """One browser's event stream."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self._lines = response.aiter_lines()

    async def next_block(self, timeout: float = 5.0) -> list[str]:
        """The lines up to the next blank line: one event or one comment."""
        block: list[str] = []
        async with asyncio.timeout(timeout):
            async for line in self._lines:
                if not line:
                    return block
                block.append(line)
        return block


@contextlib.asynccontextmanager
async def connected(base_url: str, cookie: str) -> AsyncIterator[Stream]:
    async with httpx.AsyncClient(base_url=base_url, timeout=None) as http:
        async with http.stream(
            "GET", "/api/v1/events", headers={"Cookie": f"ndm_session={cookie}"}
        ) as response:
            stream = Stream(response)
            assert response.status_code == 200
            assert await stream.next_block() == [": connected"]
            yield stream


def session_cookie(client: AsyncClient) -> str:
    # The cookie is Secure and the local test server speaks plain HTTP, so it is sent by hand.
    cookie: str = client.cookies["ndm_session"]
    return cookie


def event(block: list[str]) -> tuple[str, dict[str, Any]]:
    kind, data = block
    assert kind.startswith("event: ") and data.startswith("data: ")
    return kind.removeprefix("event: "), json.loads(data.removeprefix("data: "))


# --- Authentication ---


async def test_event_stream_answers_401_without_a_session(anonymous_client: AsyncClient) -> None:
    response = await anonymous_client.get("/api/v1/events")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


# --- From the hub to the browser ---


async def test_event_reaches_a_connected_client(app: FastAPI, client: AsyncClient) -> None:
    hub: EventHub = app.state.events
    async with served(app) as url, connected(url, session_cookie(client)) as stream:
        assert stream.response.headers["content-type"].startswith("text/event-stream")
        assert stream.response.headers["cache-control"] == "no-cache"

        hub.dispatch(PROGRESS.model_dump_json().encode())
        hub.dispatch(HOSTS.model_dump_json().encode())

        assert event(await stream.next_block()) == (
            "scan_progress",
            {
                "scan_id": 7,
                "type": "scan_progress",
                "project_id": 3,
                "state": "running",
                "phase": "port_scan",
                "chunks_done": 1,
                "chunks_total": 4,
                "probes_sent": 9984,
            },
        )
        kind, data = event(await stream.next_block())
        assert kind == "hosts_changed"
        assert data["hosts"] == [
            {
                "address": "8.8.8.1",
                "reachable": True,
                "open_port_count": 2,
                "latitude": None,
                "longitude": None,
            }
        ]


async def test_every_connected_client_gets_the_event(app: FastAPI, client: AsyncClient) -> None:
    hub: EventHub = app.state.events
    cookie = session_cookie(client)
    async with (
        served(app) as url,
        connected(url, cookie) as first,
        connected(url, cookie) as second,
    ):
        hub.dispatch(PROGRESS.model_dump_json().encode())

        assert event(await first.next_block())[0] == "scan_progress"
        assert event(await second.next_block())[0] == "scan_progress"


async def test_events_are_not_replayed(app: FastAPI, client: AsyncClient) -> None:
    hub: EventHub = app.state.events
    hub.dispatch(PROGRESS.model_dump_json().encode())  # nobody is connected yet

    async with served(app) as url, connected(url, session_cookie(client)) as stream:
        hub.dispatch(HOSTS.model_dump_json().encode())

        # The first thing the client sees is the event sent after it connected.
        assert event(await stream.next_block())[0] == "hosts_changed"


async def test_only_known_events_are_forwarded(app: FastAPI, client: AsyncClient) -> None:
    hub: EventHub = app.state.events
    async with served(app) as url, connected(url, session_cookie(client)) as stream:
        hub.dispatch(b"not json")
        hub.dispatch(b'{"type": "scan_progress", "scan_id": 7, "password": "leak"}')
        hub.dispatch(PROGRESS.model_dump_json().encode())

        # The first thing that arrives is the valid event; the two others were dropped.
        block = await stream.next_block()
        assert event(block)[1]["scan_id"] == 7
        assert "leak" not in "".join(block)


async def test_idle_stream_is_kept_alive(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(events_router, "KEEP_ALIVE_SECONDS", 0.2)

    async with served(app) as url, connected(url, session_cookie(client)) as stream:
        assert await stream.next_block() == [": keep-alive"]


async def test_disconnected_client_is_forgotten(app: FastAPI, client: AsyncClient) -> None:
    hub: EventHub = app.state.events
    async with served(app) as url:
        async with connected(url, session_cookie(client)):
            assert len(hub._subscribers) == 1
        for _ in range(50):
            if not hub._subscribers:
                break
            await asyncio.sleep(0.05)

    assert hub._subscribers == set()


def test_client_that_does_not_read_is_cut_off() -> None:
    hub = EventHub()
    slow = hub.subscribe()

    for _ in range(QUEUE_SIZE + 1):
        hub.dispatch(PROGRESS.model_dump_json().encode())

    # Its stream is ended and it is no longer served; it reconnects and reloads.
    assert hub._subscribers == set()
    items = [slow.get_nowait() for _ in range(slow.qsize())]
    assert items[-1] is None


# --- From the broker to the browser ---


async def test_event_published_to_the_broker_reaches_a_connected_client(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    broker_url: str,
    topology: AbstractChannel,
    app: FastAPI,
    client: AsyncClient,
) -> None:
    # A second instance of the API, this one with a broker, on the same database.
    monkeypatch.setenv("NDM_BROKER_URL", broker_url)
    with_broker = create_app(Settings())
    async with with_broker.router.lifespan_context(with_broker), served(with_broker) as url:
        async with connected(url, session_cookie(client)) as stream:
            # The listener needs a moment to bind its queue; publish until it arrives.
            for _ in range(20):
                await broker.publish(
                    topology, broker.EXCHANGE_EVENTS, "project.3.scan.7", PROGRESS, persistent=False
                )
                with contextlib.suppress(TimeoutError):
                    block = await stream.next_block(timeout=0.5)
                    break
            else:
                pytest.fail("no event arrived from the broker")

    assert event(block) == (
        "scan_progress",
        json.loads(PROGRESS.model_dump_json(exclude={"message_id", "schema_version"})),
    )
