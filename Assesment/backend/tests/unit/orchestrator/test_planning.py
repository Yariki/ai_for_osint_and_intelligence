"""Check 1B.1: chunk planning."""

from collections import Counter
from ipaddress import IPv4Address

import pytest

from ndm.core.messages import ChunkPhase, Discovery
from ndm.core.ports import PortProfile, profile_ports
from ndm.orchestrator.planning import ChunkSpec, plan_discovery, plan_port_scan


def addresses(count: int) -> list[IPv4Address]:
    return [IPv4Address("8.8.0.0") + offset for offset in range(count)]


def pairs(chunks: list[ChunkSpec]) -> Counter[tuple[IPv4Address, int]]:
    return Counter(
        (host, port) for chunk in chunks for host in chunk.targets for port in chunk.ports
    )


SCANS = [
    # (hosts, ports, hosts probed in parallel)
    (256, list(profile_ports(PortProfile.TOP_1000)), 64),
    (256, list(profile_ports(PortProfile.TOP_100)), 64),
    (200, list(profile_ports(PortProfile.TOP_1000)), 64),  # the last group is smaller
    (1, list(profile_ports(PortProfile.TOP_1000)), 64),
    (7, [22, 80, 443], 64),
    (256, [443], 64),
    (100, list(range(1, 2001)), 1),
    (256, list(range(1, 501)), 256),
    (65, list(range(1, 1001)), 64),  # one host left over
    (3, list(profile_ports(PortProfile.FULL)), 64),
]


# --- Every pair in exactly one chunk ---


@pytest.mark.parametrize(("hosts", "ports", "parallel"), SCANS)
def test_every_host_and_port_pair_is_in_exactly_one_chunk(
    hosts: int, ports: list[int], parallel: int
) -> None:
    targets = addresses(hosts)

    chunks = plan_port_scan(targets, ports, seed=7, hosts_in_parallel=parallel)

    planned = pairs(chunks)
    assert set(planned) == {(host, port) for host in targets for port in ports}
    assert set(planned.values()) == {1}
    assert sum(chunk.probes for chunk in chunks) == hosts * len(ports)


def test_full_range_scan_of_256_hosts() -> None:
    """The largest scan: 16,776,960 probes in about 1,700 chunks (architecture 6.2)."""
    targets = addresses(256)
    ports = list(profile_ports(PortProfile.FULL))

    chunks = plan_port_scan(targets, ports, seed=1)

    assert sum(chunk.probes for chunk in chunks) == 16_776_960
    assert 1_600 <= len(chunks) <= 1_800
    # Counted per group, because 16.8 million pairs are too many to hold in a test.
    by_group: dict[tuple[IPv4Address, ...], list[int]] = {}
    for chunk in chunks:
        by_group.setdefault(tuple(chunk.targets), []).extend(chunk.ports)
    assert sorted(host for group in by_group for host in group) == targets
    for group_ports in by_group.values():
        assert sorted(group_ports) == ports


# --- About 10,000 probes ---


@pytest.mark.parametrize(("hosts", "ports", "parallel"), SCANS)
def test_no_chunk_is_much_larger_than_10000_probes(
    hosts: int, ports: list[int], parallel: int
) -> None:
    chunks = plan_port_scan(addresses(hosts), ports, seed=7, hosts_in_parallel=parallel)

    assert max(chunk.probes for chunk in chunks) <= 10_100


def test_full_chunks_hold_about_10000_probes() -> None:
    chunks = plan_port_scan(addresses(256), list(profile_ports(PortProfile.FULL)), seed=3)

    sizes = Counter(chunk.probes for chunk in chunks)
    typical, count = sizes.most_common(1)[0]
    assert 9_900 <= typical <= 10_100
    # Only the last slice of each of the four groups is shorter.
    assert count >= len(chunks) - 4


def test_group_size_is_the_hosts_in_parallel_parameter() -> None:
    chunks = plan_port_scan(addresses(200), list(range(1, 1001)), seed=5, hosts_in_parallel=64)

    assert sorted({len(chunk.targets) for chunk in chunks}) == [8, 64]

    with pytest.raises(ValueError):
        plan_port_scan(addresses(4), [80], seed=5, hosts_in_parallel=0)


# --- Seed and order ---


def test_same_seed_gives_the_same_plan() -> None:
    targets, ports = addresses(256), list(profile_ports(PortProfile.TOP_1000))

    assert plan_port_scan(targets, ports, seed=91823) == plan_port_scan(targets, ports, seed=91823)
    assert plan_port_scan(targets, ports, seed=91823) != plan_port_scan(targets, ports, seed=91824)


def test_order_is_not_sorted() -> None:
    targets, ports = addresses(256), list(profile_ports(PortProfile.TOP_1000))

    chunks = plan_port_scan(targets, ports, seed=91823)

    host_order = [host for chunk in chunks[::7] for host in chunk.targets]
    port_order = [port for chunk in chunks[:7] for port in chunk.ports]
    assert host_order != sorted(host_order)
    assert port_order != sorted(port_order)
    # The first chunk neither holds the lowest addresses nor the lowest ports.
    assert chunks[0].targets != targets[:64]
    assert chunks[0].ports != ports[: len(chunks[0].ports)]


def test_chunk_numbers_are_consecutive_and_follow_the_discovery_chunk() -> None:
    chunks = plan_port_scan(addresses(256), list(range(1, 1001)), seed=2)

    assert [chunk.chunk_seq for chunk in chunks] == list(range(1, len(chunks) + 1))
    assert {chunk.phase for chunk in chunks} == {ChunkPhase.PORT_SCAN}

    later = plan_port_scan(addresses(4), [80], seed=2, first_seq=10)
    assert [chunk.chunk_seq for chunk in later] == [10]


def test_nothing_to_scan_gives_no_chunk() -> None:
    assert plan_port_scan([], [80, 443], seed=1) == []
    assert plan_port_scan(addresses(4), [], seed=1) == []


# --- Discovery chunk ---


def test_discovery_chunk_holds_all_targets_with_the_selected_methods() -> None:
    targets = addresses(256)
    methods = Discovery(icmp_echo=True, tcp_ports=[80, 443])

    chunk = plan_discovery(targets, methods, seed=91823)

    assert (chunk.chunk_seq, chunk.phase) == (0, ChunkPhase.DISCOVERY)
    assert sorted(chunk.targets) == targets
    assert chunk.targets != targets
    assert chunk.discovery == methods
    assert chunk.ports == []
    assert plan_discovery(targets, methods, seed=91823) == chunk


def test_a_chunk_specification_survives_storage_as_json() -> None:
    chunk = plan_port_scan(addresses(3), [22, 80], seed=4)[0]

    assert ChunkSpec.model_validate(chunk.model_dump(mode="json")) == chunk
