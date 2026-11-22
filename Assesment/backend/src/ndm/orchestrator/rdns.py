"""Reverse DNS for discovered hosts (FR-ENR-4).

This is the one step of enrichment that sends target addresses to another
system: each address goes to the configured resolver in a PTR query. The
operator can switch it off; then this module is not called at all.
"""

import asyncio
from collections.abc import Sequence
from ipaddress import IPv4Address

import dns.asyncresolver
import dns.exception
import dns.resolver

STATUS_OK = "ok"
STATUS_NO_NAME = "no_name"
STATUS_FAILED = "failed"
STATUS_DISABLED = "disabled"

# Lookups in flight at once, and the time one lookup may take with its retries.
CONCURRENCY = 50
LIFETIME_SECONDS = 4.0

Answer = tuple[list[str], str]
"""The names of an address and the status of its lookup."""


def _resolver(nameserver: tuple[str, int] | None) -> dns.asyncresolver.Resolver:
    """The configured resolver, or the one of the system if none is configured."""
    resolver = dns.asyncresolver.Resolver(configure=nameserver is None)
    if nameserver is not None:
        resolver.nameservers = [nameserver[0]]
        resolver.port = nameserver[1]
    resolver.lifetime = LIFETIME_SECONDS
    return resolver


async def _lookup(resolver: dns.asyncresolver.Resolver, address: IPv4Address) -> Answer:
    try:
        answer = await resolver.resolve_address(str(address))
    except dns.resolver.NXDOMAIN, dns.resolver.NoAnswer:
        return [], STATUS_NO_NAME
    except dns.exception.DNSException, OSError:
        # No answer in time, a refusal, a broken reply, no route to the resolver.
        return [], STATUS_FAILED
    names = sorted({record.target.to_text(omit_final_dot=True) for record in answer})
    return names, STATUS_OK if names else STATUS_NO_NAME


async def resolve(
    addresses: Sequence[IPv4Address], nameserver: tuple[str, int] | None
) -> dict[IPv4Address, Answer]:
    """Look up the PTR names of every address. A lookup that fails is an answer
    too; nothing is raised for a single address."""
    resolver = _resolver(nameserver)
    limit = asyncio.Semaphore(CONCURRENCY)

    async def one(address: IPv4Address) -> tuple[IPv4Address, Answer]:
        async with limit:
            return address, await _lookup(resolver, address)

    return dict(await asyncio.gather(*(one(address) for address in addresses)))
