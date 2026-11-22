"""Small MaxMind databases for tests, built on the spot.

No GeoLite2 file is in the repository (NFR-LIC-1). These hold a handful of
made-up records in the layout of the real databases.
"""

import hashlib
import io
import tarfile
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
from mmdb_writer import MMDBWriter
from netaddr import IPSet  # type: ignore[import-untyped]

ACCOUNT_ID = "123456"
LICENSE_KEY = "test-licence-key-7f3a"


def city(name: str, country: str, latitude: float, longitude: float, radius: int) -> dict[str, Any]:
    """A record as GeoLite2 City holds it."""
    return {
        "city": {"names": {"en": name}},
        "country": {"iso_code": country, "names": {"en": country}},
        "location": {"latitude": latitude, "longitude": longitude, "accuracy_radius": radius},
    }


def asn(number: int, holder: str) -> dict[str, Any]:
    """A record as GeoLite2 ASN holds it."""
    return {"autonomous_system_number": number, "autonomous_system_organization": holder}


def write_database(
    path: Path, edition: str, records: dict[str, dict[str, Any]], built: date
) -> None:
    """Write a database of the given edition with one record per network."""
    writer = MMDBWriter(ip_version=4, database_type=edition, languages=["en"])
    for network, record in records.items():
        writer.insert_network(IPSet([network]), record)
    epoch = datetime(built.year, built.month, built.day, 12, tzinfo=UTC).timestamp()
    with mock.patch("mmdb_writer.time.time", return_value=epoch):
        writer.to_db_file(str(path))


def archive(edition: str, records: dict[str, dict[str, Any]], built: date) -> bytes:
    """A tar.gz as MaxMind serves it: one directory with the database in it."""
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / f"{edition}.mmdb"
        write_database(database, edition, records, built)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            folder = f"{edition}_{built:%Y%m%d}"
            tar.add(database, arcname=f"{folder}/{edition}.mmdb")
            notice = tarfile.TarInfo(f"{folder}/COPYRIGHT.txt")
            notice.size = 4
            tar.addfile(notice, io.BytesIO(b"test"))
        return buffer.getvalue()


class FakeMaxMind:
    """Stands in for MaxMind's download service: checksum, redirect, archive."""

    def __init__(self) -> None:
        self.archives: dict[str, bytes] = {}
        self.published_checksums: dict[str, str] = {}
        self.status = 200
        self.requests: list[httpx.Request] = []

    def publish(self, edition: str, content: bytes) -> None:
        self.archives[edition] = content
        self.published_checksums[edition] = hashlib.sha256(content).hexdigest()

    def downloads(self, edition: str) -> int:
        """How often the archive itself was fetched."""
        return sum(request.url.path == f"/{edition}.tar.gz" for request in self.requests)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._answer))

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "storage.example":
            # The real service redirects to a storage host. The key must not follow.
            assert "authorization" not in request.headers
            return httpx.Response(200, content=self.archives[request.url.path[1:-7]])

        assert request.url.host == "download.maxmind.com"
        expected = httpx.BasicAuth(ACCOUNT_ID, LICENSE_KEY)._auth_header
        if request.headers.get("authorization") != expected:
            return httpx.Response(401, text="Invalid license key")
        if self.status != 200:
            return httpx.Response(self.status, text="unavailable")
        edition = request.url.path.split("/")[3]
        if request.url.params["suffix"] == "tar.gz.sha256":
            name = f"{edition}_20261113.tar.gz"
            return httpx.Response(200, text=f"{self.published_checksums[edition]}  {name}\n")
        return httpx.Response(
            302, headers={"location": f"https://storage.example/{edition}.tar.gz"}
        )
