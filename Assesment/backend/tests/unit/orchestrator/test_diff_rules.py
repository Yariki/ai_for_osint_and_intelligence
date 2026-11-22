"""Checks 4.1 to 4.3 at the level of the rules: the diff of two scan views."""

import json
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any

import pytest

from ndm.orchestrator.diff import Change, HostView, ScanView, changes, dataset_of, ranges

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "diff"
PAIRS: list[dict[str, Any]] = json.loads((FIXTURES / "scan_pairs.json").read_text())["pairs"]


def view(scan_id: int, data: dict[str, Any]) -> ScanView:
    hosts = {}
    for address, host in data["hosts"].items():
        hosts[IPv4Address(address)] = HostView(
            reachable=host["reachable"],
            port_scanned=host["port_scanned"],
            open_ports=frozenset(host.get("open_ports", [])),
            ptr_names=tuple(host.get("ptr_names", [])),
            device_type=host.get("device_type"),
            sub_role=host.get("sub_role"),
            enrichment=host.get("enrichment", {}),
            unavailable=frozenset(host.get("unavailable", [])),
            dataset_versions=host.get("dataset_versions", {}),
        )
    return ScanView(
        scan_id=scan_id,
        targets=frozenset(IPv4Address(a) for a in data["targets"]),
        probed_ports=frozenset(data["probed_ports"]),
        hosts=hosts,
    )


def as_list(change: Change) -> list[Any]:
    if change.kind in ("host_appeared", "host_disappeared", "port_opened", "port_closed"):
        return [str(change.address), change.kind, change.port]
    return [
        str(change.address),
        change.kind,
        change.port,
        change.field,
        change.before,
        change.after,
        change.dataset_change,
    ]


@pytest.mark.parametrize("pair", PAIRS, ids=lambda pair: pair["name"])
def test_fixture_pairs_give_the_expected_diff(pair: dict[str, Any]) -> None:
    result = changes(view(1, pair["base"]), view(2, pair["target"]))

    expected = pair["expected"]
    assert [as_list(change) for change in result.changes] == expected["changes"]
    assert [list(span) for span in result.compared_ports] == expected["compared_ports"]
    assert [str(address) for address in result.not_comparable] == expected["not_comparable"]
    assert (result.base_scan_id, result.target_scan_id) == (1, 2)


def test_every_kind_of_change_is_covered_by_a_fixture() -> None:
    kinds = {change[1] for pair in PAIRS for change in pair["expected"]["changes"]}

    assert kinds == {
        "host_appeared",
        "host_disappeared",
        "port_opened",
        "port_closed",
        "ptr_changed",
        "classification_changed",
        "enrichment_changed",
    }


def test_summary_counts_each_kind_and_what_was_compared() -> None:
    pair = PAIRS[0]

    summary = changes(view(1, pair["base"]), view(2, pair["target"])).summary

    assert summary == {
        "host_appeared": 1,
        "host_disappeared": 1,
        "port_opened": 1,
        "port_closed": 1,
        "ptr_changed": 0,
        "classification_changed": 0,
        "enrichment_changed": 0,
        "compared_ports": 3,
        "compared_addresses": 4,
        "not_comparable": 2,
    }


def test_the_diff_is_directional() -> None:
    pair = PAIRS[0]

    backwards = changes(view(2, pair["target"]), view(1, pair["base"]))

    assert [as_list(c) for c in backwards.changes] == [
        ["198.51.100.1", "port_opened", 22],
        ["198.51.100.1", "port_closed", 443],
        ["198.51.100.2", "host_appeared", None],
        ["198.51.100.3", "host_disappeared", None],
    ]


def test_a_host_without_a_snapshot_on_one_side_has_no_enrichment_change() -> None:
    address = IPv4Address("198.51.100.1")
    before = HostView(reachable=True, port_scanned=True)
    after = HostView(reachable=True, port_scanned=True, enrichment={"country": "US"})
    base = ScanView(1, frozenset({address}), frozenset(), {address: before})
    target = ScanView(2, frozenset({address}), frozenset(), {address: after})

    assert changes(base, target).changes == []


@pytest.mark.parametrize(
    ("field", "enrichment", "dataset"),
    [
        ("city", {}, "geolite2-city"),
        ("country", {}, "geolite2-city"),
        ("asn", {}, "geolite2-asn"),
        ("as_holder", {}, "geolite2-asn"),
        ("rir", {"rir": "ripe"}, "rir-delegation-ripe"),
        ("registration_country", {"rir": "ripe"}, "rir-delegation-ripe"),
        ("network_name", {"rir": "apnic"}, "rir-objects-apnic"),
        ("organization", {"rir": "apnic", "organization_source": "registry"}, "rir-objects-apnic"),
        # An organization that is the AS holder comes from GeoLite2 ASN (FR-ENR-3).
        ("organization", {"rir": "arin", "organization_source": "as_holder"}, "geolite2-asn"),
        ("network_name", {}, None),
    ],
)
def test_each_field_is_tied_to_the_dataset_behind_it(
    field: str, enrichment: dict[str, Any], dataset: str | None
) -> None:
    assert dataset_of(field, HostView(enrichment=enrichment)) == dataset


def test_ports_are_stated_as_ranges() -> None:
    assert ranges(frozenset({1, 2, 3, 80, 443, 444, 8080})) == [
        (1, 3),
        (80, 80),
        (443, 444),
        (8080, 8080),
    ]
    assert ranges(frozenset()) == []
