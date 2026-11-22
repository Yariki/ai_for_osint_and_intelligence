"""The device-type and tag filters (TASK-055), and check 2.9 with them: the
table and the map return the same set of addresses."""

import itertools
from typing import Any

from httpx import AsyncClient

from ndm.db.models import Scan
from tests.integration.api.test_host_filters import addresses, points, table


def hosts_url(scan: Scan) -> str:
    return f"/api/v1/scans/{scan.id}/hosts"


def map_url(scan: Scan) -> str:
    return f"/api/v1/scans/{scan.id}/map-points"


async def tag(client: AsyncClient, scan: Scan, address: str, *tags: str) -> None:
    response = await client.put(
        f"/api/v1/projects/{scan.project_id}/hosts/{address}/annotations", json={"tags": list(tags)}
    )
    assert response.status_code == 200


async def test_device_type_filter_uses_the_effective_type(
    client: AsyncClient, classified_scan: Scan
) -> None:
    servers = addresses(await table(client, hosts_url(classified_scan), device_type="server"))
    assert "193.0.6.1" in servers

    # The analyst says 193.0.6.1 is a router.
    await client.put(
        f"/api/v1/projects/{classified_scan.project_id}/hosts/193.0.6.1/override",
        json={"device_type": "router"},
    )

    assert "193.0.6.1" not in addresses(
        await table(client, hosts_url(classified_scan), device_type="server")
    )
    routers = addresses(await table(client, hosts_url(classified_scan), device_type="router"))
    assert "193.0.6.1" in routers
    on_map = await points(client, map_url(classified_scan), device_type="router")
    assert addresses(on_map["located"]) | addresses(on_map["unlocated"]) == routers
    assert {p["effective_type"] for p in on_map["located"]} == {"router"}


async def test_tag_filter_selects_the_tagged_hosts(
    client: AsyncClient, classified_scan: Scan
) -> None:
    await tag(client, classified_scan, "8.8.8.8", "dns", "edge")
    await tag(client, classified_scan, "1.0.0.1", "edge")

    assert addresses(await table(client, hosts_url(classified_scan), tag="edge")) == {
        "8.8.8.8",
        "1.0.0.1",
    }
    assert addresses(await table(client, hosts_url(classified_scan), tag="dns")) == {"8.8.8.8"}
    assert await table(client, hosts_url(classified_scan), tag="dn") == []


async def test_table_and_map_agree_with_the_new_filters_in_every_combination(
    client: AsyncClient, classified_scan: Scan
) -> None:
    await tag(client, classified_scan, "8.8.8.8", "edge")
    await tag(client, classified_scan, "193.0.6.1", "edge", "web")
    await client.put(
        f"/api/v1/projects/{classified_scan.project_id}/hosts/8.8.8.1/override",
        json={"device_type": "camera"},
    )
    options: dict[str, list[Any]] = {
        "device_type": [None, "server", "camera", "unknown", "dns_server"],
        "tag": [None, "edge", "web", "none"],
        "open_port": [None, 443],
        "country": [None, "US"],
        "reachable": [None, "true"],
    }
    selective = 0
    for values in itertools.product(*options.values()):
        params = {
            name: value for name, value in zip(options, values, strict=True) if value is not None
        }
        rows = await table(client, hosts_url(classified_scan), **params)
        on_map = await points(client, map_url(classified_scan), **params)
        mapped = [*on_map["located"], *on_map["unlocated"]]
        assert addresses(mapped) == addresses(rows), params
        assert len(mapped) == len(rows), params
        selective += 0 < len(rows) < 80
    assert selective > 20


async def test_sorting_by_device_type(client: AsyncClient, classified_scan: Scan) -> None:
    rows = await table(client, hosts_url(classified_scan), page_size=9, sort="device_type")

    types = [row["effective_type"] for row in rows]
    known = [value for value in types if value is not None]
    assert known == sorted(known)
    assert types[len(known) :] == [None] * (len(types) - len(known))


async def test_invalid_filter_values_are_refused(
    client: AsyncClient, classified_scan: Scan
) -> None:
    for params in ({"device_type": "Not A Type"}, {"tag": "x" * 65}):
        assert (await client.get(hosts_url(classified_scan), params=params)).status_code == 422
