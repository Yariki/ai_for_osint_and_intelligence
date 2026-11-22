"""Reverse DNS against a real resolver on the loopback address (check 2.7, in part).

The resolver here is a small UDP server that answers PTR queries from a table
and counts them, so "no query is sent" can be measured without a capture.
"""

import asyncio
from collections.abc import AsyncIterator
from ipaddress import IPv4Address
from typing import Any

import dns.message
import dns.rcode
import dns.rdatatype
import dns.reversename
import dns.rrset
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.db.models import ScanState
from ndm.orchestrator import rdns
from ndm.orchestrator.tick import Orchestrator
from tests.integration.orchestrator.conftest import Outbox, ScanFactory
from tests.tools.enrichment_scan import REACHABLE, reverse_dns, scan_to_end, stored

RECORDS = {
    "8.8.8.8": ["dns.example.net."],
    "193.0.6.1": ["www.example.nl.", "web.example.nl."],
}
SILENT = "1.0.0.1"  # the resolver does not answer for this one


class FakeResolver(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.transport: Any = None

    def connection_made(self, transport: Any) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, sender: Any) -> None:
        query = dns.message.from_wire(data)
        name = query.question[0].name
        address = str(dns.reversename.to_address(name))
        self.queries.append(address)
        if address == SILENT:
            return
        answer = dns.message.make_response(query)
        if address in RECORDS:
            answer.answer.append(dns.rrset.from_text(name, 60, "IN", "PTR", *RECORDS[address]))
        else:
            answer.set_rcode(dns.rcode.NXDOMAIN)
        self.transport.sendto(answer.to_wire(), sender)


@pytest.fixture
async def resolver() -> AsyncIterator[tuple[FakeResolver, tuple[str, int]]]:
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        FakeResolver, local_addr=("127.0.0.1", 0)
    )
    yield protocol, ("127.0.0.1", transport.get_extra_info("sockname")[1])
    transport.close()


@pytest.fixture(autouse=True)
def short_patience(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rdns, "LIFETIME_SECONDS", 0.6)


async def test_names_are_resolved_through_the_configured_resolver(
    resolver: tuple[FakeResolver, tuple[str, int]],
) -> None:
    server, nameserver = resolver
    addresses = [IPv4Address(a) for a in ("8.8.8.8", "193.0.6.1", "200.3.13.2", SILENT)]

    answers = await rdns.resolve(addresses, nameserver)

    assert answers == {
        IPv4Address("8.8.8.8"): (["dns.example.net"], "ok"),
        # Every name of the address, in a fixed order, without the final dot.
        IPv4Address("193.0.6.1"): (["web.example.nl", "www.example.nl"], "ok"),
        # The resolver says there is no such name.
        IPv4Address("200.3.13.2"): ([], "no_name"),
        # The resolver does not answer in time. Not an error: an answer of its own.
        IPv4Address(SILENT): ([], "failed"),
    }
    assert set(server.queries) == {str(address) for address in addresses}


async def test_resolver_that_is_not_there_gives_failed_for_every_address() -> None:
    unused = ("127.0.0.1", 1)  # nothing listens on this port

    answers = await rdns.resolve([IPv4Address("8.8.8.8"), IPv4Address("8.8.4.4")], unused)

    assert {status for _, status in answers.values()} == {"failed"}


async def test_many_addresses_are_resolved_concurrently(
    resolver: tuple[FakeResolver, tuple[str, int]],
) -> None:
    server, nameserver = resolver
    addresses = [
        IPv4Address(f"198.51.{third}.{fourth}") for third in range(2) for fourth in range(200)
    ]

    answers = await asyncio.wait_for(rdns.resolve(addresses, nameserver), timeout=20)

    assert len(answers) == 400
    assert {status for _, status in answers.values()} == {"no_name"}
    assert len(set(server.queries)) == 400


# --- Through the enrichment phase of a scan ---


async def test_names_from_the_resolver_are_stored_with_the_scan(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
    resolver: tuple[FakeResolver, tuple[str, int]],
) -> None:
    server, (host, port) = resolver
    enricher = enriching_orchestrator.enricher
    assert enricher is not None
    enricher.settings = enricher.settings.model_copy(
        update={"ptr_enabled": True, "ptr_resolver": f"{host}:{port}"}
    )

    scan = await scan_to_end(enriching_orchestrator, outbox, make_scan)

    names = await reverse_dns(db_session, scan)
    assert names["8.8.8.8"] == (["dns.example.net"], "ok")
    assert names["193.0.6.1"] == (["web.example.nl", "www.example.nl"], "ok")
    assert names["200.3.13.2"] == ([], "no_name")
    assert names[SILENT] == ([], "failed")
    assert len(set(server.queries)) == REACHABLE
    assert (await stored(db_session, scan)).state is ScanState.COMPLETED


async def test_with_the_switch_off_no_dns_query_is_sent(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
    resolver: tuple[FakeResolver, tuple[str, int]],
) -> None:
    server, (host, port) = resolver
    enricher = enriching_orchestrator.enricher
    assert enricher is not None
    # A resolver is configured, and the switch is off.
    enricher.settings = enricher.settings.model_copy(
        update={"ptr_enabled": False, "ptr_resolver": f"{host}:{port}"}
    )

    scan = await scan_to_end(enriching_orchestrator, outbox, make_scan)
    await asyncio.sleep(0.2)

    assert server.queries == []
    assert {status for _, status in (await reverse_dns(db_session, scan)).values()} == {"disabled"}
    assert (await stored(db_session, scan)).state is ScanState.COMPLETED
