"""What the registries' data says about an address (FR-ENR-1, FR-ENR-3).

- Registry and registration country: the delegation that covers the address.
- Network name and organization: the most specific registration record that
  covers the address, from the registry the address belongs to.
- Without such a record, the organization is the AS holder, and that source
  is recorded.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from ipaddress import IPv4Address

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SOURCE_REGISTRY = "registry"
SOURCE_AS_HOLDER = "as_holder"


@dataclass(frozen=True)
class Registration:
    rir: str | None = None
    registration_country: str | None = None
    network_name: str | None = None
    organization: str | None = None


# One round trip for a batch of addresses. Each subquery is answered by the
# GiST index on the range column. A registry's records are trusted for its own
# address space only: registries also keep placeholder objects for the space
# of the others, and those must not win.
LOOKUP = text(
    """
    SELECT a.address, d.rir, d.country, i.network_name, i.organization
    FROM unnest(CAST(:addresses AS bigint[])) AS a(address)
    LEFT JOIN LATERAL (
        SELECT rir, country FROM rir_delegation
        WHERE ip_range @> a.address
        ORDER BY upper(ip_range) - lower(ip_range) LIMIT 1
    ) AS d ON true
    LEFT JOIN LATERAL (
        SELECT network_name, organization FROM rir_inetnum
        WHERE ip_range @> a.address AND rir = d.rir
        ORDER BY upper(ip_range) - lower(ip_range) LIMIT 1
    ) AS i ON true
    """
)


async def lookup(
    db: AsyncSession, addresses: Sequence[IPv4Address]
) -> dict[IPv4Address, Registration]:
    """The registration of each address. An address nobody registered gets an empty one."""
    if not addresses:
        return {}
    rows = await db.execute(LOOKUP, {"addresses": [int(address) for address in addresses]})
    return {
        IPv4Address(address): Registration(rir, country, network_name, organization)
        for address, rir, country, network_name, organization in rows
    }


def organization(
    registration: Registration, as_holder: str | None
) -> tuple[str | None, str | None]:
    """The organization of an address and where it was taken from (FR-ENR-3)."""
    if registration.organization:
        return registration.organization, SOURCE_REGISTRY
    if as_holder:
        return as_holder, SOURCE_AS_HOLDER
    return None, None
