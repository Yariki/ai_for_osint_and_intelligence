"""GeoLite2 City and ASN: download, verify, replace, read (architecture 11.1).

The databases are MaxMind's and are downloaded with the operator's own account
and licence key. None is in the repository or in an image (NFR-LIC-1). The key
is sent as HTTP basic authentication, never as part of a URL, so it cannot
end up in a logged or stored URL.

An update never disturbs a reader. The new database is written beside the old
one and then renamed over it, which is atomic: whoever has the old file open
keeps reading the complete old file, and whoever opens the name next gets the
complete new one.
"""

import asyncio
import hashlib
import os
import tarfile
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any

import httpx
import maxminddb
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.settings import Settings
from ndm.orchestrator.datasets.manager import Dataset, DatasetError

# Dataset name -> MaxMind's edition identifier.
EDITIONS = {"geolite2-city": "GeoLite2-City", "geolite2-asn": "GeoLite2-ASN"}

URL = "https://download.maxmind.com/geoip/databases/{edition}/download?suffix={suffix}"

# The licence requires an old database to be gone within 30 days of a new
# release. A database this old is no longer used.
MAX_AGE = timedelta(days=30)

DOWNLOAD_TIMEOUT = httpx.Timeout(30, read=120)
CHUNK_BYTES = 1 << 16


def database_path(directory: Path, edition: str) -> Path:
    return directory / f"{edition}.mmdb"


def build_date(path: Path, edition: str) -> str:
    """The version of a database file: the day it was built, from the file itself.

    Raises if the file is not a database of this edition.
    """
    try:
        with maxminddb.open_database(path) as reader:
            metadata = reader.metadata()
    except (maxminddb.InvalidDatabaseError, OSError) as error:
        raise DatasetError(f"The downloaded {edition} file is not a valid database.") from error
    if metadata.database_type != edition:
        raise DatasetError(
            f"The downloaded file is a {metadata.database_type} database, not {edition}."
        )
    return datetime.fromtimestamp(metadata.build_epoch, UTC).date().isoformat()


async def _get(
    client: httpx.AsyncClient, auth: httpx.Auth | None, edition: str, suffix: str
) -> httpx.Response:
    response = await client.get(
        URL.format(edition=edition, suffix=suffix),
        auth=auth,
        follow_redirects=True,
        timeout=DOWNLOAD_TIMEOUT,
    )
    _check(response, edition)
    return response


def _check(response: httpx.Response, edition: str) -> None:
    if response.status_code == 401:
        raise DatasetError(f"MaxMind refused the account ID or the licence key ({edition}).")
    if response.status_code != 200:
        raise DatasetError(f"MaxMind answered with status {response.status_code} for {edition}.")


async def _download(
    client: httpx.AsyncClient, auth: httpx.Auth | None, edition: str, target: Path
) -> str:
    """Download the archive to a file. Returns its SHA-256."""
    digest = hashlib.sha256()
    url = URL.format(edition=edition, suffix="tar.gz")
    async with client.stream(
        "GET", url, auth=auth, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT
    ) as response:
        _check(response, edition)
        with target.open("wb") as file:
            async for chunk in response.aiter_bytes(CHUNK_BYTES):
                digest.update(chunk)
                file.write(chunk)
    return digest.hexdigest()


def _unpack(archive: Path, edition: str, target: Path) -> str:
    """Take the database out of the archive and check it. Returns its version."""
    try:
        with tarfile.open(archive, "r:gz") as tar:
            member = next(
                (m for m in tar if m.isfile() and Path(m.name).name == f"{edition}.mmdb"), None
            )
            source = tar.extractfile(member) if member is not None else None
            if source is None:
                raise DatasetError(f"The downloaded archive holds no {edition}.mmdb.")
            with target.open("wb") as file:
                while chunk := source.read(CHUNK_BYTES):
                    file.write(chunk)
    except (tarfile.TarError, EOFError) as error:
        raise DatasetError(f"The downloaded {edition} archive cannot be read.") from error
    return build_date(target, edition)


async def update(
    edition: str,
    *,
    directory: Path,
    client: httpx.AsyncClient,
    auth: httpx.Auth | None,
    current_version: str | None,
) -> str | None:
    """Bring one database up to date. Returns the new version, or None if the
    one in place is the newest.

    On any failure the database in place is left exactly as it was.
    """
    directory.mkdir(parents=True, exist_ok=True)
    final = database_path(directory, edition)
    marker = directory / f"{edition}.sha256"
    archive = directory / f".{edition}.tar.gz.part"
    unpacked = directory / f".{edition}.mmdb.part"

    try:
        # The published checksum is small. It tells whether there is anything new.
        expected = (await _get(client, auth, edition, "tar.gz.sha256")).text.split()[0].lower()
        if final.exists() and marker.exists() and marker.read_text() == expected:
            installed = await asyncio.to_thread(build_date, final, edition)
            return None if installed == current_version else installed

        actual = await _download(client, auth, edition, archive)
        if actual != expected:
            raise DatasetError(
                f"The checksum of the downloaded {edition} archive is not the published one."
            )
        version = await asyncio.to_thread(_unpack, archive, edition, unpacked)
        os.replace(unpacked, final)
        marker.write_text(expected)
        return version
    except httpx.HTTPError as error:
        raise DatasetError(f"The download of {edition} failed: {type(error).__name__}.") from error
    except IndexError as error:
        raise DatasetError(f"MaxMind published no checksum for {edition}.") from error
    finally:
        archive.unlink(missing_ok=True)
        unpacked.unlink(missing_ok=True)


class GeoLite:
    """The readers of the two databases. Reopened after an update."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._readers: dict[str, maxminddb.Reader] = {}

    def reload(self) -> None:
        """Open the databases that are in place now. The readers opened before are closed."""
        for name, edition in EDITIONS.items():
            previous = self._readers.pop(name, None)
            path = database_path(self._directory, edition)
            if path.exists():
                self._readers[name] = maxminddb.open_database(path)
            if previous is not None:
                previous.close()

    def close(self) -> None:
        for reader in self._readers.values():
            reader.close()
        self._readers = {}

    def available(self, name: str) -> bool:
        """Whether the database of that dataset is open for reading."""
        return name in self._readers

    def city(self, address: IPv4Address) -> dict[str, Any] | None:
        return self._get("geolite2-city", address)

    def asn(self, address: IPv4Address) -> dict[str, Any] | None:
        return self._get("geolite2-asn", address)

    def _get(self, name: str, address: IPv4Address) -> dict[str, Any] | None:
        reader = self._readers.get(name)
        record = reader.get(str(address)) if reader is not None else None
        return record if isinstance(record, dict) else None


def datasets(settings: Settings, client: httpx.AsyncClient, readers: GeoLite) -> list[Dataset]:
    """The two GeoLite2 datasets of this installation, for the dataset manager."""
    key = settings.maxmind_license_key
    auth: httpx.Auth | None = None
    not_configured: str | None = "No MaxMind account ID and licence key are configured."
    if key is not None and settings.maxmind_account_id is not None:
        auth = httpx.BasicAuth(settings.maxmind_account_id, key.get_secret_value())
        not_configured = None

    def loader(edition: str) -> Any:
        async def load(_db: AsyncSession, current_version: str | None) -> str | None:
            version = await update(
                edition,
                directory=settings.reference_data_path,
                client=client,
                auth=auth,
                current_version=current_version,
            )
            if version is not None:
                readers.reload()
            return version

        return load

    return [
        Dataset(
            name=name,
            interval=timedelta(hours=settings.geolite_update_interval_hours),
            update=loader(edition),
            not_configured=not_configured,
            max_age=MAX_AGE,
        )
        for name, edition in EDITIONS.items()
    ]
