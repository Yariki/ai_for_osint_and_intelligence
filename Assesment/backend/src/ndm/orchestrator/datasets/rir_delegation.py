"""Delegation statistics of the five registries (docs/datasets/registry-sources.md).

One line per block a registry has handed out: prefix, country, date, status.
They give the registry and the registration country of an address. They carry
no names.

A registry's file is downloaded, checked against its published MD5, parsed,
and loaded in the transaction the dataset manager commits: the registry's rows
are replaced as a whole, so a reader never sees a half-loaded set.
"""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from ipaddress import AddressValueError, IPv4Address
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import httpx
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.settings import Settings
from ndm.db.models import RirDelegation
from ndm.orchestrator.datasets.manager import Dataset, DatasetError

# Our name of a registry -> (the name in its file, the location of the file).
SOURCES: dict[str, tuple[str, str]] = {
    "ripe": (
        "ripencc",
        "https://ftp.ripe.net/pub/stats/ripencc/delegated-ripencc-extended-latest",
    ),
    "apnic": ("apnic", "https://ftp.apnic.net/stats/apnic/delegated-apnic-extended-latest"),
    "arin": ("arin", "https://ftp.arin.net/pub/stats/arin/delegated-arin-extended-latest"),
    "lacnic": (
        "lacnic",
        "https://ftp.lacnic.net/pub/stats/lacnic/delegated-lacnic-extended-latest",
    ),
    "afrinic": (
        "afrinic",
        "https://ftp.afrinic.net/pub/stats/afrinic/delegated-afrinic-extended-latest",
    ),
}

# Only these describe a registration. "available" and "reserved" blocks are
# still with the registry.
REGISTERED = ("allocated", "assigned")
# Codes that stand for "no country".
NO_COUNTRY = ("", "ZZ")

TIMEOUT = httpx.Timeout(30, read=120)


@dataclass(frozen=True)
class Delegation:
    first: int
    last: int
    country: str | None
    delegated_on: date | None
    status: str


def _date(text: str) -> date | None:
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None  # empty, or 00000000


def parse(text: str, registry: str) -> tuple[str, list[Delegation]]:
    """Read a delegation statistics file. Returns its version and its IPv4 registrations.

    The version is the last day the file covers, from its version line.
    ``registry`` is the name the file must carry in that line.
    """
    version: str | None = None
    records: list[Delegation] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.strip().split("|")
        if version is None:
            # version|registry|serial|records|startdate|enddate|UTCoffset
            covered = _date(fields[5]) if len(fields) >= 7 else None
            if covered is None or fields[1] != registry:
                raise DatasetError(f"This is not a delegation statistics file of {registry}.")
            version = covered.isoformat()
            continue
        if fields[-1] == "summary" or len(fields) < 7 or fields[2] != "ipv4":
            continue
        # registry|cc|type|start|value|date|status[|opaque-id]
        _, country, _, start, value, day, status = fields[:7]
        if status not in REGISTERED:
            continue
        try:
            first = int(IPv4Address(start))
            # For IPv4 the value is a number of addresses, not a prefix length.
            last = first + int(value) - 1
        except (AddressValueError, ValueError) as error:
            raise DatasetError(f"Line {number} of the {registry} file cannot be read.") from error
        if not first <= last <= 0xFFFFFFFF:
            raise DatasetError(f"Line {number} of the {registry} file cannot be read.")
        records.append(
            Delegation(first, last, None if country in NO_COUNTRY else country, _date(day), status)
        )
    if version is None or not records:
        raise DatasetError(f"The {registry} file holds no IPv4 registration.")
    return version, records


async def fetch(client: httpx.AsyncClient, url: str) -> bytes:
    """Download a file into memory."""
    try:
        response = await client.get(url, follow_redirects=True, timeout=TIMEOUT)
    except httpx.HTTPError as error:
        raise DatasetError(f"The download of {url} failed: {type(error).__name__}.") from error
    if response.status_code != 200:
        raise DatasetError(f"{url} answered with status {response.status_code}.")
    return response.content


async def copy_rows(
    db: AsyncSession, table: str, columns: Sequence[str], rows: Sequence[tuple[Any, ...]]
) -> None:
    """Insert many rows in one go, inside the session's transaction."""
    connection = await db.connection()
    raw = await connection.get_raw_connection()
    driver: Any = raw.driver_connection  # the asyncpg connection
    await driver.copy_records_to_table(table, records=rows, columns=list(columns))


def address_range(first: int, last: int) -> Any:
    """The value of an ``int8range`` column for ``copy_rows``: first to last, inclusive."""
    return asyncpg.Range(first, last + 1)


async def update(
    db: AsyncSession, rir: str, client: httpx.AsyncClient, current_version: str | None
) -> str | None:
    """Load one registry's delegation statistics. Returns the version, or None
    if the file is the one that is loaded."""
    registry, url = SOURCES[rir]
    body = await fetch(client, url)
    published = re.search(rb"\b[0-9a-fA-F]{32}\b", await fetch(client, url + ".md5"))
    if published is None or published.group().decode().lower() != hashlib.md5(body).hexdigest():
        raise DatasetError(f"The checksum of the {registry} file is not the published one.")
    version, records = parse(body.decode("latin-1"), registry)
    if version == current_version:
        return None

    # Replace the registry's rows as a whole. Readers see the old set until the commit.
    await db.execute(delete(RirDelegation).where(RirDelegation.rir == rir))
    await copy_rows(
        db,
        "rir_delegation",
        ("rir", "ip_range", "country", "delegated_on", "status"),
        [
            (rir, address_range(r.first, r.last), r.country, r.delegated_on, r.status)
            for r in records
        ],
    )
    return version


def datasets(settings: Settings, client: httpx.AsyncClient) -> list[Dataset]:
    def loader(rir: str) -> Any:
        async def load(db: AsyncSession, current_version: str | None) -> str | None:
            return await update(db, rir, client, current_version)

        return load

    return [
        Dataset(
            name=f"rir-delegation-{rir}",
            interval=timedelta(hours=settings.rir_update_interval_hours),
            update=loader(rir),
        )
        for rir in SOURCES
    ]
