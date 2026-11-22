"""Registration records of the registries: who a range is registered to
(docs/datasets/registry-sources.md).

- RIPE NCC, APNIC and AFRINIC publish their records as RPSL dumps. They are
  downloaded only for the registries the operator has listed, because each
  has terms the operator must meet.
- ARIN and LACNIC give their records out under an agreement. The operator
  puts the file into a directory, and it is loaded from there.

Only the range, the network name, the organization and the country are read.
No contact, address, telephone or e-mail attribute is stored.

A registry's records are replaced as a whole in the transaction the dataset
manager commits, so a reader never sees a half-loaded set.
"""

import asyncio
import gzip
import itertools
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any, TextIO

import httpx
import structlog
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.settings import DOWNLOADABLE_REGISTRIES, Settings
from ndm.db.models import RirInetnum
from ndm.orchestrator.datasets.manager import Dataset, DatasetError, NotConfigured
from ndm.orchestrator.datasets.rir_delegation import TIMEOUT, address_range, copy_rows

log = structlog.get_logger(__name__)

# The organisation file comes first: the inetnum objects refer to it.
DOWNLOADS: dict[str, list[str]] = {
    "ripe": [
        "https://ftp.ripe.net/ripe/dbase/split/ripe.db.organisation.gz",
        "https://ftp.ripe.net/ripe/dbase/split/ripe.db.inetnum.gz",
    ],
    "apnic": [
        "https://ftp.apnic.net/apnic/whois/apnic.db.organisation.gz",
        "https://ftp.apnic.net/apnic/whois/apnic.db.inetnum.gz",
    ],
    "afrinic": ["https://ftp.afrinic.net/pub/dbase/afrinic.db.gz"],
}
assert set(DOWNLOADS) == set(DOWNLOADABLE_REGISTRIES)

# Registry -> the name of the file the operator supplies.
OPERATOR_FILES = {"arin": "arin_db.xml", "lacnic": "lacnic_db.txt"}

BATCH_ROWS = 5000
CHUNK_BYTES = 1 << 16


@dataclass(frozen=True)
class Inetnum:
    first: int
    last: int
    network_name: str | None
    organization: str | None
    country: str | None


# --- RPSL: RIPE NCC, APNIC, AFRINIC, and LACNIC's whois format ----------------------


def rpsl_objects(lines: Iterable[str]) -> Iterator[tuple[str, dict[str, list[str]]]]:
    """The objects of an RPSL dump, as (class, attributes).

    Objects are separated by empty lines. A line is ``attribute: value``; a
    line that starts with a space, a tab or ``+`` continues the value before it.
    """
    kind: str | None = None
    attributes: dict[str, list[str]] = {}
    last: list[str] | None = None
    for line in itertools.chain(lines, [""]):
        line = line.rstrip("\r\n")
        if not line.strip():
            if kind is not None:
                yield kind, attributes
            kind, attributes, last = None, {}, None
        elif line[0] in "#%":
            continue
        elif line[0] in " \t+":
            if last is not None:
                last[-1] = f"{last[-1]} {line[1:].strip()}".strip()
        elif ":" in line:
            name, _, value = line.partition(":")
            name = name.strip().lower()
            kind = kind or name
            last = attributes.setdefault(name, [])
            last.append(value.strip())


def parse_address(text: str) -> int:
    """An address as an integer. Octets with leading zeros are accepted."""
    octets = [int(part) for part in text.strip().split(".")]
    if len(octets) != 4 or not all(0 <= octet <= 255 for octet in octets):
        raise ValueError(text)
    return int(IPv4Address(".".join(str(octet) for octet in octets)))


def parse_range(text: str) -> tuple[int, int]:
    """``first - last``, or a prefix. A prefix may leave out trailing zero
    octets, as LACNIC writes it: ``200.3.12/22``."""
    if "-" in text:
        first_text, _, last_text = text.partition("-")
        first, last = parse_address(first_text), parse_address(last_text)
    else:
        network, _, length_text = text.strip().partition("/")
        octets = network.split(".")
        length = int(length_text)
        if not 1 <= len(octets) <= 4 or not 0 <= length <= 32:
            raise ValueError(text)
        first = parse_address(".".join(octets + ["0"] * (4 - len(octets))))
        last = first | ((1 << (32 - length)) - 1)
    if first > last:
        raise ValueError(text)
    return first, last


def _first(attributes: dict[str, list[str]], name: str) -> str | None:
    values = [value for value in attributes.get(name, []) if value]
    return values[0] if values else None


def _country(attributes: dict[str, list[str]]) -> str | None:
    """The country code. A comment may follow it: ``EU # really world wide``."""
    code = (_first(attributes, "country") or "").split("#")[0].strip().upper()
    return code if len(code) == 2 and code.isalpha() else None


def organisations(lines: Iterable[str]) -> dict[str, str]:
    """Identifier -> name of every ``organisation`` object."""
    names = {}
    for kind, attributes in rpsl_objects(lines):
        name = _first(attributes, "org-name")
        if kind == "organisation" and name:
            names[attributes["organisation"][0].upper()] = name
    return names


def inetnums(lines: Iterable[str], names: dict[str, str]) -> Iterator[Inetnum]:
    """Every ``inetnum`` object. An object whose range cannot be read is left out.

    The organization is the name of the organisation object the record refers
    to; without one, the ``owner`` (LACNIC) or the first description line.
    """
    for kind, attributes in rpsl_objects(lines):
        if kind != "inetnum":
            continue
        try:
            first, last = parse_range(attributes["inetnum"][0])
        except ValueError:
            continue
        reference = _first(attributes, "org")
        organization = (
            (names.get(reference.upper()) if reference else None)
            or _first(attributes, "owner")
            or _first(attributes, "descr")
        )
        yield Inetnum(
            first, last, _first(attributes, "netname"), organization, _country(attributes)
        )


def open_text(path: Path) -> TextIO:
    """Open a dump, compressed or not. Latin-1 reads any byte."""
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="latin-1")
    return path.open(encoding="latin-1")


def rpsl_files(paths: list[Path]) -> Iterator[Inetnum]:
    """The inetnum objects of a set of dump files, with their organizations resolved."""
    names: dict[str, str] = {}
    for path in paths:
        with open_text(path) as lines:
            names.update(organisations(lines))
    for path in paths:
        with open_text(path) as lines:
            yield from inetnums(lines, names)


# --- ARIN: arin_db.xml ---------------------------------------------------------------


def _children(element: ElementTree.Element) -> dict[str, ElementTree.Element]:
    """Child elements by name, without the namespace."""
    return {child.tag.rpartition("}")[2]: child for child in element}


def _elements(path: Path, name: str) -> Iterator[dict[str, ElementTree.Element]]:
    """Every top-level element of that name, read as a stream."""
    depth = 0
    for event, element in ElementTree.iterparse(path, events=("start", "end")):
        if event == "start":
            depth += 1
            continue
        depth -= 1
        if depth == 1:
            if element.tag.rpartition("}")[2] == name:
                yield _children(element)
            element.clear()


def arin_file(path: Path) -> Iterator[Inetnum]:
    """The IPv4 networks of ARIN's bulk file, one record per block of a network."""
    try:
        names = {
            (org["handle"].text or "").upper(): org["name"].text or ""
            for org in _elements(path, "org")
            if "handle" in org and "name" in org
        }
        for net in _elements(path, "net"):
            if "version" in net and (net["version"].text or "").strip() != "4":
                continue
            handle = (net["orgHandle"].text or "").upper() if "orgHandle" in net else ""
            name = (net["name"].text or "").strip() if "name" in net else ""
            for block in net["netBlocks"] if "netBlocks" in net else []:
                ends = _children(block)
                try:
                    first = parse_address(ends["startAddress"].text or "")
                    last = parse_address(ends["endAddress"].text or "")
                except KeyError, ValueError:
                    continue
                if first <= last:
                    yield Inetnum(first, last, name or None, names.get(handle) or None, None)
    except ElementTree.ParseError as error:
        raise DatasetError(f"{path.name} is not a readable XML file.") from error


# --- Loading -------------------------------------------------------------------------


async def load(db: AsyncSession, rir: str, records: Iterator[Inetnum]) -> int:
    """Replace the registry's records. Readers see the old set until the commit."""
    await db.execute(delete(RirInetnum).where(RirInetnum.rir == rir))
    total = 0
    while True:
        # Parsing runs in a thread, a batch at a time, so the event loop stays free.
        batch = await asyncio.to_thread(lambda: list(itertools.islice(records, BATCH_ROWS)))
        if not batch:
            break
        await copy_rows(
            db,
            "rir_inetnum",
            ("rir", "ip_range", "network_name", "organization", "country"),
            [
                (rir, address_range(r.first, r.last), r.network_name, r.organization, r.country)
                for r in batch
            ],
        )
        total += len(batch)
    if total == 0:
        raise DatasetError(f"The {rir} data holds no IPv4 network.")
    log.info("rir_objects_loaded", rir=rir, records=total)
    return total


async def _last_modified(client: httpx.AsyncClient, url: str) -> str:
    """When the file at the URL was last changed. This is the version of a dump."""
    try:
        response = await client.head(url, follow_redirects=True, timeout=TIMEOUT)
    except httpx.HTTPError as error:
        raise DatasetError(f"{url} cannot be reached: {type(error).__name__}.") from error
    if response.status_code != 200:
        raise DatasetError(f"{url} answered with status {response.status_code}.")
    try:
        changed = parsedate_to_datetime(response.headers["last-modified"])
    except KeyError, ValueError:
        return datetime.now(UTC).date().isoformat()
    return changed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _download(client: httpx.AsyncClient, url: str, target: Path) -> None:
    try:
        async with client.stream("GET", url, follow_redirects=True, timeout=TIMEOUT) as response:
            if response.status_code != 200:
                raise DatasetError(f"{url} answered with status {response.status_code}.")
            with target.open("wb") as file:
                async for chunk in response.aiter_bytes(CHUNK_BYTES):
                    file.write(chunk)
    except httpx.HTTPError as error:
        raise DatasetError(f"The download of {url} failed: {type(error).__name__}.") from error


async def update_downloaded(
    db: AsyncSession,
    rir: str,
    client: httpx.AsyncClient,
    directory: Path,
    current_version: str | None,
) -> str | None:
    """Download and load the dump of RIPE NCC, APNIC or AFRINIC."""
    urls = DOWNLOADS[rir]
    version = await _last_modified(client, urls[-1])
    if version == current_version:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    paths = [directory / f".{rir}-objects-{number}.part.gz" for number in range(len(urls))]
    try:
        for url, path in zip(urls, paths, strict=True):
            await _download(client, url, path)
        try:
            await load(db, rir, rpsl_files(paths))
        except (OSError, EOFError) as error:  # a truncated or broken gzip file
            raise DatasetError(f"The downloaded {rir} dump cannot be read.") from error
    finally:
        for path in paths:
            path.unlink(missing_ok=True)
    return version


async def update_supplied(
    db: AsyncSession, rir: str, directory: Path, current_version: str | None
) -> str | None:
    """Load the file the operator obtained from ARIN or LACNIC, if there is one."""
    path = directory / OPERATOR_FILES[rir]
    if not path.is_file():
        raise NotConfigured(f"There is no {path.name} in the directory for registry files.")
    # The version is the time the operator's file was last changed.
    changed = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    version = changed.strftime("%Y-%m-%dT%H:%M:%SZ")
    if version == current_version:
        return None
    await load(db, rir, arin_file(path) if rir == "arin" else rpsl_files([path]))
    return version


def datasets(settings: Settings, client: httpx.AsyncClient) -> list[Dataset]:
    interval = timedelta(hours=settings.rir_update_interval_hours)

    def downloaded(rir: str) -> Callable[..., Any]:
        async def run(db: AsyncSession, current_version: str | None) -> str | None:
            return await update_downloaded(
                db, rir, client, settings.reference_data_path, current_version
            )

        return run

    def supplied(rir: str, directory: Path) -> Callable[..., Any]:
        async def run(db: AsyncSession, current_version: str | None) -> str | None:
            return await update_supplied(db, rir, directory, current_version)

        return run

    result = []
    for rir in DOWNLOADS:
        enabled = rir in settings.rir_object_registries
        result.append(
            Dataset(
                name=f"rir-objects-{rir}",
                interval=interval,
                update=downloaded(rir),
                not_configured=None
                if enabled
                else f"Not enabled. Add {rir} to NDM_RIR_OBJECT_REGISTRIES if your use "
                "meets the registry's terms.",
            )
        )
    for rir in OPERATOR_FILES:
        directory = settings.rir_files_path
        result.append(
            Dataset(
                name=f"rir-objects-{rir}",
                interval=interval,
                update=supplied(rir, directory or Path()),
                not_configured=None
                if directory is not None
                else "No directory for registry files is configured.",
            )
        )
    return result
