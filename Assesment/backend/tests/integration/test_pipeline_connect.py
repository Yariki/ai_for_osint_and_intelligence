"""The scan pipeline end to end, with the real orchestrator code and the real
scanner worker in connect mode, against listeners on the loopback address.

Only the broker is replaced: what one side publishes is handed to the other
directly. Nothing here needs raw sockets or a container, so this runs in CI
and shows that the two halves agree on their messages.
"""

# The fixtures of the orchestrator and scanner tests are imported by name and
# then named again as test arguments, which is how pytest fixtures are shared.
# ruff: noqa: F811

import asyncio
from collections.abc import Callable
from ipaddress import IPv4Address

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.api.schemas.scans import to_ranges
from ndm.core.messages import Chunk, ChunkDone, ChunkRejected, Findings, Message, Technique
from ndm.core.settings import Settings
from ndm.db.models import ScanState
from ndm.orchestrator.recording import record_result
from ndm.orchestrator.tick import Orchestrator, tick
from ndm.scanner.main import Worker

# Fixtures of the orchestrator and scanner tests, used here together.
from tests.integration.orchestrator.conftest import (  # noqa: F401
    Outbox,
    ScanFactory,
    make_scan,
    orchestrator,
    outbox,
    scanner_reports,
    snapshot,
)
from tests.integration.scanner.conftest import (  # noqa: F401
    Listeners,
    closed_ports,
    listeners,
    make_worker,
    published,
)

TARGETS = [IPv4Address(f"127.0.0.{last}") for last in (1, 2, 3, 4)]


async def run_scan(orchestrator: Orchestrator, outbox: Outbox, worker: Worker) -> list[Chunk]:
    """Tick, give every published chunk to the worker, and record what it
    publishes, until nothing more is sent."""
    handled = 0
    for _ in range(50):
        await tick(orchestrator)
        if len(outbox.chunks) == handled:
            return outbox.chunks
        for chunk in outbox.chunks[handled:]:
            await worker.handle_chunk(chunk)
        handled = len(outbox.chunks)
    raise AssertionError("the scan did not come to an end")


@pytest.fixture
def scanner(make_worker: Callable[..., Worker], orchestrator: Orchestrator) -> Worker:
    """The worker, publishing straight into the orchestrator's result handlers."""
    worker = make_worker(NDM_ALLOW_NON_PUBLIC_TARGETS="true")

    async def deliver(message: Message) -> None:
        assert isinstance(message, Findings | ChunkDone | ChunkRejected)
        # Through JSON, as over the broker.
        received = type(message).model_validate_json(message.model_dump_json())
        async with orchestrator.sessions() as db:
            await record_result(db, received)
            await db.commit()

    worker._publish = deliver
    return worker


@pytest.fixture
def lab_orchestrator(monkeypatch: pytest.MonkeyPatch, orchestrator: Orchestrator) -> Orchestrator:
    """The orchestrator with the test switch on: the loopback address is non-public."""
    monkeypatch.setenv("NDM_ALLOW_NON_PUBLIC_TARGETS", "true")
    orchestrator.settings = Settings()
    return orchestrator


async def test_connect_scan_from_queue_to_completion(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    lab_orchestrator: Orchestrator,
    outbox: Outbox,
    scanner: Worker,
    listeners: Listeners,
) -> None:
    refused = closed_ports(6)
    ports = [*listeners.ports, *refused]
    scan = await make_scan(
        state=ScanState.QUEUED,
        targets=TARGETS,
        scope=("127.0.0.0", "127.0.0.255"),
        probed_ports=to_ranges(ports),
        parameters={
            "discovery": {"icmp_echo": True, "tcp_ports": [listeners.ports[0], refused[0]]},
            "pps": 5000,
            "timeout_ms": 500,
            "retries": 0,
        },
    )
    # The scanner has no raw sockets here: it reports no SYN capability.
    await scanner_reports(lab_orchestrator, syn=scanner.syn_capable, allow_non_public=True)

    chunks = await run_scan(lab_orchestrator, outbox, scanner)

    stored = await snapshot(db_session, scan)
    state, phase, partial, reason, counters = stored["scan"]
    assert (state, phase, partial, reason) == (ScanState.COMPLETED, None, False, None)
    # "automatic" became connect, and every chunk said so.
    assert {chunk.technique for chunk in chunks} == {Technique.CONNECT}
    # Every loopback address answers, with an accepted or a refused connection.
    assert all(row[0] for row in stored["observations"].values())
    # The listeners are bound to 127.0.0.1 only: its ports are open, nothing else is.
    assert stored["ports"] == [("127.0.0.1", port) for port in sorted(listeners.ports)]
    assert stored["observations"]["127.0.0.1"][2:] == (6, 0)
    for address in ("127.0.0.2", "127.0.0.3", "127.0.0.4"):
        assert stored["observations"][address][2:] == (16, 0)
    # Discovery: 4 targets on 2 ports. Port scan: 4 targets on 16 ports.
    assert counters == {
        "chunks_total": 2,
        "chunks_done": 2,
        "probes_sent": 8 + 64,
        "receive_drops": 0,
    }
    await asyncio.sleep(0.05)
    # No connection carried data.
    assert set(listeners.connections) == {b""}


async def test_scanner_rejects_what_the_orchestrator_would_send_without_its_own_switch(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    lab_orchestrator: Orchestrator,
    outbox: Outbox,
    make_worker: Callable[..., Worker],
    scanner: Worker,
    listeners: Listeners,
) -> None:
    """The orchestrator believes the test switch is on everywhere. The scanner's
    own environment says otherwise, and the scanner's word is final."""
    strict = make_worker()  # no test switch
    strict._publish = scanner._publish
    scan = await make_scan(
        state=ScanState.QUEUED,
        targets=TARGETS,
        scope=("127.0.0.0", "127.0.0.255"),
        probed_ports=to_ranges(listeners.ports),
    )
    await scanner_reports(lab_orchestrator, syn=False, allow_non_public=True)

    chunks = await run_scan(lab_orchestrator, outbox, strict)

    assert len(chunks) == 1
    state, _, partial, reason, _ = (await snapshot(db_session, scan))["scan"]
    assert (state, partial, reason) == (ScanState.FAILED, True, "chunk_rejected: non_public")
    await asyncio.sleep(0.05)
    assert listeners.connections == []
