"""Host discovery of a chunk: ICMP echo and TCP probes (FR-CFG-2, FR-SCN-1).

A host is reachable if it answers an echo request, or answers a TCP probe with
a SYN/ACK or a reset. Each reply is recorded as evidence.
"""

import asyncio
from collections.abc import Sequence
from ipaddress import IPv4Address

from ndm.core.messages import Discovery
from ndm.scanner.connect import ConnectProbes
from ndm.scanner.icmp import EchoProbes
from ndm.scanner.ratelimit import RateLimiter
from ndm.scanner.results import ChunkResults
from ndm.scanner.syn import SynProbes


async def discover(
    targets: Sequence[IPv4Address],
    methods: Discovery,
    *,
    scan_id: int,
    limiter: RateLimiter,
    results: ChunkResults,
    timeout_ms: int,
    retries: int,
    tcp: SynProbes | ConnectProbes,
    echo: EchoProbes | None,
) -> None:
    """Run the selected discovery methods side by side.

    ``tcp`` is the chunk's TCP prober in discovery mode, SYN or connect.
    ``echo`` is None when the scanner has no raw ICMP capability; ICMP echo
    is then skipped and discovery rests on the TCP probes.
    """
    work = []
    if methods.icmp_echo and echo is not None:
        work.append(
            echo.run(
                targets,
                scan_id=scan_id,
                limiter=limiter,
                results=results,
                timeout_ms=timeout_ms,
                retries=retries,
            )
        )
    if methods.tcp_ports:
        work.append(tcp.run(targets, methods.tcp_ports))
    await asyncio.gather(*work)
