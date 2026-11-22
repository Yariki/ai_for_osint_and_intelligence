"""Reference datasets: GeoLite2 and the registries' data (architecture 11.1).

``build`` lists the datasets of this installation. The dataset manager updates
whatever is in the list; a task that adds a loader adds it here.
"""

import httpx

from ndm.core.settings import Settings
from ndm.orchestrator.datasets import geolite, rir_delegation, rir_objects
from ndm.orchestrator.datasets.manager import Dataset


def build(settings: Settings, client: httpx.AsyncClient, readers: geolite.GeoLite) -> list[Dataset]:
    return [
        *geolite.datasets(settings, client, readers),
        *rir_delegation.datasets(settings, client),
        *rir_objects.datasets(settings, client),
    ]
