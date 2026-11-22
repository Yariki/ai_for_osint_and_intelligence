"""GeoLite2 updates (check 2.2, GeoLite2 part; NFR-LIC-1).

No GeoLite2 file is used: the databases are built in the test, and a stand-in
answers for MaxMind's download service.
"""

import io
import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime
from ipaddress import IPv4Address
from pathlib import Path

import httpx
import maxminddb
import pytest
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ndm.core.logging import configure_logging
from ndm.core.settings import Settings
from ndm.db.models import AuditEvent, ReferenceDataset
from ndm.orchestrator.datasets import geolite
from ndm.orchestrator.datasets.manager import DatasetError, DatasetManager
from tests.tools.mmdb import ACCOUNT_ID, LICENSE_KEY, FakeMaxMind, archive, asn, city

CITY = "GeoLite2-City"
ASN = "GeoLite2-ASN"
ADDRESS = IPv4Address("8.8.8.8")
OLD = {"8.8.8.0/24": city("Mountain View", "US", 37.4056, -122.0775, 1000)}
NEW = {
    "8.8.8.0/24": city("Sunnyvale", "US", 37.3688, -122.0363, 20),
    "9.9.9.0/24": city("Zurich", "CH", 47.3667, 8.55, 50),
}
AUTH = httpx.BasicAuth(ACCOUNT_ID, LICENSE_KEY)
NOW = datetime(2026, 11, 16, 9, tzinfo=UTC)


@pytest.fixture
def maxmind() -> FakeMaxMind:
    server = FakeMaxMind()
    server.publish(CITY, archive(CITY, OLD, date(2026, 11, 10)))
    server.publish(ASN, archive(ASN, {"8.8.8.0/24": asn(15169, "GOOGLE")}, date(2026, 11, 10)))
    return server


@pytest.fixture
async def client(maxmind: FakeMaxMind) -> AsyncIterator[httpx.AsyncClient]:
    async with maxmind.client() as http:
        yield http


async def update_city(
    client: httpx.AsyncClient, directory: Path, current: str | None = None
) -> str | None:
    return await geolite.update(
        CITY, directory=directory, client=client, auth=AUTH, current_version=current
    )


def city_name(reader: maxminddb.Reader, address: str = "8.8.8.8") -> str | None:
    record = reader.get(address)
    return record["city"]["names"]["en"] if isinstance(record, dict) else None  # type: ignore[index,call-overload,return-value]


def leftovers(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir() if path.name.startswith("."))


# --- A successful update ---


async def test_first_update_installs_the_database(
    client: httpx.AsyncClient, tmp_path: Path, maxmind: FakeMaxMind
) -> None:
    version = await update_city(client, tmp_path)

    # The version is the build date in the file.
    assert version == "2026-11-10"
    with maxminddb.open_database(geolite.database_path(tmp_path, CITY)) as reader:
        assert city_name(reader) == "Mountain View"
    # Only the database and its checksum remain: no archive, no partial file.
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "GeoLite2-City.mmdb",
        "GeoLite2-City.sha256",
    ]


async def test_nothing_is_downloaded_when_the_published_checksum_is_unchanged(
    client: httpx.AsyncClient, tmp_path: Path, maxmind: FakeMaxMind
) -> None:
    version = await update_city(client, tmp_path)

    assert await update_city(client, tmp_path, current=version) is None
    assert await update_city(client, tmp_path, current=version) is None
    assert maxmind.downloads(CITY) == 1


async def test_a_new_release_replaces_the_database(
    client: httpx.AsyncClient, tmp_path: Path, maxmind: FakeMaxMind
) -> None:
    first = await update_city(client, tmp_path)
    maxmind.publish(CITY, archive(CITY, NEW, date(2026, 11, 13)))

    second = await update_city(client, tmp_path, current=first)

    assert second == "2026-11-13"
    with maxminddb.open_database(geolite.database_path(tmp_path, CITY)) as reader:
        assert city_name(reader) == "Sunnyvale"
        assert city_name(reader, "9.9.9.9") == "Zurich"


async def test_reader_during_an_update_sees_the_complete_old_file(
    client: httpx.AsyncClient, tmp_path: Path, maxmind: FakeMaxMind
) -> None:
    await update_city(client, tmp_path)
    readers = geolite.GeoLite(tmp_path)
    readers.reload()
    old_reader = maxminddb.open_database(geolite.database_path(tmp_path, CITY))
    old_bytes = geolite.database_path(tmp_path, CITY).read_bytes()
    maxmind.publish(CITY, archive(CITY, NEW, date(2026, 11, 13)))

    await update_city(client, tmp_path, current="2026-11-10")

    # The file was replaced, yet whoever had it open still reads the old one, whole.
    assert geolite.database_path(tmp_path, CITY).read_bytes() != old_bytes
    assert city_name(old_reader) == "Mountain View"
    assert old_reader.get("9.9.9.9") is None
    assert old_reader.metadata().build_epoch == int(
        datetime(2026, 11, 10, 12, tzinfo=UTC).timestamp()
    )
    assert readers.city(ADDRESS)["city"]["names"]["en"] == "Mountain View"  # type: ignore[index]
    old_reader.close()

    # Reopened, the readers give the new data.
    readers.reload()
    assert readers.city(ADDRESS)["city"]["names"]["en"] == "Sunnyvale"  # type: ignore[index]
    assert readers.city(IPv4Address("9.9.9.9")) is not None
    readers.close()


async def test_the_key_is_sent_to_maxmind_only_and_never_in_a_url(
    client: httpx.AsyncClient, tmp_path: Path, maxmind: FakeMaxMind
) -> None:
    await update_city(client, tmp_path)

    hosts = {request.url.host for request in maxmind.requests}
    assert hosts == {"download.maxmind.com", "storage.example"}
    for request in maxmind.requests:
        assert LICENSE_KEY not in str(request.url)
        assert ("authorization" in request.headers) == (request.url.host == "download.maxmind.com")


# --- Failures keep the previous version ---


async def test_checksum_mismatch_keeps_the_previous_database(
    client: httpx.AsyncClient, tmp_path: Path, maxmind: FakeMaxMind
) -> None:
    await update_city(client, tmp_path)
    before = geolite.database_path(tmp_path, CITY).read_bytes()
    maxmind.publish(CITY, archive(CITY, NEW, date(2026, 11, 13)))
    maxmind.published_checksums[CITY] = "0" * 64

    with pytest.raises(DatasetError, match="checksum"):
        await update_city(client, tmp_path, current="2026-11-10")

    assert geolite.database_path(tmp_path, CITY).read_bytes() == before
    assert leftovers(tmp_path) == []


@pytest.mark.parametrize("status", [500, 429, 404])
async def test_failed_download_keeps_the_previous_database(
    client: httpx.AsyncClient, tmp_path: Path, maxmind: FakeMaxMind, status: int
) -> None:
    await update_city(client, tmp_path)
    before = geolite.database_path(tmp_path, CITY).read_bytes()
    maxmind.status = status

    with pytest.raises(DatasetError, match=str(status)):
        await update_city(client, tmp_path, current="2026-11-10")

    assert geolite.database_path(tmp_path, CITY).read_bytes() == before
    assert leftovers(tmp_path) == []


async def test_connection_failure_is_reported_as_a_failed_download(tmp_path: Path) -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(unreachable)) as offline:
        with pytest.raises(DatasetError, match="ConnectError"):
            await update_city(offline, tmp_path)

    assert list(tmp_path.iterdir()) == []


async def test_wrong_key_is_reported_without_repeating_it(
    tmp_path: Path, maxmind: FakeMaxMind, client: httpx.AsyncClient
) -> None:
    wrong = httpx.BasicAuth(ACCOUNT_ID, "a-wrong-key-55")

    with pytest.raises(DatasetError) as failure:
        await geolite.update(
            CITY, directory=tmp_path, client=client, auth=wrong, current_version=None
        )

    assert "refused" in str(failure.value)
    assert "a-wrong-key-55" not in str(failure.value)


async def test_archive_of_another_edition_is_refused(
    client: httpx.AsyncClient, tmp_path: Path, maxmind: FakeMaxMind
) -> None:
    await update_city(client, tmp_path)
    before = geolite.database_path(tmp_path, CITY).read_bytes()
    # MaxMind serves an ASN database under the City name.
    wrong = archive(ASN, {"8.8.8.0/24": asn(15169, "GOOGLE")}, date(2026, 11, 13))
    maxmind.publish(CITY, wrong)

    with pytest.raises(DatasetError, match="holds no GeoLite2-City.mmdb"):
        await update_city(client, tmp_path, current="2026-11-10")

    assert geolite.database_path(tmp_path, CITY).read_bytes() == before
    assert leftovers(tmp_path) == []


async def test_archive_that_is_not_an_archive_is_refused(
    client: httpx.AsyncClient, tmp_path: Path, maxmind: FakeMaxMind
) -> None:
    maxmind.publish(CITY, b"<html>temporarily unavailable</html>")

    with pytest.raises(DatasetError, match="cannot be read"):
        await update_city(client, tmp_path)

    assert list(tmp_path.iterdir()) == []


# --- Through the dataset manager: version, audit entry, and no key anywhere ---


@pytest.fixture
def sessions(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(app_engine, expire_on_commit=False)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    monkeypatch.setenv("NDM_MAXMIND_ACCOUNT_ID", ACCOUNT_ID)
    monkeypatch.setenv("NDM_MAXMIND_LICENSE_KEY", LICENSE_KEY)
    monkeypatch.setenv("NDM_REFERENCE_DATA_PATH", str(tmp_path))
    return Settings()


@pytest.fixture
def log_output(settings: Settings) -> Iterator[io.StringIO]:
    import logging

    root = logging.getLogger()
    handlers, level = root.handlers, root.level
    stream = io.StringIO()
    configure_logging(settings.secret_values(), stream=stream)
    yield stream
    root.handlers, root.level = handlers, level
    structlog.reset_defaults()


async def test_update_through_the_manager_sets_version_load_time_and_one_audit_entry(
    db_session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: httpx.AsyncClient,
    maxmind: FakeMaxMind,
) -> None:
    readers = geolite.GeoLite(settings.reference_data_path)
    manager = DatasetManager(sessions, geolite.datasets(settings, client, readers))

    assert await manager.run_due(NOW) == ["geolite2-city", "geolite2-asn"]

    rows = {row.name: row for row in await db_session.scalars(select(ReferenceDataset))}
    for name in ("geolite2-city", "geolite2-asn"):
        assert (rows[name].version, rows[name].status) == ("2026-11-10", "ok")
        assert (rows[name].loaded_at, rows[name].checked_at) == (NOW, NOW)
    audit = (await db_session.execute(select(AuditEvent.action, AuditEvent.object_id))).all()
    assert sorted(tuple(row) for row in audit) == [
        ("dataset.update", "geolite2-asn"),
        ("dataset.update", "geolite2-city"),
    ]
    # The manager's update reopened the readers.
    assert readers.asn(ADDRESS) == asn(15169, "GOOGLE")
    assert readers.city(ADDRESS) is not None
    readers.close()


async def test_the_licence_key_is_in_no_log_line_and_in_nothing_that_is_stored(
    db_session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    maxmind: FakeMaxMind,
    log_output: io.StringIO,
) -> None:
    def leaky(request: httpx.Request) -> httpx.Response:
        # A failure whose message repeats the credentials, as a careless library might.
        raise httpx.ConnectError(f"cannot connect with {request.headers['authorization']}")

    readers = geolite.GeoLite(settings.reference_data_path)
    async with maxmind.client() as working:
        manager = DatasetManager(
            sessions, geolite.datasets(settings, working, readers), settings.secret_values()
        )
        await manager.run_due(NOW)
    maxmind.status = 503
    async with maxmind.client() as failing:
        manager = DatasetManager(
            sessions, geolite.datasets(settings, failing, readers), settings.secret_values()
        )
        await manager.run_due(datetime(2026, 11, 17, 10, tzinfo=UTC))

    # Deliberately: an unexpected error that carries the key in its text.
    async def explodes(_db: AsyncSession, _current: str | None) -> str | None:
        raise RuntimeError(f"request to https://{ACCOUNT_ID}:{LICENSE_KEY}@download.example failed")

    [dataset] = [
        d for d in geolite.datasets(settings, working, readers) if d.name == "geolite2-city"
    ]
    broken = type(dataset)(name=dataset.name, interval=dataset.interval, update=explodes)
    await DatasetManager(sessions, [broken], settings.secret_values()).run_due(
        datetime(2026, 11, 18, 10, tzinfo=UTC)
    )
    readers.close()

    stored = [
        *(await db_session.scalars(select(ReferenceDataset.detail))),
        *(json.dumps(d) for d in await db_session.scalars(select(AuditEvent.details))),
    ]
    assert any("[REDACTED]" in (text or "") for text in stored)
    assert any("503" in (text or "") for text in stored)
    for text in stored:
        assert LICENSE_KEY not in (text or "")
    lines = log_output.getvalue()
    assert "dataset_update_failed" in lines
    assert LICENSE_KEY not in lines
    # The previous version is still the one in use.
    city_row = await db_session.scalar(
        select(ReferenceDataset).where(ReferenceDataset.name == "geolite2-city")
    )
    assert city_row is not None
    assert (city_row.version, city_row.status) == ("2026-11-10", "ok")


async def test_without_a_key_nothing_is_requested(
    db_session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    maxmind: FakeMaxMind,
    client: httpx.AsyncClient,
) -> None:
    for name in ("NDM_MAXMIND_ACCOUNT_ID", "NDM_MAXMIND_LICENSE_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NDM_REFERENCE_DATA_PATH", str(tmp_path))
    readers = geolite.GeoLite(tmp_path)

    changed = await DatasetManager(sessions, geolite.datasets(Settings(), client, readers)).run_due(
        NOW
    )

    assert changed == []
    assert maxmind.requests == []
    rows = (
        await db_session.execute(select(ReferenceDataset.status, ReferenceDataset.detail))
    ).all()
    assert [tuple(row) for row in rows] == [
        ("missing", "No MaxMind account ID and licence key are configured.")
    ] * 2
    readers.reload()
    assert readers.city(ADDRESS) is None
