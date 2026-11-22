"""Check 2.1, parsing: the fixture file of each registry and format is read correctly."""

from datetime import date
from ipaddress import IPv4Address
from pathlib import Path

import pytest

from ndm.orchestrator.datasets import rir_delegation, rir_objects
from ndm.orchestrator.datasets.manager import DatasetError
from ndm.orchestrator.datasets.rir_delegation import Delegation
from ndm.orchestrator.datasets.rir_lookup import Registration, organization
from ndm.orchestrator.datasets.rir_objects import Inetnum

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "rir"


def ip(address: str) -> int:
    return int(IPv4Address(address))


def net(first: str, last: str, name: str | None, org: str | None, country: str | None) -> Inetnum:
    return Inetnum(ip(first), ip(last), name, org, country)


# --- Delegation statistics: all five registries ---


def delegations(registry: str) -> tuple[str, list[Delegation]]:
    text = (FIXTURES / f"delegated-{registry}-extended-latest").read_text()
    return rir_delegation.parse(text, registry)


def test_ripe_statistics() -> None:
    version, records = delegations("ripencc")

    # The version is the last day the file covers. Format version 2.
    assert version == "2026-11-15"
    assert records == [
        Delegation(ip("193.0.0.0"), ip("193.0.7.255"), "NL", date(1993, 9, 1), "allocated"),
        # 768 addresses: the value is a count, and not a power of two.
        Delegation(ip("193.0.8.0"), ip("193.0.10.255"), "DE", date(1995, 3, 14), "assigned"),
        Delegation(ip("194.0.0.0"), ip("194.0.255.255"), "EU", date(1996, 1, 1), "allocated"),
        Delegation(ip("1.178.112.0"), ip("1.178.127.255"), "PS", date(2007, 11, 26), "allocated"),
    ]


def test_apnic_statistics_with_comment_lines() -> None:
    version, records = delegations("apnic")

    assert version == "2026-11-16"
    assert records == [
        Delegation(ip("1.0.0.0"), ip("1.0.0.255"), "AU", date(2011, 8, 11), "assigned"),
        Delegation(ip("1.0.1.0"), ip("1.0.1.255"), "CN", date(2011, 4, 14), "allocated"),
    ]


def test_arin_statistics() -> None:
    version, records = delegations("arin")

    assert version == "2026-11-16"
    assert records == [
        Delegation(ip("8.0.0.0"), ip("8.255.255.255"), "US", date(1992, 12, 1), "allocated"),
        Delegation(ip("24.48.0.0"), ip("24.49.255.255"), "CA", date(1998, 9, 22), "allocated"),
    ]


def test_lacnic_statistics_with_a_record_that_lacks_its_last_field() -> None:
    version, records = delegations("lacnic")

    assert version == "2026-11-13"
    assert [(r.first, r.country, r.status) for r in records] == [
        (ip("200.3.12.0"), "UY", "assigned"),
        (ip("200.160.0.0"), "BR", "allocated"),
    ]


def test_afrinic_statistics() -> None:
    version, records = delegations("afrinic")

    assert version == "2026-11-16"
    assert records == [
        Delegation(ip("41.0.0.0"), ip("41.31.255.255"), "ZA", date(2007, 11, 26), "allocated"),
        Delegation(ip("196.200.0.0"), ip("196.200.4.255"), "KE", date(2005, 5, 18), "assigned"),
    ]


@pytest.mark.parametrize("registry", ["ripencc", "apnic", "arin", "lacnic", "afrinic"])
def test_only_registered_ipv4_blocks_are_read(registry: str) -> None:
    _, records = delegations(registry)

    # No reserved or available block, no AS number, no IPv6 prefix.
    assert {record.status for record in records} <= {"allocated", "assigned"}
    assert all(record.country not in ("", "ZZ") for record in records)
    assert all(0 <= record.first <= record.last <= 0xFFFFFFFF for record in records)


def test_file_of_another_registry_is_refused() -> None:
    text = (FIXTURES / "delegated-apnic-extended-latest").read_text()

    with pytest.raises(DatasetError, match="not a delegation statistics file of arin"):
        rir_delegation.parse(text, "arin")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "<html>maintenance</html>",
        "2.3|apnic|20261117|0||20261116|+1000\napnic|*|ipv4|*|0|summary\n",
        "2.3|apnic|20261117|1||20261116|+1000\napnic|AU|ipv4|1.0.0.999|256|20110811|assigned|A\n",
        "2.3|apnic|20261117|1||20261116|+1000\napnic|AU|ipv4|1.0.0.0|many|20110811|assigned|A\n",
        "2.3|apnic|20261117|1||20261116|+1000\napnic|AU|ipv4|255.255.255.0|512|20110811|assigned|A\n",
    ],
)
def test_file_that_is_not_what_it_should_be_is_refused(text: str) -> None:
    with pytest.raises(DatasetError):
        rir_delegation.parse(text, "apnic")


# --- Detailed objects: RPSL dumps ---


def rpsl(*names: str) -> list[Inetnum]:
    return list(rir_objects.rpsl_files([FIXTURES / name for name in names]))


def test_ripe_objects() -> None:
    records = rpsl("ripe.db.organisation", "ripe.db.inetnum")

    assert records == [
        # The placeholder for the whole address space. Its country has a comment.
        net("0.0.0.0", "255.255.255.255", "IANA-BLK", "The whole IPv4 address space", "EU"),
        # The organization comes from the organisation object the record refers to.
        net("193.0.0.0", "193.0.7.255", "NL-EXAMPLE-19930901", "Example Networks B.V.", "NL"),
        # The reference is matched whatever its case; the country is upper-cased.
        net("193.0.6.0", "193.0.6.255", "EXAMPLE-UNIVERSITY", "University of Example", "NL"),
        # Without a reference, the first description line.
        net("193.0.8.0", "193.0.10.255", "DE-CUSTOMER-NET", "Beispiel GmbH", "DE"),
        net(
            "8.0.0.0",
            "8.255.255.255",
            "NON-RIPE-NCC-MANAGED-ADDRESS-BLOCK",
            "IPv4 address block not managed by the RIPE NCC",
            "EU",
        ),
        # The object whose range cannot be read is left out.
    ]


def test_apnic_objects() -> None:
    assert rpsl("apnic.db.organisation", "apnic.db.inetnum") == [
        net("1.0.0.0", "1.0.0.255", "EXAMPLE-LIBRARY-AU", "Example Library of Australia", "AU"),
        net("1.0.1.0", "1.0.1.255", "EXAMPLE-CN-NET", "Example Telecom", "CN"),
    ]


def test_afrinic_objects_from_one_file_with_every_object_type() -> None:
    assert rpsl("afrinic.db") == [
        # Its organisation object comes later in the file.
        net("41.0.0.0", "41.31.255.255", "EXAMPLE-ZA-MOBILE", "Example Mobile (Pty) Ltd", "ZA"),
        net("196.200.0.0", "196.200.4.255", "EXAMPLE-KE-EDU", "Example Education Network", "KE"),
    ]


def test_lacnic_objects_with_shortened_prefixes() -> None:
    assert rpsl("lacnic_db.txt") == [
        net("200.3.12.0", "200.3.15.255", None, "Ejemplo - Registro de Direcciones", "UY"),
        net("200.160.0.0", "200.160.31.255", None, "Exemplo Telecomunicacoes S.A.", "BR"),
        net("200.3.13.0", "200.3.13.255", None, "Cliente de Ejemplo", "UY"),
    ]


def test_arin_objects() -> None:
    assert list(rir_objects.arin_file(FIXTURES / "arin_db.xml")) == [
        # Addresses with leading zeros are read.
        net("8.0.0.0", "8.255.255.255", "EXAMPLE-BACKBONE", "Example Backbone, Inc.", None),
        # A network with two blocks gives two records.
        net("8.8.8.0", "8.8.8.255", "EXAMPLE-DNS", "Example DNS LLC", None),
        net("8.8.4.0", "8.8.4.255", "EXAMPLE-DNS", "Example DNS LLC", None),
        # No organization handle: the organization is not known. The IPv6 network is skipped.
        net("24.48.0.0", "24.49.255.255", "EXAMPLE-CABLE-CA", None, None),
    ]


def test_arin_file_that_is_not_xml_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "arin_db.xml"
    broken.write_text("<bulkwhois><net><name>CUT OFF")

    with pytest.raises(DatasetError, match="not a readable XML file"):
        list(rir_objects.arin_file(broken))


def test_no_contact_attribute_is_read() -> None:
    """Only range, network name, organization and country leave the parser."""
    for record in rpsl("ripe.db.organisation", "ripe.db.inetnum", "apnic.db.inetnum", "afrinic.db"):
        text = repr(record)
        assert "@" not in text and "DUMY" not in text and "***" not in text


def test_rpsl_continuation_lines_and_comments() -> None:
    lines = [
        "% a comment",
        "inetnum: 192.0.2.0 - 192.0.2.255",
        "descr:   first line",
        "         goes on",
        "+        and on",
        "\tand ends",
        "descr:   second value",
        "",
        "",
        "inetnum:192.0.2.0/25",
    ]

    assert list(rir_objects.rpsl_objects(lines)) == [
        (
            "inetnum",
            {
                "inetnum": ["192.0.2.0 - 192.0.2.255"],
                "descr": ["first line goes on and on and ends", "second value"],
            },
        ),
        ("inetnum", {"inetnum": ["192.0.2.0/25"]}),
    ]


@pytest.mark.parametrize(
    ("text", "first", "last"),
    [
        ("192.0.2.0 - 192.0.2.255", "192.0.2.0", "192.0.2.255"),
        ("192.0.2.7-192.0.2.7", "192.0.2.7", "192.0.2.7"),
        ("192.0.2.0/24", "192.0.2.0", "192.0.2.255"),
        ("200.3.12/22", "200.3.12.0", "200.3.15.255"),
        ("200.160/19", "200.160.0.0", "200.160.31.255"),
        ("10/8", "10.0.0.0", "10.255.255.255"),
        ("192.000.002.000 - 192.000.002.255", "192.0.2.0", "192.0.2.255"),
        ("0.0.0.0/0", "0.0.0.0", "255.255.255.255"),
    ],
)
def test_ranges_are_read_in_every_form(text: str, first: str, last: str) -> None:
    assert rir_objects.parse_range(text) == (ip(first), ip(last))


@pytest.mark.parametrize(
    "text",
    ["", "192.0.2.255 - 192.0.2.0", "192.0.2.0/33", "192.0.2/x", "2001:db8::/32", "1.2.3.4.5/8"],
)
def test_range_that_cannot_be_read_is_an_error(text: str) -> None:
    with pytest.raises(ValueError):
        rir_objects.parse_range(text)


# --- The organization and its source (FR-ENR-3) ---


def test_organization_comes_from_the_registry_when_it_has_one() -> None:
    registration = Registration("ripe", "NL", "NL-EXAMPLE", "Example Networks B.V.")

    assert organization(registration, "EXAMPLE-AS") == ("Example Networks B.V.", "registry")


def test_without_a_record_the_organization_falls_back_to_the_as_holder() -> None:
    assert organization(Registration("arin", "US"), "EXAMPLE-AS") == ("EXAMPLE-AS", "as_holder")
    assert organization(Registration(), "EXAMPLE-AS") == ("EXAMPLE-AS", "as_holder")


def test_without_either_there_is_no_organization_and_no_source() -> None:
    assert organization(Registration("arin", "US"), None) == (None, None)
