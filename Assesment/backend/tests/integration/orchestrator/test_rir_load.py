"""Registry data: loading and lookup (checks 2.1 and 2.2, registry part).

A stand-in answers for the registries' download servers with the fixture files.
"""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ndm.core.settings import Settings
from ndm.db.models import AuditEvent, ReferenceDataset, RirDelegation, RirInetnum
from ndm.orchestrator import datasets as datasets_package
from ndm.orchestrator.datasets import geolite, rir_delegation, rir_objects
from ndm.orchestrator.datasets.manager import Dataset, DatasetManager
from ndm.orchestrator.datasets.rir_lookup import LOOKUP, Registration, lookup, organization
from tests.tools.registries import FakeRegistries, supply_operator_files

NOW = datetime(2026, 11, 16, 9, tzinfo=UTC)
WEEK = timedelta(days=7)


@pytest.fixture
def registries() -> FakeRegistries:
    return FakeRegistries()


@pytest.fixture
async def client(registries: FakeRegistries) -> AsyncIterator[httpx.AsyncClient]:
    async with registries.client() as http:
        yield http


@pytest.fixture
def sessions(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(app_engine, expire_on_commit=False)


@pytest.fixture
def files_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "registry-files"
    directory.mkdir()
    return directory


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, files_directory: Path) -> Settings:
    for name in list(os.environ):
        if name.startswith("NDM_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("NDM_REFERENCE_DATA_PATH", str(tmp_path / "reference"))
    monkeypatch.setenv("NDM_RIR_FILES_PATH", str(files_directory))
    monkeypatch.setenv("NDM_RIR_OBJECT_REGISTRIES", "ripe,apnic,afrinic")
    return Settings()


def ip(address: str) -> int:
    return int(IPv4Address(address))


async def count(db: AsyncSession, model: Any, rir: str | None = None) -> int:
    await db.commit()
    query = select(func.count()).select_from(model)
    return await db.scalar(query.where(model.rir == rir) if rir else query) or 0


async def dataset_row(db: AsyncSession, name: str) -> ReferenceDataset:
    await db.commit()
    return (
        await db.scalars(
            select(ReferenceDataset)
            .where(ReferenceDataset.name == name)
            .execution_options(populate_existing=True)
        )
    ).one()


async def load_everything(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, client: httpx.AsyncClient
) -> list[str]:
    registry_datasets = [
        *rir_delegation.datasets(settings, client),
        *rir_objects.datasets(settings, client),
    ]
    return await DatasetManager(sessions, registry_datasets).run_due(NOW)


# --- Delegation statistics ---


async def test_statistics_of_all_five_registries_are_loaded(
    db_session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: httpx.AsyncClient,
) -> None:
    manager = DatasetManager(sessions, rir_delegation.datasets(settings, client))

    changed = await manager.run_due(NOW)

    assert changed == [
        f"rir-delegation-{rir}" for rir in ("ripe", "apnic", "arin", "lacnic", "afrinic")
    ]
    loaded = {rir: await count(db_session, RirDelegation, rir) for rir in rir_delegation.SOURCES}
    assert loaded == {"ripe": 4, "apnic": 2, "arin": 2, "lacnic": 2, "afrinic": 2}
    # The version is the last day the file covers; each update has its audit entry.
    assert (await dataset_row(db_session, "rir-delegation-ripe")).version == "2026-11-15"
    assert (await dataset_row(db_session, "rir-delegation-lacnic")).version == "2026-11-13"
    actions = await db_session.scalars(select(AuditEvent.action))
    assert list(actions) == ["dataset.update"] * 5
    # RIPE's 768 addresses are stored as one range of that size.
    size = await db_session.scalar(
        select(func.upper(RirDelegation.ip_range) - func.lower(RirDelegation.ip_range)).where(
            RirDelegation.country == "DE"
        )
    )
    assert size == 768


async def test_unchanged_statistics_are_not_loaded_again(
    db_session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: httpx.AsyncClient,
) -> None:
    manager = DatasetManager(sessions, rir_delegation.datasets(settings, client))
    await manager.run_due(NOW)
    ids = set(await db_session.scalars(select(RirDelegation.id)))

    assert await manager.run_due(NOW + WEEK) == []

    await db_session.commit()
    assert set(await db_session.scalars(select(RirDelegation.id))) == ids
    row = await dataset_row(db_session, "rir-delegation-ripe")
    assert (row.loaded_at, row.checked_at) == (NOW, NOW + WEEK)


async def test_statistics_with_a_wrong_checksum_keep_the_previous_set(
    db_session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: httpx.AsyncClient,
    registries: FakeRegistries,
) -> None:
    manager = DatasetManager(sessions, rir_delegation.datasets(settings, client))
    await manager.run_due(NOW)
    url = rir_delegation.SOURCES["apnic"][1]
    newer = registries.files[url].replace(b"20261116|+1000", b"20261123|+1000")
    # The file arrives damaged: what is served is not what the checksum was made of.
    registries.files[url] = newer

    def damaged(request: httpx.Request) -> httpx.Response:
        response = registries._answer(request)
        if str(request.url) == url:
            return httpx.Response(200, content=newer[:-40])
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(damaged)) as bad:
        changed = await DatasetManager(sessions, rir_delegation.datasets(settings, bad)).run_due(
            NOW + WEEK
        )

    assert "rir-delegation-apnic" not in changed
    assert await count(db_session, RirDelegation, "apnic") == 2
    row = await dataset_row(db_session, "rir-delegation-apnic")
    assert (row.version, row.status) == ("2026-11-16", "ok")
    assert row.detail == "The checksum of the apnic file is not the published one."


async def test_reader_during_a_load_sees_the_complete_old_set(
    db_session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: httpx.AsyncClient,
    registries: FakeRegistries,
) -> None:
    await load_everything(sessions, settings, client)
    old_delegations = set(await db_session.scalars(select(RirDelegation.id)))
    old_networks = set(await db_session.scalars(select(RirInetnum.id)))
    await db_session.commit()
    # New files: one more delegation, and a dump with a different set of networks.
    url = rir_delegation.SOURCES["ripe"][1]
    registries.files[url] = (
        registries.files[url].replace(b"20261115|+0100", b"20261122|+0100")
        + b"ripencc|SE|ipv4|193.1.0.0|256|20261120|assigned|x\n"
    )
    registries.last_modified = "Sun, 22 Nov 2026 23:20:53 GMT"
    seen: dict[str, tuple[set[int], set[int]]] = {}

    def watched(name: str, real: Dataset) -> Dataset:
        async def update(db: AsyncSession, current: str | None) -> str | None:
            version = await real.update(db, current)
            # The new rows are written but not committed. What does a reader see?
            async with sessions() as reader:
                seen[name] = (
                    set(await reader.scalars(select(RirDelegation.id))),
                    set(await reader.scalars(select(RirInetnum.id))),
                )
            return version

        return Dataset(name=real.name, interval=real.interval, update=update)

    watched_datasets = [
        watched(d.name, d)
        for d in (
            *rir_delegation.datasets(settings, client),
            *rir_objects.datasets(settings, client),
        )
        if d.name in ("rir-delegation-ripe", "rir-objects-ripe")
    ]
    changed = await DatasetManager(sessions, watched_datasets).run_due(NOW + WEEK)

    assert changed == ["rir-delegation-ripe", "rir-objects-ripe"]
    # During the first load the reader saw every old row and no new one.
    assert seen["rir-delegation-ripe"] == (old_delegations, old_networks)
    # During the second, the delegations were the new set, the networks still the old one.
    assert seen["rir-objects-ripe"][1] == old_networks
    # After the commit the registry's rows are the new ones, all of them.
    assert await count(db_session, RirDelegation, "ripe") == 5
    new_networks = set(
        await db_session.scalars(select(RirInetnum.id).where(RirInetnum.rir == "ripe"))
    )
    assert len(new_networks) == 5 and not new_networks & old_networks
    # The other registries were not touched.
    assert await count(db_session, RirInetnum, "apnic") == 2


# --- Detailed objects ---


async def test_objects_are_loaded_for_the_registries_the_operator_listed(
    db_session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: httpx.AsyncClient,
    files_directory: Path,
) -> None:
    supply_operator_files(files_directory)

    changed = await DatasetManager(sessions, rir_objects.datasets(settings, client)).run_due(NOW)

    assert changed == [
        f"rir-objects-{rir}" for rir in ("ripe", "apnic", "afrinic", "arin", "lacnic")
    ]
    loaded = {
        rir: await count(db_session, RirInetnum, rir)
        for rir in ("ripe", "apnic", "afrinic", "arin", "lacnic")
    }
    assert loaded == {"ripe": 5, "apnic": 2, "afrinic": 2, "arin": 4, "lacnic": 3}
    # The version of a dump is the time the registry last changed it.
    assert (await dataset_row(db_session, "rir-objects-ripe")).version == "2026-11-15T23:20:53Z"
    stored = (
        await db_session.execute(
            select(RirInetnum.network_name, RirInetnum.organization, RirInetnum.country).where(
                RirInetnum.ip_range == func.int8range(ip("193.0.6.0"), ip("193.0.6.255") + 1)
            )
        )
    ).one()
    assert tuple(stored) == ("EXAMPLE-UNIVERSITY", "University of Example", "NL")
    # Nothing of the download is left on the volume.
    assert list(settings.reference_data_path.iterdir()) == []


async def test_objects_of_a_registry_that_is_not_listed_are_never_requested(
    db_session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    registries: FakeRegistries,
    client: httpx.AsyncClient,
) -> None:
    monkeypatch.setenv("NDM_RIR_OBJECT_REGISTRIES", "apnic")
    monkeypatch.delenv("NDM_RIR_FILES_PATH")

    changed = await load_everything(sessions, Settings(), client)

    assert [name for name in changed if "objects" in name] == ["rir-objects-apnic"]
    asked = {url for _, url in registries.requests}
    assert not any("ripe.net/ripe/dbase" in url or "afrinic.net/pub/dbase" in url for url in asked)
    assert await count(db_session, RirInetnum) == 2
    row = await dataset_row(db_session, "rir-objects-ripe")
    assert (row.status, row.checked_at) == ("missing", None)
    assert row.detail is not None and "NDM_RIR_OBJECT_REGISTRIES" in row.detail
    arin = await dataset_row(db_session, "rir-objects-arin")
    assert arin.detail == "No directory for registry files is configured."


async def test_by_default_no_registry_objects_are_downloaded(
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    registries: FakeRegistries,
    client: httpx.AsyncClient,
) -> None:
    monkeypatch.delenv("NDM_RIR_OBJECT_REGISTRIES")

    changed = await load_everything(sessions, Settings(), client)

    assert len(changed) == 5 and all(name.startswith("rir-delegation-") for name in changed)
    assert registries.fetched("dbase") == 0 and registries.fetched("whois") == 0


async def test_unchanged_dump_is_not_downloaded_again(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    registries: FakeRegistries,
    client: httpx.AsyncClient,
) -> None:
    manager = DatasetManager(sessions, rir_objects.datasets(settings, client))
    await manager.run_due(NOW)
    assert registries.fetched("ripe.db.inetnum") == 1

    assert await manager.run_due(NOW + WEEK) == []
    assert registries.fetched("ripe.db.inetnum") == 1

    registries.last_modified = "Sun, 22 Nov 2026 23:20:53 GMT"
    assert "rir-objects-ripe" in await manager.run_due(NOW + 2 * WEEK)
    assert registries.fetched("ripe.db.inetnum") == 2


async def test_missing_operator_file_is_not_a_failure(
    db_session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: httpx.AsyncClient,
    files_directory: Path,
) -> None:
    manager = DatasetManager(sessions, rir_objects.datasets(settings, client))

    await manager.run_due(NOW)

    row = await dataset_row(db_session, "rir-objects-arin")
    assert (row.status, row.version) == ("missing", None)
    assert row.detail == "There is no arin_db.xml in the directory for registry files."
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.object_id == "rir-objects-arin")
        )
        == 0
    )

    # The operator puts the file there. It is loaded at the next check, or on request.
    supply_operator_files(files_directory)
    row.update_requested_at = NOW
    await db_session.commit()
    # Only the dataset that was asked for is loaded now; the other waits for its interval.
    assert await manager.run_due(NOW + timedelta(minutes=5)) == ["rir-objects-arin"]
    assert await manager.run_due(NOW + WEEK) == ["rir-objects-lacnic"]
    row = await dataset_row(db_session, "rir-objects-arin")
    assert (row.status, row.detail) == ("ok", None)


async def test_replaced_operator_file_is_loaded_and_an_unchanged_one_is_not(
    db_session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: httpx.AsyncClient,
    files_directory: Path,
) -> None:
    supply_operator_files(files_directory)
    manager = DatasetManager(sessions, rir_objects.datasets(settings, client))
    await manager.run_due(NOW)
    first = (await dataset_row(db_session, "rir-objects-lacnic")).version

    assert "rir-objects-lacnic" not in await manager.run_due(NOW + WEEK)

    path = files_directory / "lacnic_db.txt"
    path.write_text("inetnum: 200.3.12/22\nowner: Otro Titular\ncountry: UY\n")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 3600))
    assert "rir-objects-lacnic" in await manager.run_due(NOW + 2 * WEEK)
    assert (await dataset_row(db_session, "rir-objects-lacnic")).version != first
    owners = await db_session.scalars(
        select(RirInetnum.organization).where(RirInetnum.rir == "lacnic")
    )
    assert list(owners) == ["Otro Titular"]


async def test_broken_dump_keeps_the_previous_set(
    db_session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    registries: FakeRegistries,
    client: httpx.AsyncClient,
) -> None:
    manager = DatasetManager(sessions, rir_objects.datasets(settings, client))
    await manager.run_due(NOW)
    url = rir_objects.DOWNLOADS["ripe"][1]
    registries.files[url] = registries.files[url][:60]  # the download was cut off
    registries.last_modified = "Sun, 22 Nov 2026 23:20:53 GMT"

    changed = await manager.run_due(NOW + WEEK)

    assert "rir-objects-ripe" not in changed
    assert await count(db_session, RirInetnum, "ripe") == 5
    row = await dataset_row(db_session, "rir-objects-ripe")
    assert (row.version, row.status) == ("2026-11-15T23:20:53Z", "ok")
    assert row.detail == "The downloaded ripe dump cannot be read."
    assert list(settings.reference_data_path.iterdir()) == []


# --- Lookup (check 2.1) ---


@pytest.fixture
async def loaded(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: httpx.AsyncClient,
    files_directory: Path,
) -> None:
    supply_operator_files(files_directory)
    await load_everything(sessions, settings, client)


async def test_the_most_specific_range_that_covers_the_address_wins(
    db_session: AsyncSession, loaded: None
) -> None:
    inside, beside = IPv4Address("193.0.6.139"), IPv4Address("193.0.5.1")

    found = await lookup(db_session, [inside, beside])

    # 193.0.6.0/24 lies inside 193.0.0.0/21; an address beside it gets the wider record.
    assert found[inside] == Registration(
        "ripe", "NL", "EXAMPLE-UNIVERSITY", "University of Example"
    )
    assert found[beside] == Registration(
        "ripe", "NL", "NL-EXAMPLE-19930901", "Example Networks B.V."
    )
    assert organization(found[inside], "EXAMPLE-AS") == ("University of Example", "registry")


async def test_every_registry_and_format_answers_for_its_own_space(
    db_session: AsyncSession, loaded: None
) -> None:
    addresses = {
        "apnic": IPv4Address("1.0.0.7"),
        "afrinic": IPv4Address("41.10.20.30"),
        "arin": IPv4Address("8.8.8.8"),
        "lacnic": IPv4Address("200.3.13.9"),
    }

    found = await lookup(db_session, list(addresses.values()))

    assert found[addresses["apnic"]] == Registration(
        "apnic", "AU", "EXAMPLE-LIBRARY-AU", "Example Library of Australia"
    )
    assert found[addresses["afrinic"]] == Registration(
        "afrinic", "ZA", "EXAMPLE-ZA-MOBILE", "Example Mobile (Pty) Ltd"
    )
    # ARIN: the /24 of the network with two blocks, not the /8 around it.
    assert found[addresses["arin"]] == Registration("arin", "US", "EXAMPLE-DNS", "Example DNS LLC")
    # LACNIC: the reassigned /24 inside the /22; LACNIC objects carry no network name.
    assert found[addresses["lacnic"]] == Registration("lacnic", "UY", None, "Cliente de Ejemplo")


async def test_without_a_record_the_organization_falls_back_to_the_as_holder(
    db_session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: httpx.AsyncClient,
) -> None:
    # No ARIN file was supplied. RIPE's dump holds a placeholder for ARIN's 8.0.0.0/8.
    await load_everything(sessions, settings, client)
    address = IPv4Address("8.8.8.8")

    found = (await lookup(db_session, [address]))[address]

    # Registry and registration country still come from the delegation statistics.
    # The placeholder of another registry is not taken for a registration.
    assert found == Registration("arin", "US", None, None)
    assert organization(found, "EXAMPLE-AS") == ("EXAMPLE-AS", "as_holder")
    assert organization(found, None) == (None, None)


async def test_a_record_without_an_organization_falls_back_too(
    db_session: AsyncSession, loaded: None
) -> None:
    address = IPv4Address("24.48.1.1")

    found = (await lookup(db_session, [address]))[address]

    assert found == Registration("arin", "CA", "EXAMPLE-CABLE-CA", None)
    assert organization(found, "EXAMPLE-CABLE-AS") == ("EXAMPLE-CABLE-AS", "as_holder")


async def test_address_that_nobody_registered_has_an_empty_registration(
    db_session: AsyncSession, loaded: None
) -> None:
    unknown = IPv4Address("100.100.100.100")

    assert await lookup(db_session, [unknown]) == {unknown: Registration()}
    assert await lookup(db_session, []) == {}


async def test_lookup_can_use_the_range_indexes(db_session: AsyncSession, loaded: None) -> None:
    """With a handful of rows the planner prefers to read the whole table. With
    that choice taken away it must be able to answer from the two GiST indexes."""
    await db_session.execute(text("SET LOCAL enable_seqscan = off"))
    plan = "\n".join(
        await db_session.scalars(
            text(f"EXPLAIN {LOOKUP.text}"), {"addresses": [ip("8.8.8.8"), ip("193.0.6.139")]}
        )
    )
    await db_session.rollback()

    assert "ix_rir_delegation_ip_range" in plan
    assert "ix_rir_inetnum_ip_range" in plan


# --- The loaders are registered with the dataset manager ---


def test_every_registry_dataset_is_in_the_list_of_the_installation(
    settings: Settings, tmp_path: Path
) -> None:
    client = httpx.AsyncClient()
    names = [d.name for d in datasets_package.build(settings, client, geolite.GeoLite(tmp_path))]

    assert names == [
        "geolite2-city",
        "geolite2-asn",
        *(f"rir-delegation-{rir}" for rir in ("ripe", "apnic", "arin", "lacnic", "afrinic")),
        *(f"rir-objects-{rir}" for rir in ("ripe", "apnic", "afrinic", "arin", "lacnic")),
    ]
