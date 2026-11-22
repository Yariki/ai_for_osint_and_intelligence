"""The lab resolver's zone file, checked against the lab manifest.

The zone is what check 2.7 expects to find stored with a scan of the lab, so it
must name lab hosts only, and it must leave something to say "no name" about.
"""

import re
from ipaddress import IPv4Address, IPv4Network

import yaml

from tests.tools.lab_zone import LAB, load_zone, ptr_records


def manifest_hosts() -> tuple[IPv4Network, set[IPv4Address]]:
    manifest = yaml.safe_load((LAB / "manifest.yaml").read_text())
    hosts = {IPv4Address(host["address"]) for host in manifest["hosts"]}
    return IPv4Network(manifest["scanned_network"]), hosts


def test_zone_is_the_reverse_zone_of_the_scanned_network() -> None:
    network, _ = manifest_hosts()
    zone = load_zone()

    octets = str(network.network_address).split(".")[:3]
    assert zone.origin is not None
    assert zone.origin.to_text() == ".".join(reversed(octets)) + ".in-addr.arpa."
    assert all(address in network for address in ptr_records())


def test_every_name_belongs_to_a_host_of_the_manifest() -> None:
    _, hosts = manifest_hosts()

    assert set(ptr_records()) <= hosts


def test_zone_has_a_host_with_two_names_and_hosts_with_none() -> None:
    _, hosts = manifest_hosts()
    records = ptr_records()

    assert records[IPv4Address("10.77.3.10")] == ["web.lab.test.", "www.lab.test."]
    assert hosts - set(records) == {IPv4Address("10.77.3.30"), IPv4Address("10.77.3.40")}


def test_the_resolver_serves_the_zone_and_forwards_nothing() -> None:
    corefile = (LAB / "resolver" / "Corefile").read_text()
    directives = [line.strip() for line in corefile.splitlines() if not line.startswith("#")]

    assert "3.77.10.in-addr.arpa:53 {" in directives
    assert "file /etc/coredns/lab.zone" in directives
    assert not [line for line in directives if re.match(r"(forward|proxy)\b", line)]


def test_the_stack_is_pointed_at_the_lab_resolver() -> None:
    compose = yaml.safe_load((LAB / "compose.lab.yaml").read_text())
    published = compose["services"]["resolver"]["ports"]
    settings = (LAB / "lab.env").read_text()

    assert published == ["10.77.0.1:1053:53/udp", "10.77.0.1:1053:53/tcp"]
    assert "\nNDM_PTR_RESOLVER=10.77.0.1:1053\n" in settings
