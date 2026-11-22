"""Chunk planning: cutting a scan into units of work (architecture section 6.2).

Pure functions. A chunk is the unit of work, of checkpointing and of recovery:
after an interruption at most one chunk is probed again, so a chunk is kept to
about 10,000 probes, which is about 10 seconds at the default rate (R8).

A planning error would lose probes or repeat them, so the one property that
matters is: every host and port pair of a scan is in exactly one chunk.
"""

import random
from collections.abc import Sequence
from ipaddress import IPv4Address

from pydantic import BaseModel

from ndm.core.messages import ChunkPhase, Discovery

PROBES_PER_CHUNK = 10_000
DEFAULT_HOSTS_IN_PARALLEL = 64


class ScanParameters(BaseModel):
    """The parameter set of a scan, as stored on the scan row (FR-SCN-8).

    Written by the API when a scan is created, after it has checked the values
    against the operator ceilings; read by the orchestrator.
    """

    discovery: Discovery | None
    """The discovery methods; None when discovery is skipped."""
    scan_all_targets: bool = False
    """Port-scan every target, not only the hosts that answered discovery (FR-CFG-3)."""
    pps: int
    timeout_ms: int
    retries: int
    hosts_in_parallel: int = DEFAULT_HOSTS_IN_PARALLEL


class ChunkSpec(BaseModel):
    """What a chunk probes. Stored on the chunk row and copied into the chunk message."""

    chunk_seq: int
    phase: ChunkPhase
    targets: list[IPv4Address]
    ports: list[int] = []
    discovery: Discovery | None = None

    @property
    def probes(self) -> int:
        """Probes of a port-scan chunk, before retries."""
        return len(self.targets) * len(self.ports)


def plan_discovery(targets: Sequence[IPv4Address], discovery: Discovery, *, seed: int) -> ChunkSpec:
    """The discovery chunk: all targets, with the selected discovery methods.

    It is always chunk 0. Discovery sends a handful of probes per target, so
    256 targets fit in one chunk.
    """
    shuffled = list(targets)
    random.Random(seed).shuffle(shuffled)
    return ChunkSpec(chunk_seq=0, phase=ChunkPhase.DISCOVERY, targets=shuffled, discovery=discovery)


def plan_port_scan(
    hosts: Sequence[IPv4Address],
    ports: Sequence[int],
    *,
    seed: int,
    hosts_in_parallel: int = DEFAULT_HOSTS_IN_PARALLEL,
    first_seq: int = 1,
) -> list[ChunkSpec]:
    """The port-scan chunks: each is a group of hosts multiplied by a slice of ports.

    - Hosts and ports are shuffled with the scan's seed before they are cut
      (FR-SCN-10), so neither the targets nor the ports are probed in order.
    - A group holds ``hosts_in_parallel`` hosts (FR-CFG-6).
    - A slice is as long as it takes to give the chunk about 10,000 probes.
    """
    if hosts_in_parallel < 1:
        raise ValueError("hosts_in_parallel must be at least 1")
    order = random.Random(seed)
    shuffled_hosts = list(hosts)
    shuffled_ports = list(ports)
    order.shuffle(shuffled_hosts)
    order.shuffle(shuffled_ports)

    chunks: list[ChunkSpec] = []
    for start in range(0, len(shuffled_hosts), hosts_in_parallel):
        group = shuffled_hosts[start : start + hosts_in_parallel]
        # Sized for this group: the last group may be smaller and gets longer slices.
        slice_size = max(1, round(PROBES_PER_CHUNK / len(group)))
        for offset in range(0, len(shuffled_ports), slice_size):
            chunks.append(
                ChunkSpec(
                    chunk_seq=first_seq + len(chunks),
                    phase=ChunkPhase.PORT_SCAN,
                    targets=group,
                    ports=shuffled_ports[offset : offset + slice_size],
                )
            )
    return chunks
