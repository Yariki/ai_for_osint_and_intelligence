"""Reference datasets for tests, loaded the way the product loads them.

GeoLite2 databases are built from the made-up reference data of the replay
fixture and served by the stand-in for MaxMind; the registry data comes from
the registry fixtures. Both go through the real dataset manager, so the
``reference_dataset`` rows, the files and the registry tables are exactly what
an installation has after its first update.
"""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ndm.core.settings import Settings
from ndm.orchestrator.datasets import geolite, rir_delegation, rir_objects
from ndm.orchestrator.datasets.geolite import GeoLite
from ndm.orchestrator.datasets.manager import Dataset, DatasetManager
from tests.tools.mmdb import ACCOUNT_ID, LICENSE_KEY, FakeMaxMind, archive, asn, city
from tests.tools.registries import FakeRegistries, supply_operator_files

LOADED_AT = datetime(2026, 11, 14, 6, tzinfo=UTC)
BUILT = date(2026, 11, 13)

ENVIRONMENT = {
    "NDM_MAXMIND_ACCOUNT_ID": ACCOUNT_ID,
    "NDM_MAXMIND_LICENSE_KEY": LICENSE_KEY,
    "NDM_RIR_OBJECT_REGISTRIES": "ripe,apnic,afrinic",
}


def city_records(reference: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """GeoLite2 City records for the networks of the reference data that have a location."""
    return {
        network: city(
            data["city"],
            data["country"],
            data["latitude"],
            data["longitude"],
            data["accuracy_radius_km"],
        )
        for network, data in reference.items()
        if "latitude" in data
    }


def asn_records(reference: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {network: asn(data["asn"], data["as_holder"]) for network, data in reference.items()}


def publish_geolite(
    maxmind: FakeMaxMind, reference: dict[str, dict[str, Any]], built: date = BUILT
) -> None:
    maxmind.publish("GeoLite2-City", archive("GeoLite2-City", city_records(reference), built))
    maxmind.publish("GeoLite2-ASN", archive("GeoLite2-ASN", asn_records(reference), built))


async def load(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    readers: GeoLite,
    reference: dict[str, dict[str, Any]],
    *,
    only: tuple[str, ...] | None = None,
    built: date = BUILT,
    now: datetime = LOADED_AT,
) -> list[str]:
    """Load every dataset, or only those whose names start with one of ``only``.

    ``settings`` must carry ``ENVIRONMENT`` and the two paths. Returns the
    names of the datasets that were loaded.
    """
    maxmind = FakeMaxMind()
    publish_geolite(maxmind, reference, built)
    registries = FakeRegistries()
    if settings.rir_files_path is not None:
        supply_operator_files(settings.rir_files_path)
    async with maxmind.client() as from_maxmind, registries.client() as from_registries:
        wanted: list[Dataset] = [
            dataset
            for dataset in (
                *geolite.datasets(settings, from_maxmind, readers),
                *rir_delegation.datasets(settings, from_registries),
                *rir_objects.datasets(settings, from_registries),
            )
            if only is None or dataset.name.startswith(only)
        ]
        return await DatasetManager(sessions, wanted).run_due(now)
